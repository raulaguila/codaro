import json
import sys
import threading

import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent
from codaro.edits import EditManager
from codaro.index import CodeIndex
from codaro.policies import ApprovalPolicy, Mode
from codaro.repository import Repository
from codaro.tasks import TaskStore


def outputs(model):
    return [json.loads(item["content"]) for item in model.requests[-1][0] if item["role"] == "tool"]


@pytest.mark.parametrize("mode", ["ask", "plan"])
def test_read_modes_block_mutations_even_when_model_requests_them(tmp_path, mode):
    model = FakeModel(
        [
            call(
                "apply_changes",
                {
                    "reason": "criar",
                    "operations": [{"kind": "create", "path": "x.py", "content": "x = 1\n"}],
                },
            ),
            call("run_command", {"argv": [sys.executable, "-c", "print('no')"]}),
            {"content": "Sem mudanças."},
        ]
    )
    agent = Agent(
        Repository(tmp_path),
        model,
        mode=mode,
        approve_edit=lambda *_: pytest.fail("review must not run"),
        approve_command=lambda *_: pytest.fail("command must not run"),
    )
    agent.ask("Explique como fazer.")
    assert not (tmp_path / "x.py").exists()
    assert all("error" in result for result in outputs(model))
    for _, definitions in model.requests:
        assert not {"apply_changes", "propose_edit", "run_command"}.intersection(
            tool["function"]["name"] for tool in definitions or []
        )


