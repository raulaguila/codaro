import json

from codaro.agent import Agent
from codaro.repository import Repository


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages, tools=None):
        self.requests.append((list(messages), tools))
        return next(self.responses)


def call(name, arguments, identifier="call-1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": identifier,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def test_agent_searches_then_reads_before_answering(tmp_path):
    (tmp_path / "auth.py").write_text("def can_edit(user):\n    return user.is_admin\n")
    model = FakeModel(
        [
            call("search_code", {"query": "can_edit"}),
            call("read_symbol", {"path": "auth.py", "symbol": "can_edit"}, "call-2"),
            {"role": "assistant", "content": "auth.py:2 verifica user.is_admin."},
        ]
    )
    answer = Agent(Repository(tmp_path), model).ask("Quem pode editar?")
    assert "auth.py:2" in answer
    outputs = [message for message in model.requests[-1][0] if message["role"] == "tool"]
    assert "return user.is_admin" in json.loads(outputs[-1]["content"])["content"]


def test_agent_forces_final_response_after_step_limit(tmp_path):
    model = FakeModel(
        [
            call("list_files", {}),
            {"role": "assistant", "content": "Não há arquivos permitidos."},
        ]
    )
    Agent(Repository(tmp_path), model, max_steps=1).ask("Quais arquivos existem?")
    assert model.requests[-1][1] is None


def test_invalid_tool_arguments_are_returned_as_errors(tmp_path):
    model = FakeModel(
        [
            call("read_lines", {"path": "auth.py", "start": True, "end": 3}),
            {"role": "assistant", "content": "Não foi possível ler."},
        ]
    )
    Agent(Repository(tmp_path), model).ask("Leia o código.")
    tool_result = next(item for item in model.requests[-1][0] if item["role"] == "tool")
    assert "inteiro" in json.loads(tool_result["content"])["error"]


def test_malformed_model_message_is_a_controlled_error(tmp_path):
    import pytest

    from codaro.provider import ModelError

    with pytest.raises(ModelError):
        Agent(Repository(tmp_path), FakeModel([{"content": []}])).ask("Investigue.")


def test_repeated_reads_do_not_repeat_source(tmp_path):
    (tmp_path / "x.py").write_text("def f(): return True")
    model = FakeModel(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}),
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}, "call-2"),
            {"content": "x.py:1 retorna True."},
        ]
    )
    Agent(Repository(tmp_path), model).ask("Leia f.")
    outputs = [item for item in model.requests[-1][0] if item["role"] == "tool"]
    assert json.loads(outputs[1]["content"])["already_read"]
    assert "return True" not in outputs[1]["content"]


def test_bad_tool_json_does_not_break_agent(tmp_path):
    for arguments in ["{invalid", "[]", "null"]:
        tool = call("list_files", {})
        tool["tool_calls"][0]["function"]["arguments"] = arguments
        model = FakeModel([tool, {"content": "Não foi possível investigar."}])
        Agent(Repository(tmp_path), model).ask("Liste.")
        output = next(item for item in model.requests[-1][0] if item["role"] == "tool")
        assert "error" in json.loads(output["content"])


def test_failed_requests_can_be_retried(tmp_path):
    model = FakeModel(
        [
            call("read_lines", {"path": "missing.py", "start": 1, "end": 1}),
            call("read_lines", {"path": "missing.py", "start": 1, "end": 1}, "call-2"),
            {"content": "Arquivo inexistente."},
        ]
    )
    Agent(Repository(tmp_path), model).ask("Leia.")
    outputs = [item for item in model.requests[-1][0] if item["role"] == "tool"]
    assert all("error" in json.loads(item["content"]) for item in outputs)


def test_history_keeps_answers_without_tool_payloads(tmp_path):
    (tmp_path / "x.py").write_text("def f(): return True")
    model = FakeModel(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}),
            {"content": "Primeira resposta."},
            {"content": "Segunda resposta."},
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    agent.ask("Leia f.")
    agent.ask("Explique mais.")
    assert not any(item["role"] == "tool" for item in model.requests[-1][0])
    assert "Primeira resposta." in json.dumps(model.requests[-1][0])


def test_tool_output_budget_includes_escaping(tmp_path):
    (tmp_path / "x.py").write_text("x = " + '"\\' * 4000)
    model = FakeModel(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}),
            {"content": "Leitura parcial."},
        ]
    )
    Agent(Repository(tmp_path), model, tool_budget=1024).ask("Leia x.")
    output = next(item for item in model.requests[-1][0] if item["role"] == "tool")
    assert len(output["content"]) <= 1024
    assert json.loads(output["content"])["truncated"]


