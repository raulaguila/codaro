"""Real commands, approval boundaries, revision receipts and conflict recovery."""

import json
import sys
import time

import pytest
from test_agent import FakeModel

from codaro.agent import Agent
from codaro.agent.progress import TurnProgress
from codaro.credentials import CredentialVault
from codaro.edits import EditManager
from codaro.index import CodeIndex
from codaro.jobs import JobManager, command_directory
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.validation import discover_checks, validation_report


def agent_for(root, **kwargs):
    agent = Agent(Repository(root), FakeModel([]), mode="execute", **kwargs)
    agent.tasks.start("Implementar e verificar")
    return agent


def test_multiple_replacements_use_one_observed_snapshot_preserving_bom_and_crlf(tmp_path):
    path = tmp_path / "code.py"
    original = b"\xef\xbb\xbfx = 1\r\ny = 2\r\n"
    path.write_bytes(original)
    repository = Repository(tmp_path)
    edits = EditManager(repository)
    edits.observe(repository.read_lines("code.py", 1, 2), original)
    proposal = edits.prepare_operations(
        [
            {
                "kind": "edit",
                "path": "code.py",
                "replacements": [
                    {"old_text": "x = 1", "new_text": "x = 3"},
                    {"old_text": "y = 2", "new_text": "y = 4"},
                ],
            }
        ],
        "Corrigir valores",
    )[0]
    assert path.read_bytes() == original
    edits.apply(proposal.id)
    assert path.read_bytes() == b"\xef\xbb\xbfx = 3\r\ny = 4\r\n"
    undo = edits.propose_undo(proposal.checkpoint_id)
    edits.apply(undo.id)
    assert path.read_bytes() == original


def test_multiple_replacements_cannot_overlap_or_touch_unread_lines(tmp_path):
    (tmp_path / "a.py").write_text("first = 1\nsecond = 2\n")
    repository = Repository(tmp_path)
    edits = EditManager(repository)
    edits.observe(repository.read_lines("a.py", 1, 1), repository.read_bytes(tmp_path / "a.py"))
    for replacements in (
        [{"old_text": "second", "new_text": "new"}],
        [{"old_text": "first", "new_text": "new"}, {"old_text": "first = 1", "new_text": "x"}],
    ):
        with pytest.raises(ValueError):
            edits.propose_many("a.py", replacements, "Corrigir")
    assert not edits.pending