def test_plan_to_execution_preserves_objective_and_does_not_grant_permission(tmp_path):
    steps = [{"title": "Implementar e testar", "state": "todo"}]
    model = FakeModel(
        [
            call("update_plan", {"steps": steps, "criteria": ["Teste aprovado"]}),
            call("finish_task", {"status": "planned", "summary": "Plano disponível"}),
            {"content": "Plano pronto."},
            {"content": "Preciso de informações adicionais."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, mode="plan")
    agent.ask("Adicionar validação")
    identifier = agent.tasks.current()["id"]
    agent.set_mode("execute")
    agent.ask("Continue o plano")
    task = agent.tasks.current()
    assert task["id"] == identifier
    assert task["objective"] == "Adicionar validação"
    assert task["plan"] == steps
    assert agent.policy.kind == "action"


def test_full_task_edits_creates_fails_corrects_and_reexecutes_exact_test_command(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    argv = [sys.executable, "checks.py"]
    model = FakeModel(
        [
            call(
                "update_plan",
                {
                    "steps": [{"title": "Corrigir soma e testar", "state": "doing"}],
                    "criteria": ["2 + 3 retorna 5"],
                },
            ),
            call("read_lines", {"path": "calc.py", "start": 1, "end": 2}),
            call(
                "apply_changes",
                {
                    "reason": "Implementar soma e verificação",
                    "operations": [
                        {
                            "kind": "edit",
                            "path": "calc.py",
                            "old_text": "return a - b",
                            "new_text": "return a",
                        },
                        {
                            "kind": "create",
                            "path": "checks.py",
                            "content": "from calc import add\nassert add(2, 3) == 5\n",
                        },
                    ],
                },
            ),
            call("run_command", {"argv": argv, "purpose": "validation"}),
            call("read_lines", {"path": "calc.py", "start": 1, "end": 2}),
            call(
                "propose_edit",
                {
                    "path": "calc.py",
                    "old_text": "return a",
                    "new_text": "return a + b",
                    "reason": "Corrigir falha do teste",
                },
            ),
            call("run_command", {"argv": argv, "purpose": "validation"}),
            call(
                "update_plan",
                {
                    "steps": [{"title": "Corrigir soma e testar", "state": "done"}],
                    "criteria": ["2 + 3 retorna 5"],
                },
            ),
            call(
                "finish_task",
                {"status": "completed", "summary": "Soma implementada e teste aprovado"},
            ),
            {"content": "Soma corrigida e verificada."},
        ]
    )
    reviews, commands = [], []

    def approve_edits(proposals, _):
        reviews.append([item.path for item in proposals])
        assert all(item.state == "pending" for item in proposals)
        return True

    def approve_command(command, *_):
        commands.append(command)
        return True

    agent = Agent(
        Repository(tmp_path),
        model,
        mode="execute",
        approve_edit=approve_edits,
        approve_command=approve_command,
    )
    answer = agent.ask("Corrija a soma e crie uma verificação")
    assert answer == "Soma corrigida e verificada."
    assert (tmp_path / "calc.py").read_text().endswith("return a + b\n")
    assert commands == [argv, argv]
    assert reviews == [["calc.py", "checks.py"], ["calc.py"]]
    assert agent.tasks.current()["state"] == "completed"
    assert [item["exit_code"] for item in agent.tasks.current()["validations"]] == [1, 0]
    assert not agent.edits.pending
    assert len(agent.edits.checkpoints.list()) == 3
    results = outputs(model)
    assert any(result.get("exit_code") == 1 for result in results)
    assert any(result.get("exit_code") == 0 for result in results)
    assert not any(result.get("reused_result") for result in results)


def test_rejected_set_and_cancelled_review_never_write(tmp_path):
    repository = Repository(tmp_path)
    agent = Agent(repository, FakeModel([]), mode="execute", approve_edit=lambda *_: False)
    agent.tasks.start("Criar módulo")
    with CodeIndex(repository) as index:
        result = agent.execute(
            index,
            "apply_changes",
            {
                "reason": "Criar módulo",
                "operations": [{"kind": "create", "path": "new.py", "content": "x=1\n"}],
            },
        )
    assert result["state"] == "rejected"
    assert not (tmp_path / "new.py").exists()
    assert not agent.edits.pending


def test_task_scopes_are_exact_session_local_and_bound_to_task(tmp_path):
    store = TaskStore(tmp_path)
    task = store.start("Implementar")
    policy = ApprovalPolicy()
    policy.grant(task["id"], ["src", "test.py"], [["pytest", "-q"]])
    assert policy.permits_paths(task["id"], ["src/new.py", "test.py"])
    assert not policy.permits_paths(task["id"], ["src2/new.py"])
    assert not policy.permits_paths("other", ["src/new.py"])
    assert not policy.permits_command(task["id"], ["pytest", "-q", "--extra"])
    assert not ApprovalPolicy().permits_command(task["id"], ["pytest", "-q"])
    with pytest.raises(ValueError):
        policy.grant(task["id"], ["../outside"], [])


def test_creation_ignores_links_and_exclusive_destination(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored/\n")
    repository = Repository(tmp_path)
    edits = EditManager(repository)
    with pytest.raises(ValueError, match="ignore"):
        edits.prepare_operations(
            [{"kind": "create", "path": "ignored/x.py", "content": "x=1"}], "x"
        )
    proposals = edits.prepare_operations(
        [{"kind": "create", "path": "nested/x.py", "content": "x=1"}], "x"
    )
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/x.py").write_text("external")
    with pytest.raises(ValueError):
        edits.apply(proposals[0].id)
    assert (tmp_path / "nested/x.py").read_text() == "external"


def test_create_delete_rename_and_undo_preserve_bytes_and_permissions(tmp_path):
    repository = Repository(tmp_path)
    edits = EditManager(repository)
    create = edits.prepare_operations(
        [{"kind": "create", "path": "nested/x.py", "content": "x=1\r\n"}], "Criar"
    )[0]
    edits.apply(create.id)
    edits.propose_undo(create.checkpoint_id)
    edits.apply(edits.pending[0].id)
    assert not (tmp_path / "nested/x.py").exists()
    source = tmp_path / "run.py"
    source.write_bytes(b"\xef\xbb\xbfx=1\r\n")
    source.chmod(0o755)
    edits.observe(repository.read_lines("run.py", 1, 1), source.read_bytes())
    rename = edits.prepare_operations(
        [{"kind": "rename", "path": "run.py", "destination": "nested/run.py"}], "Mover"
    )
    for proposal in rename:
        edits.apply(proposal.id)
    assert not source.exists()
    assert (tmp_path / "nested/run.py").read_bytes() == b"\xef\xbb\xbfx=1\r\n"
    assert (tmp_path / "nested/run.py").stat().st_mode & 0o777 == 0o755
    undo = edits.propose_undo(rename[-1].checkpoint_id)
    edits.apply(undo.id)
    assert source.read_bytes() == b"\xef\xbb\xbfx=1\r\n"
    assert source.stat().st_mode & 0o777 == 0o755


def test_partial_set_failure_is_explicit_and_preserves_external_file(tmp_path):
    repository = Repository(tmp_path)

    def approve(proposals, _):
        (tmp_path / "second.py").write_text("external")
        return True

    agent = Agent(repository, FakeModel([]), mode="execute", approve_edit=approve)
    agent.tasks.start("Criar dois arquivos")
    with CodeIndex(repository) as index:
        result = agent.execute(
            index,
            "apply_changes",
            {
                "reason": "Criar",
                "operations": [
                    {"kind": "create", "path": "first.py", "content": "x=1"},
                    {"kind": "create", "path": "second.py", "content": "x=2"},
                ],
            },
        )
    assert result["state"] == "partial"
    assert (tmp_path / "first.py").read_text() == "x=1"
    assert (tmp_path / "second.py").read_text() == "external"
    assert not agent.edits.pending
    assert agent.tasks.current()["state"] == "blocked"


def test_no_validation_cannot_be_marked_completed(tmp_path):
    agent = Agent(Repository(tmp_path), FakeModel([]), mode="execute")
    agent.tasks.start("Atividade")
    agent.tasks.changed("x.py", "example")
    with CodeIndex(agent.repository) as index, pytest.raises(ValueError, match="pendentes"):
        agent.execute(index, "finish_task", {"status": "completed", "summary": "Tudo pronto"})


def test_task_persistence_retention_redaction_and_no_grant_restoration(tmp_path):
    from codaro.sessions import SessionStore

    store = TaskStore(tmp_path, redact=SessionStore(tmp_path, "private-key").redact)
    for i in range(22):
        store.start(f"task {i} private-key", new=True)
    restored = TaskStore(tmp_path)
    assert len(restored.load()["tasks"]) == 20
    assert "private-key" not in restored.path.read_text()
    assert restored.current()["objective"].startswith("task 21")
    assert restored.path.stat().st_mode & 0o777 == 0o600


def test_mode_switch_preserves_task_but_clears_grant(tmp_path):
    agent = Agent(Repository(tmp_path), FakeModel([]), mode="plan")
    task = agent.tasks.start("Planejar")
    agent.policy.grant(task["id"], ["src"], [])
    agent.set_mode(Mode.EXECUTE)
    assert agent.tasks.current()["id"] == task["id"]
    assert agent.policy.kind == "action"
    agent._lock.acquire()
    try:
        with pytest.raises(ValueError):
            agent.set_mode(Mode.ASK)
    finally:
        agent._lock.release()


def test_cancellation_during_review_records_cancelled_task_without_writes(tmp_path):
    cancelled = threading.Event()

    def review(*_):
        cancelled.set()
        return True

    model = FakeModel(
        [
            call(
                "apply_changes",
                {
                    "reason": "criar",
                    "operations": [{"kind": "create", "path": "x.py", "content": "x=1"}],
                },
            )
        ]
    )
    from codaro.provider import RequestCancelled

    agent = Agent(Repository(tmp_path), model, mode="execute", approve_edit=review)
    with pytest.raises(RequestCancelled):
        agent.ask("Criar", cancelled=cancelled)
    assert not (tmp_path / "x.py").exists()
    assert agent.tasks.current()["state"] == "cancelled"
    assert not agent.edits.pending


def test_all_previous_validation_commands_must_run_on_current_revision(tmp_path):
    store = TaskStore(tmp_path)
    store.start("Validar")
    store.changed("x.py", "checkpoint")
    for argv in (["pytest", "-q"], ["ruff", "check"]):
        store.validation({"argv": argv, "exit_code": 0, "timed_out": False})
    assert store.validation_ready()
    store.changed("x.py", "another")
    store.validation({"argv": ["pytest", "-q"], "exit_code": 0, "timed_out": False})
    assert not store.validation_ready()
    store.validation({"argv": ["ruff", "check"], "exit_code": 0, "timed_out": False})
    assert store.validation_ready()


def test_external_change_after_test_invalidates_completion(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n")
    agent = Agent(Repository(tmp_path), FakeModel([]), mode="execute")
    agent.tasks.start("Verificar código")
    with CodeIndex(agent.repository) as index:
        agent.sync_workspace(index)
        agent.tasks.validation({"argv": ["pytest"], "exit_code": 0, "timed_out": False})
        (tmp_path / "x.py").write_text("x = 2\n")
        with pytest.raises(ValueError, match="pendentes"):
            agent.execute(index, "finish_task", {"status": "completed", "summary": "Pronto"})
    assert not agent.tasks.validation_ready()


def test_fresh_agent_resume_preserves_plan_and_does_not_replay_operations(tmp_path):
    first = Agent(Repository(tmp_path), FakeModel([]), mode="execute")
    task = first.tasks.start("Modificar")
    first.tasks.plan([{"title": "Implementar", "state": "doing"}], ["Testes aprovados"])
    first.tasks.event("command_started", {"argv": ["pytest"]})
    first.policy.grant(task["id"], ["src"], [["pytest"]])
    model = FakeModel([{"content": "A execução anterior ficou interrompida."}])
    fresh = Agent(Repository(tmp_path), model, mode="ask")
    fresh.ask("Qual o estado da tarefa?")
    assert fresh.tasks.current()["id"] == task["id"]
    assert fresh.policy.kind == "action"
    assert len(model.requests) == 1
    assert "command_started" in model.requests[0][0][0]["content"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "malformed"])
def test_task_storage_rejects_links_and_invalid_data(tmp_path, kind):
    root = tmp_path / "project"
    root.mkdir()
    store = TaskStore(root)
    store.start("Tarefa")
    if kind == "malformed":
        data = json.loads(store.path.read_text())
        data["tasks"][0]["validations"] = [{"argv": "invalid"}]
        store.path.write_text(json.dumps(data))
    else:
        import os

        target = tmp_path / "external.json"
        target.write_text(store.path.read_text())
        store.path.unlink()
        if kind == "symlink":
            store.path.symlink_to(target)
        else:
            os.link(target, store.path)
    with pytest.raises(ValueError):
        store.current()


def test_task_grant_applies_and_executes_without_interactive_callbacks(tmp_path):
    argv = [sys.executable, "-c", "import new; assert new.x == 1"]
    model = FakeModel(
        [
            call(
                "apply_changes",
                {
                    "reason": "Criar módulo",
                    "operations": [{"kind": "create", "path": "new.py", "content": "x=1\n"}],
                },
            ),
            call("run_command", {"argv": argv, "purpose": "validation"}),
            call("finish_task", {"status": "completed", "summary": "Arquivo verificado"}),
            {"content": "Arquivo criado e verificado."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, mode="execute")
    task = agent.tasks.start("Criar módulo")
    agent.policy.grant(task["id"], ["new.py"], [argv])
    assert agent.ask("Continue") == "Arquivo criado e verificado."
    assert (tmp_path / "new.py").read_text() == "x=1\n"
    assert agent.tasks.current()["state"] == "completed"
    assert any(tool["function"]["name"] == "run_command" for tool in model.requests[0][1])


def test_task_pages_and_projection_are_bounded_with_large_interaction(tmp_path):
    store = TaskStore(tmp_path)
    store.start("x" * 8000)
    store.event("interaction", {"request": "y" * 8000})
    assert len(json.dumps(store.projection())) < 3000
    page = store.page(0, 200)
    assert len(page["text"]) == 200 and page["next_offset"] == 200
    next_page = store.page(page["next_offset"], 200)
    assert next_page["offset"] == 200


def test_optional_citations_do_not_fail_evaluation_when_sources_and_facts_match():
    from codaro.evaluation import answer_metrics

    result = answer_metrics(
        "A regra verifica is_admin.", [("auth.py", 1, 4)], ["auth.py"], ["is_admin"]
    )
    assert result["expectations_passed"]
    assert result["citations"] == 0 and result["observed_path_recall"] == 1


def test_small_window_execution_compacts_without_reapplying_changes(tmp_path):
    from codaro.provider import Settings

    content = "\n".join(f"v{i} = '{'x' * 80}'" for i in range(200)) + "\n"
    (tmp_path / "large.py").write_text(content)
    responses = [
        call("read_lines", {"path": "large.py", "start": 1, "end": 1}),
        call(
            "propose_edit",
            {
                "path": "large.py",
                "old_text": "v0 = '" + "x" * 80 + "'",
                "new_text": "v0 = 'changed'",
                "reason": "Ajustar",
            },
        ),
    ]
    for number in range(4):
        responses.append(
            call(
                "read_lines",
                {"path": "large.py", "start": number * 50 + 1, "end": number * 50 + 50},
            )
        )
    responses += [
        call(
            "run_command",
            {
                "argv": [sys.executable, "-c", "import large; assert large.v0 == 'changed'"],
                "purpose": "validation",
            },
        ),
        {"content": "Verificado."},
    ]
    model = FakeModel(responses)
    model.settings = Settings(
        "https://test.invalid/v1", "test", context_window=8192, max_output_tokens=512
    )
    reviews = []
    agent = Agent(
        Repository(tmp_path),
        model,
        mode="execute",
        approve_edit=lambda items, _: reviews.append(items) or True,
        approve_command=lambda *_: True,
    )
    assert agent.ask("Ajuste v0 e valide") == "Verificado."
    assert len(reviews) == 1
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["compactions"]
    assert all(
        turn["budget"]["input_tokens_estimate"] <= agent.input_limit for turn in flow["turns"]
    )


def test_plan_can_replace_obsolete_validation_commands_without_granting_permission(tmp_path):
    store = TaskStore(tmp_path)
    store.start("Validar o módulo renomeado")
    store.changed("new.py", "checkpoint")
    store.validation({"argv": ["python", "old_checks.py"], "exit_code": 1, "timed_out": False})
    store.plan([], ["Teste do módulo atual aprovado"], [["python", "new_checks.py"]])
    assert not store.validation_ready()
    store.validation({"argv": ["python", "new_checks.py"], "exit_code": 0, "timed_out": False})
    assert store.validation_ready()
    assert not ApprovalPolicy().permits_command(store.current()["id"], ["python", "new_checks.py"])


def test_declared_checks_are_required_even_without_source_changes(tmp_path):
    store = TaskStore(tmp_path)
    store.start("Verificar implementação existente")
    store.plan([], ["Teste deve passar"], [["pytest", "-q"]])
    assert not store.validation_ready()
    store.validation({"argv": ["pytest", "-q"], "exit_code": 0, "timed_out": False})
    assert store.validation_ready()


def test_controller_pending_validation_notice_is_retained_in_conversation(tmp_path):
    model = FakeModel(
        [
            call(
                "apply_changes",
                {
                    "reason": "Criar",
                    "operations": [{"kind": "create", "path": "x.py", "content": "x=1"}],
                },
            ),
            call("finish_task", {"status": "blocked", "summary": "Sem ambiente para validar"}),
            {"content": "Arquivo criado."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, mode="execute", approve_edit=lambda *_: True)
    answer = agent.ask("Crie o módulo e valide")
    assert "Validação pendente" in answer
    assert agent.turns[-1][-1]["content"] == answer
    assert agent.tasks.current()["state"] == "blocked"


@pytest.mark.parametrize("data", [b"", b"\xef\xbb\xbf"])
def test_freshly_read_empty_file_can_be_removed_and_restored(tmp_path, data):
    path = tmp_path / "empty.py"
    path.write_bytes(data)
    repository = Repository(tmp_path)
    edits = EditManager(repository)
    edits.observe(repository.read_lines("empty.py", 1, 1), data)
    proposal = edits.prepare_operations([{"kind": "delete", "path": "empty.py"}], "Remover vazio")[
        0
    ]
    edits.apply(proposal.id)
    assert not path.exists()
    undo = edits.propose_undo(proposal.checkpoint_id)
    edits.apply(undo.id)
    assert path.read_bytes() == data


@pytest.mark.parametrize("kind", ["create", "delete"])
def test_error_after_publication_reports_applied_with_warning(tmp_path, monkeypatch, kind):
    repository = Repository(tmp_path)
    manager = EditManager(repository)
    if kind == "delete":
        (tmp_path / "x.py").write_text("x=1")
        manager.observe(repository.read_lines("x.py", 1, 1), b"x=1")
        operation = {"kind": "delete", "path": "x.py"}
    else:
        operation = {"kind": "create", "path": "x.py", "content": "x=1"}
    proposal = manager.prepare_operations([operation], "Alterar")[0]
    original = manager._apply

    def apply_then_fail(item):
        original(item)
        raise OSError("Falha posterior simulada")

    monkeypatch.setattr(manager, "_apply", apply_then_fail)
    manager.apply(proposal.id)
    assert proposal.state == "applied"
    assert "posterior" in proposal.checkpoint_warning
    assert (tmp_path / "x.py").exists() == (kind == "create")
    assert manager.checkpoints.list()[0]["can_undo"]