def test_context_budget_evicts_complete_turns(tmp_path):
    from codaro.agent import serialize

    model = FakeModel([{"content": "Nova resposta."}])
    agent = Agent(Repository(tmp_path), model, history_budget=100_000, context_budget=12_000)
    agent.turns = [
        [{"role": "user", "content": "old"}, {"role": "assistant", "content": "x" * 11_000}]
    ]
    agent.ask("Pergunta nova.")
    messages, tools = model.requests[0]
    assert len(serialize({"messages": messages, "tools": tools})) <= 12_000
    assert not any(item["content"] == "old" for item in messages)


def test_cancellation_prevents_model_call(tmp_path):
    import threading

    import pytest

    from codaro.agent import InvestigationCancelled

    cancelled = threading.Event()
    cancelled.set()
    model = FakeModel([])
    with pytest.raises(InvestigationCancelled):
        Agent(Repository(tmp_path), model).ask("Investigue.", cancelled=cancelled)
    assert not model.requests


def test_cancellation_after_response_does_not_save_history(tmp_path):
    import threading

    import pytest

    from codaro.agent import InvestigationCancelled

    cancelled = threading.Event()

    class CancellingModel:
        def complete(self, messages, tools=None):
            cancelled.set()
            return {"content": "Resposta."}

    agent = Agent(Repository(tmp_path), CancellingModel())
    with pytest.raises(InvestigationCancelled):
        agent.ask("Investigue.", cancelled=cancelled)
    assert not agent.turns


def test_concurrent_investigation_is_rejected(tmp_path):
    import pytest

    agent = Agent(Repository(tmp_path), FakeModel([]))
    agent._lock.acquire()
    try:
        with pytest.raises(ValueError, match="andamento"):
            agent.ask("Investigue.")
    finally:
        agent._lock.release()


def test_repeated_read_refreshes_changed_file(tmp_path):
    source = tmp_path / "x.py"
    source.write_text("x = 1")

    class EditingModel(FakeModel):
        def complete(self, messages, tools=None):
            if len(self.requests) == 1:
                source.write_text("x = 2")
            return super().complete(messages, tools)

    model = EditingModel(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}),
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}, "call-2"),
            {"content": "O arquivo mudou."},
        ]
    )
    Agent(Repository(tmp_path), model).ask("Leia x.")
    results = [item for item in model.requests[-1][0] if item["role"] == "tool"]
    assert "x = 2" in json.loads(results[-1]["content"])["content"]


def test_context_budget_never_sends_oversized_batch(tmp_path):
    import pytest

    from codaro.agent import serialize
    from codaro.provider import ModelError

    # A model returning oversized tool-call batches is rejected before another request.
    batch = {"role": "assistant", "content": None, "tool_calls": []}
    for number in range(8):
        batch["tool_calls"].append(
            call("search_code", {"query": "x" * 4000}, str(number))["tool_calls"][0]
        )
    model = FakeModel([batch])
    with pytest.raises(ModelError, match="Lote"):
        Agent(Repository(tmp_path), model, context_budget=12_000).ask("Busque.")
    assert all(len(serialize({"messages": m, "tools": t})) <= 12_000 for m, t in model.requests)


def test_deeply_nested_tool_arguments_are_returned_as_error(tmp_path):
    malformed = call("list_files", {})
    malformed["tool_calls"][0]["function"]["arguments"] = "[" * 1500 + "]" * 1500
    model = FakeModel([malformed, {"content": "Argumentos inválidos."}])
    Agent(Repository(tmp_path), model).ask("Liste.")
    output = next(item for item in model.requests[-1][0] if item["role"] == "tool")
    assert "error" in json.loads(output["content"])


def test_detailed_tool_events_include_target_outcome_and_duration(tmp_path):
    (tmp_path / "x.py").write_text("def f(): return True")
    model = FakeModel(
        [
            call("search_code", {"query": "f"}),
            call("read_symbol", {"path": "x.py", "symbol": "f"}, "call-2"),
            {"content": "Resposta."},
        ]
    )
    details = []
    Agent(Repository(tmp_path), model).ask("Investigue f.", on_detail=details.append)
    endings = [event for event in details if event.kind == "tool_end"]
    assert len(endings) == 2
    assert endings[0].title == "Buscar código"
    assert "Consulta: f" in endings[0].detail
    assert "1 resultado" in endings[0].detail
    assert "x.py" in endings[1].detail
    assert endings[1].elapsed_ms >= 0
    assert all(event.state == "success" for event in endings)
    assert any(event.context_chars for event in details if event.kind == "model_start")