def test_atomic_preflight_preserves_all_files_on_external_conflict(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 1\n")

    def approve(*args):
        (tmp_path / "b.py").write_text("external = 9\n")
        return True

    agent = agent_for(tmp_path, approve_edit=approve)
    with CodeIndex(agent.repository) as index:
        for name in ("a.py", "b.py"):
            result = agent.execute(index, "read_lines", {"path": name, "start": 1, "end": 1})
            agent.edits.observe(result, agent._read_snapshot)
        result = agent.execute(
            index,
            "apply_changes",
            {
                "reason": "Ajustar",
                "operations": [
                    {"kind": "edit", "path": "a.py", "old_text": "a = 1", "new_text": "a = 2"},
                    {"kind": "edit", "path": "b.py", "old_text": "b = 1", "new_text": "b = 2"},
                ],
            },
        )
    assert result["state"] == "conflict"
    assert (tmp_path / "a.py").read_text() == "a = 1\n"
    assert (tmp_path / "b.py").read_text() == "external = 9\n"


def test_failed_atomic_batch_rolls_back_applied_files_with_checkpoints(tmp_path, monkeypatch):
    agent = agent_for(tmp_path, approve_edit=lambda *_: True)
    real_apply = agent.edits.apply

    def apply(identifier):
        proposal = agent.edits.proposals[identifier]
        if proposal.path == "b.py":
            proposal.state = "conflict"
            raise ValueError("injected filesystem failure")
        return real_apply(identifier)

    monkeypatch.setattr(agent.edits, "apply", apply)
    with CodeIndex(agent.repository) as index:
        result = agent.execute(
            index,
            "apply_changes",
            {
                "reason": "Criar lote",
                "operations": [
                    {"kind": "create", "path": "a.py", "content": "a=1\n"},
                    {"kind": "create", "path": "b.py", "content": "b=1\n"},
                ],
            },
        )
    assert result["state"] == "rolled_back" and result["rolled_back"]
    assert not (tmp_path / "a.py").exists()
    assert not (tmp_path / "b.py").exists()
    assert agent.edits.checkpoints.list()


def test_background_output_is_incremental_cancellable_and_owned(tmp_path):
    manager = JobManager(Repository(tmp_path))
    try:
        result = manager.start(
            [sys.executable, "-u", "-c", 'import time;print("ready",flush=True);time.sleep(30)']
        )
        job_id = result["job_id"]
        deadline = time.monotonic() + 5
        while "ready" not in result["output"] and time.monotonic() < deadline:
            result = manager.poll(job_id, wait_ms=100)
        assert result["state"] == "running" and "ready" in result["output"]
        assert manager.cancel(job_id)["state"] == "cancelled"
        assert not manager.active()
        with pytest.raises(ValueError):
            JobManager(Repository(tmp_path)).poll(job_id)
    finally:
        manager.close()


def test_cwd_is_in_approval_and_task_grants_do_not_authorize_another_directory(tmp_path):
    (tmp_path / "package").mkdir()
    reviews = []

    def approve(argv, timeout, cancelled, *, cwd="."):
        reviews.append(cwd)
        return True

    agent = agent_for(tmp_path, approve_command=approve)
    argv = [sys.executable, "-c", "import os;print(os.path.basename(os.getcwd()))"]
    task = agent.tasks.current()
    agent.policy.grant(task["id"], [], [argv])
    with CodeIndex(agent.repository) as index:
        result = agent.execute(index, "run_command", {"argv": argv, "cwd": "package"})
    assert reviews == ["package"]
    assert result["output"].strip() == "package"
    with pytest.raises(ValueError):
        command_directory(agent.repository, "../outside")


def test_rejected_background_command_never_starts(tmp_path):
    agent = agent_for(tmp_path, approve_command=lambda *_: False)
    with CodeIndex(agent.repository) as index:
        result = agent.execute(index, "start_command", {"argv": [sys.executable, "-c", "print(1)"]})
    assert "error" in result and not agent.jobs.jobs


def test_discovery_is_readonly_and_criteria_require_current_registered_results(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts="-q"\n')
    (tmp_path / "test_value.py").write_text("def test_value():\n    assert 1 + 1 == 2\n")
    agent = agent_for(tmp_path, approve_command=lambda *_: True)
    checks = discover_checks(agent.repository)
    check = next(item for item in checks if item["category"] == "test")
    assert not (tmp_path / ".pytest_cache").exists()
    agent.tasks.plan(
        [],
        ["Soma correta"],
        criterion_checks=[{"criterion": "Soma correta", "check_ids": [check["id"]]}],
    )
    with CodeIndex(agent.repository) as index:
        result = agent.execute(index, "run_check", {"check_id": check["id"]})
        assert result["exit_code"] == 0
        report = agent.execute(index, "get_validation_report", {})
        assert report["status"] == "validated"
        (tmp_path / "test_value.py").write_text("def test_value():\n    assert False\n")
        report = agent.execute(index, "get_validation_report", {})
        assert report["status"] != "validated"
    assert not agent.tasks.validation_ready()


def test_arbitrary_successful_command_cannot_pass_a_bound_criterion(tmp_path):
    agent = agent_for(tmp_path, approve_command=lambda *_: True)
    agent.tasks.plan(
        [], ["Requisito"], criterion_checks=[{"criterion": "Requisito", "check_ids": ["a" * 16]}]
    )
    with CodeIndex(agent.repository) as index:
        agent.execute(
            index,
            "run_command",
            {"argv": [sys.executable, "-c", 'print("ok")'], "purpose": "validation"},
        )
    assert validation_report(agent.tasks.current())["status"] != "validated"
    assert not agent.tasks.validation_ready()


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def set_password(self, service, key, value):
        self.values[key] = value

    def get_password(self, service, key):
        return self.values.get(key)

    def delete_password(self, service, key):
        self.values.pop(key, None)


def credential_store(root):
    backend = MemoryKeyring()
    store = ProviderStore(root / "config", vault=CredentialVault(backend))
    store.mutate(
        lambda value: value["profiles"].update(
            local={
                "kind": "openai-compatible",
                "base_url": "https://test.invalid/v1",
                "api_key": "private-test-value",
                "tls_insecure": False,
                "model": "",
                "models": [],
            }
        )
    )
    return store, backend


def test_vault_migration_and_export_never_persist_or_export_the_secret(tmp_path):
    store, backend = credential_store(tmp_path)
    store.credential_storage("local", "keyring")
    assert "private-test-value" not in store.path.read_text()
    assert store.load()["profiles"]["local"]["api_key"] == "private-test-value"
    assert "private-test-value" not in json.dumps(store.export_profiles())
    assert "credential_ref" not in json.dumps(store.export_profiles())
    store.credential_storage("local", "file")
    assert store.load()["version"] == 1 and not backend.values


def test_failed_vault_write_preserves_previous_configuration(tmp_path, monkeypatch):
    store, backend = credential_store(tmp_path)
    before = store.path.read_bytes()

    def fail(*args):
        raise RuntimeError("locked")

    monkeypatch.setattr(backend, "set_password", fail)
    with pytest.raises(ValueError):
        store.credential_storage("local", "keyring")
    assert store.path.read_bytes() == before


def test_progress_detects_rephrased_searches_with_identical_evidence():
    progress = TurnProgress()
    result = {"results": [{"path": "a.py", "file_hash": "v1", "start_line": 1}]}
    progress.observe("search_code", {"query": "a"}, result)
    advice = [
        progress.observe(
            "search_code",
            {"query": str(n)},
            {"results": [{**result["results"][0], "score": n, "preview": f"fragment {n}"}]},
        )
        for n in range(3)
    ]
    assert advice[-1] and progress.flat == 3
    progress.observe("read_lines", {}, {"path": "b.py", "content": "new evidence"})
    assert progress.flat == 0


def test_inactive_locked_vault_does_not_block_listing_export_or_other_profile(
    tmp_path, monkeypatch
):
    store, backend = credential_store(tmp_path)
    store.credential_storage("local", "keyring")

    def unavailable(*args):
        raise RuntimeError("locked")

    monkeypatch.setattr(backend, "get_password", unavailable)
    assert store.load(resolve_secrets=False)["profiles"]["local"]["credential_ref"]
    assert "credential_ref" not in json.dumps(store.export_profiles())
    store.mutate(
        lambda value: value["profiles"].update(
            other={
                "kind": "ollama",
                "base_url": "http://localhost:11434",
                "api_key": "",
                "tls_insecure": False,
                "model": "",
                "models": [],
            }
        )
    )
    assert store.profile("other")[1]["kind"] == "ollama"
    assert store.load(resolve_secrets=False)["version"] == 2


def test_credential_rotation_write_failure_retains_old_vault_reference(tmp_path, monkeypatch):
    store, backend = credential_store(tmp_path)
    store.credential_storage("local", "keyring")
    before = store.path.read_bytes()
    reference = store.load(resolve_secrets=False)["profiles"]["local"]["credential_ref"]

    def unavailable(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("codaro.llm.profiles.atomic_write", unavailable)
    with pytest.raises(OSError):
        store.mutate(lambda value: value["profiles"]["local"].update(api_key="replacement"))
    assert store.path.read_bytes() == before
    assert backend.values == {reference: "private-test-value"}


def test_budget_keeps_owned_job_cursor_and_compaction_keeps_receipt():
    from test_agent import call
    from test_context import assert_protocol

    from codaro.agent.results import fit_result
    from codaro.context import compact_batch

    result = {
        "job_id": "a" * 16,
        "state": "running",
        "output": "x" * 8000,
        "argv": ["large" * 100],
        "offset": 100,
        "next_offset": 8100,
    }
    fitted = fit_result(result, 150)
    assert fitted["job_id"] == result["job_id"]
    assert fitted["next_offset"] == 100 + len(fitted.get("output", ""))
    turn = [
        {"role": "user", "content": "Run"},
        {"role": "assistant", **call("start_command", {"argv": ["check"]}, "job")},
        {
            "role": "tool",
            "tool_call_id": "job",
            "name": "start_command",
            "content": json.dumps(result),
        },
    ]
    assert compact_batch(turn)
    assert result["job_id"] in json.dumps(turn)
    assert_protocol(turn)


def test_sandbox_binds_whole_project_while_changing_working_directory(tmp_path, monkeypatch):
    from codaro.command_sandbox import sandbox_command

    monkeypatch.setenv("CODARO_SANDBOX", "required")
    monkeypatch.setattr("codaro.command_sandbox.sys.platform", "linux")
    monkeypatch.setattr("codaro.command_sandbox.shutil.which", lambda _: "/usr/bin/bwrap")
    cwd = tmp_path / "service"
    cwd.mkdir()
    argv, mode = sandbox_command(tmp_path, ["check"], {}, cwd=cwd)
    assert mode == "bubblewrap"
    assert argv[argv.index("--bind") + 1 : argv.index("--bind") + 3] == [str(tmp_path)] * 2
    assert argv[argv.index("--chdir") + 1] == str(cwd)


def test_long_conversation_compacts_repeatedly_and_restores_confirmed_decision(tmp_path):
    from test_agent import call
    from test_context import assert_protocol

    from codaro.llm import Settings

    (tmp_path / "module.py").write_text("value = 1\n" * 120)
    messages = []
    for n in range(20):
        messages.extend(
            [
                call("read_lines", {"path": "module.py", "start": 1, "end": 120}, f"read-{n}"),
                call("compact_context", {}, f"compact-{n}"),
                {"content": "Consulta concluída. " * 30},
            ]
        )
    model = FakeModel(messages)
    model.settings = Settings("http://localhost/v1", "small", context_window=4096)
    agent = Agent(Repository(tmp_path), model, mode="ask")
    try:
        for n in range(20):
            assert agent.ask(f"Consulte module.py e compacte os trechos. Rodada {n}.")
        for payload, _ in model.requests:
            assert_protocol(payload)
        assert len(agent.memory.search("Rodada")["results"]) > 0
        restored = Agent(Repository(tmp_path), FakeModel([]), mode="ask")
        try:
            restored.activate_session("default")
            assert restored.memory.search("Rodada")["results"]
            assert not restored.jobs.active()
            assert not restored.policy.auto_edits
        finally:
            restored.close()
    finally:
        agent.close()