def test_stream_failure_does_not_save_partial_history(tmp_path):
    import pytest

    from codaro.provider import ModelError

    class BrokenStream:
        def stream(self, messages, tools, on_delta, cancelled):
            on_delta("Parcial")
            raise ModelError("Conexão interrompida.")

    received = []
    agent = Agent(Repository(tmp_path), BrokenStream())
    with pytest.raises(ModelError):
        agent.ask("Investigue.", on_delta=received.append)
    assert received == ["Parcial"]
    assert not agent.turns


def edit_responses():
    return [
        call("read_lines", {"path": "code.py", "start": 1, "end": 1}),
        call(
            "propose_edit",
            {
                "path": "code.py",
                "old_text": "x = 1",
                "new_text": "x = 2",
                "reason": "Ajustar valor",
            },
            "edit-1",
        ),
        {"content": "Preparei uma edição; aguarda aprovação."},
    ]


def test_agent_proposes_without_writing_and_blocks_new_questions(tmp_path):
    import pytest

    path = tmp_path / "code.py"
    path.write_text("x = 1\n")
    model = FakeModel(edit_responses())
    agent = Agent(Repository(tmp_path), model, allow_edits=True)
    agent.ask("Mude x para 2.")
    assert path.read_text() == "x = 1\n"
    assert len(agent.edits.pending) == 1
    assert any(tool["function"]["name"] == "propose_edit" for tool in model.requests[0][1])
    with pytest.raises(ValueError, match="pendentes"):
        agent.ask("Outra pergunta.")
    assert len(agent.edits.pending) == 1
    proposal = agent.edits.pending[0]
    agent.edits.apply(proposal.id)
    assert path.read_text() == "x = 2\n"


def test_read_only_agent_rejects_edit_tool(tmp_path):
    (tmp_path / "code.py").write_text("x = 1\n")
    model = FakeModel(edit_responses())
    agent = Agent(Repository(tmp_path), model)
    agent.ask("Mude x.")
    assert not agent.edits.pending
    assert all(tool["function"]["name"] != "propose_edit" for tool in model.requests[0][1])
    result = next(item for item in model.requests[-1][0] if item.get("tool_call_id") == "edit-1")
    assert "desabilitada" in json.loads(result["content"])["error"]


def test_failed_response_discards_proposals_and_history(tmp_path):
    import pytest

    from codaro.provider import ModelError

    (tmp_path / "code.py").write_text("x = 1\n")
    agent = Agent(
        Repository(tmp_path), FakeModel([*edit_responses()[:2], {"content": []}]), allow_edits=True
    )
    with pytest.raises(ModelError):
        agent.ask("Edite.")
    assert not agent.edits.pending
    assert not agent.turns
    assert (tmp_path / "code.py").read_text() == "x = 1\n"


def test_cancelled_proposal_is_discarded(tmp_path):
    import threading

    import pytest

    from codaro.agent import InvestigationCancelled

    (tmp_path / "code.py").write_text("x = 1\n")
    cancelled = threading.Event()
    agent = Agent(Repository(tmp_path), FakeModel(edit_responses()), allow_edits=True)

    def detail(event):
        if event.kind == "tool_end" and event.state == "pending":
            cancelled.set()

    with pytest.raises(InvestigationCancelled):
        agent.ask("Edite.", cancelled=cancelled, on_detail=detail)
    assert not agent.edits.pending
    assert not agent.turns


def test_repository_context_and_tools_use_selected_root_not_process_cwd(tmp_path, monkeypatch):
    install = tmp_path / "installed-codaro"
    selected = tmp_path / "meu projeto"
    install.mkdir()
    selected.mkdir()
    (install / "decoy.py").write_text("wrong = True\n")
    (selected / "selected.py").write_text("value = 'from selected project'\n")
    monkeypatch.chdir(install)
    model = FakeModel(
        [
            call("get_repository_info", {}),
            call("list_files", {}, "files"),
            call("read_lines", {"path": "selected.py", "start": 1, "end": 1}, "read"),
            {"content": "Código lido da pasta selecionada."},
        ]
    )
    agent = Agent(Repository(selected), model)
    agent.ask("Em qual diretório está? Leia selected.py.")
    for messages, _ in model.requests:
        context = next(
            line
            for line in messages[0]["content"].splitlines()
            if line.startswith('{"repository_root"')
        )
        assert json.loads(context)["repository_root"] == str(selected.resolve())
    outputs = {
        item["tool_call_id"]: json.loads(item["content"])
        for item in model.requests[-1][0]
        if item["role"] == "tool"
    }
    assert outputs["call-1"]["repository_root"] == str(selected.resolve())
    assert outputs["files"]["files"] == ["selected.py"]
    assert "from selected project" in outputs["read"]["content"]


def test_repository_context_survives_stale_answer_and_final_no_tools(tmp_path):
    model = FakeModel([{"content": "Resposta."}])
    agent = Agent(Repository(tmp_path), model, max_steps=0, allow_edits=True)
    agent.turns = [
        [
            {"role": "user", "content": "Onde estamos?"},
            {"role": "assistant", "content": "/home/user/codigo"},
        ]
    ]
    agent.ask("Confira o diretório atual.")
    messages, tools = model.requests[0]
    assert tools is None
    assert str(tmp_path.resolve()) in messages[0]["content"]
    assert "get_repository_info" in messages[0]["content"]
    assert "propose_edit_with_approval" in messages[0]["content"]


def test_decimal_string_arguments_are_normalized_before_tool_execution(tmp_path):
    (tmp_path / "auth.py").write_text("x = 1\n")
    model = FakeModel(
        [
            call("list_files", {"limit": "10", "offset": "0"}),
            {"content": "O diretório contém auth.py."},
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    assert "auth.py" in agent.ask("Quais arquivos existem?")
    output = next(item for item in model.requests[-1][0] if item["role"] == "tool")
    assert json.loads(output["content"])["files"] == ["auth.py"]


def test_integer_normalization_keeps_validation_and_bounds():
    import pytest

    for invalid in (True, 10.0, "1e1", "10.0", " 10 ", "01", "1; code", "9" * 100, "61", "-1"):
        with pytest.raises(ValueError):
            Agent.validate_arguments("list_files", {"limit": invalid})


def test_text_tool_call_is_repaired_via_protocol_not_executed(tmp_path):
    (tmp_path / "auth.py").write_text("x = 1\n")
    faux = 'Vou tentar novamente.\n{"name":"list_files","parameters":{"limit":"10","offset":"0"}}'
    model = FakeModel(
        [
            {"content": faux},
            call("list_files", {"limit": "10", "offset": "0"}),
            {"content": "Arquivo encontrado: auth.py."},
        ]
    )
    events = []
    agent = Agent(Repository(tmp_path), model)
    answer = agent.ask("Quais arquivos existem?", on_detail=events.append)
    assert answer == "Arquivo encontrado: auth.py."
    assert any(event.state == "retry" for event in events)
    assert "A chamada em texto não foi executada" in model.requests[1][0][-1]["content"]
    tools = [item for item in model.requests[-1][0] if item["role"] == "tool"]
    assert len(tools) == 1
    assert tools[0]["tool_call_id"] == "call-1"
    assert json.loads(tools[0]["content"])["files"] == ["auth.py"]
    assert faux not in json.dumps(agent.turns)


def test_repeated_text_tool_call_fails_without_saving_false_answer(tmp_path):
    import pytest

    from codaro.provider import ModelError

    faux = '{"name":"list_files","parameters":{}}'
    model = FakeModel([{"content": faux}, {"content": faux}])
    agent = Agent(Repository(tmp_path), model)
    with pytest.raises(ModelError, match="doctor --check-tools"):
        agent.ask("Liste arquivos.")
    assert not agent.turns
    assert len(model.requests) == 2
    assert not any(item["role"] == "tool" for messages, _ in model.requests for item in messages)


def test_documentation_example_is_not_executed_or_repaired(tmp_path):
    example = 'Exemplo da estrutura de chamada:\n{"name":"list_files","parameters":{"limit":10}}'
    model = FakeModel([{"content": example}])
    agent = Agent(Repository(tmp_path), model)
    assert agent.ask("Explique o formato de uma chamada de ferramenta.") == example
    assert len(model.requests) == 1


def test_malformed_json_as_content_does_not_break_protocol_detector():
    from codaro.agent import textual_tool_call

    assert not textual_tool_call('{"name":[],"parameters":{}}')
    assert not textual_tool_call('{"name":"list_files","parameters":')
