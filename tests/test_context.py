import json
import sys
from types import SimpleNamespace

import httpx
import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent, serialize
from codaro.context import COMPACT_PREFIX, TokenCounter, compact_batch
from codaro.provider import (
    ContextLimitError,
    ModelError,
    OpenAICompatible,
    Settings,
    build_payload,
)
from codaro.repository import Repository


def configured_model(responses, window=8192):
    model = FakeModel(responses)
    model.settings = Settings("https://test.invalid/v1", "test", context_window=window)
    return model


def assert_protocol(messages):
    pending = []
    for item in messages:
        if item.get("tool_calls"):
            assert not pending
            pending = [call["id"] for call in item["tool_calls"]]
        elif item["role"] == "tool":
            assert pending and item["tool_call_id"] == pending.pop(0)
        else:
            assert not pending
    assert not pending


def test_completed_batch_compaction_preserves_protocol_and_execution_status():
    turn = [
        {"role": "user", "content": "Teste."},
        call("run_command", {"argv": ["pytest"]}, "run"),
        {
            "role": "tool",
            "tool_call_id": "run",
            "name": "run_command",
            "content": json.dumps({"exit_code": 1, "timed_out": False, "output": "x" * 6000}),
        },
        call("read_lines", {"path": "x.py", "start": 1, "end": 2}, "pending"),
    ]
    change = compact_batch(turn)
    assert change["actions"][0]["exit_code"] == 1
    assert "Não repita comandos" in turn[1]["content"]
    assert turn[-1]["tool_calls"][0]["id"] == "pending"
    assert_protocol(turn[:-1])
    assert compact_batch(turn) is None  # Outstanding calls cannot be compacted.


def test_ledger_is_bounded_and_keeps_proposal_pending_state():
    turn = [{"role": "user", "content": "Investigue."}]
    for number in range(40):
        turn.extend(
            [
                call(
                    "propose_edit", {"path": f"x{number}.py", "new_text": "x" * 3000}, str(number)
                ),
                {
                    "role": "tool",
                    "tool_call_id": str(number),
                    "name": "propose_edit",
                    "content": json.dumps({"proposal_id": str(number), "state": "pending"}),
                },
            ]
        )
        assert compact_batch(turn)
    assert len(turn) == 2
    assert len(turn[1]["content"]) < 2400
    actions = json.loads(turn[1]["content"][len(COMPACT_PREFIX) :])["actions"]
    assert actions[-1]["state"] == "pending"
    assert actions[-1]["proposal_id"] == "39"
    assert_protocol(turn)


def test_token_counter_includes_utf8_escaping_tools_and_framing():
    counter = TokenCounter()
    plain = build_payload("test", [{"role": "user", "content": "a" * 100}], None)
    unicode = build_payload("test", [{"role": "user", "content": "界" * 100}], None)
    assert counter.count(unicode) > counter.count(plain)
    assert counter.count({**plain, "tools": [{"name": "x" * 500}]}) > counter.count(plain)
    assert counter.method.startswith("estimativa")


def test_explicit_tokenizer_uses_full_payload_without_special_token_injection(monkeypatch):
    seen = []

    class Encoder:
        def encode(self, text, *, disallowed_special):
            assert disallowed_special == ()
            seen.append(text)
            return [1] * 50

    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(get_encoding=lambda _: Encoder()))
    payload = build_payload("test", [{"role": "user", "content": "<|endoftext|>"}], None)
    assert TokenCounter("cl100k_base").count(payload) == 98
    assert json.loads(seen[0]) == payload


def test_small_window_compacts_current_investigation_and_permits_reread(tmp_path):
    (tmp_path / "x.py").write_text("\n".join(f"x{i} = '{'a' * 100}'" for i in range(100)))
    responses = [
        call("read_lines", {"path": "x.py", "start": 1, "end": 100}, f"read-{i}") for i in range(4)
    ]
    model = configured_model([*responses, {"content": "Leitura concluída."}])
    agent = Agent(Repository(tmp_path), model)
    events = []
    assert agent.ask("Leia o arquivo.", on_detail=events.append) == "Leitura concluída."
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["compactions"]
    assert any(event.title == "Compactando contexto" for event in events)
    reads = [item["result"] for step in flow["turns"] for item in step["tool_results"]]
    assert sum("content" in item for item in reads) >= 2
    for step in flow["turns"]:
        payload = step["request"]
        assert agent.counter.count(payload) <= agent.input_limit
        assert_protocol(payload["messages"])
    assert "x0" in flow["turns"][0]["tool_results"][0]["result"]["content"]


def test_dynamic_result_budget_preserves_line_bounds_and_pagination(tmp_path):
    (tmp_path / "x.py").write_text("\n".join(f"x{i} = '{'a' * 100}'" for i in range(100)))
    model = configured_model(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 100}),
            {"content": "Leitura parcial."},
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    agent.ask("Leia x.")
    result = json.loads(next(m["content"] for m in model.requests[1][0] if m["role"] == "tool"))
    assert result["truncated"]
    assert result["end_line"] < 100
    assert result["next_start_line"] <= result["end_line"] + 1
    files = agent.fit_result(
        {"files": ["a" * 100, "b" * 100, "c" * 100], "total": 9, "next_offset": 6}, 180
    )
    assert len(files["files"]) == 1
    assert files["next_offset"] == 4


def test_context_recovery_reuses_command_result_without_another_approval(tmp_path):
    requests, approvals = [], []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": call(
                                "run_command",
                                {"argv": [sys.executable, "-c", "print('ok' * 3000)"]},
                            )
                        }
                    ]
                },
            )
        if len(requests) == 2:
            return httpx.Response(
                400,
                json={"error": {"code": "context_length_exceeded", "message": "sensitive-body"}},
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Comando concluído."}}]}
        )

    provider = OpenAICompatible(
        Settings("https://test.invalid/v1", "test"), httpx.MockTransport(handle)
    )
    agent = Agent(
        Repository(tmp_path), provider, approve_command=lambda *args: approvals.append(args) or True
    )
    assert agent.ask("Execute a verificação.") == "Comando concluído."
    assert len(approvals) == 1
    assert len(requests) == 3
    assert agent.counter.count(requests[2]) < agent.counter.count(requests[1])
    assert (
        '"exit_code":0' in serialize(requests[2])
        or '"exit_code":0' in requests[2]["messages"][2]["content"]
    )
    assert "sensitive-body" not in serialize(requests[2])
    for request in requests:
        assert_protocol(request["messages"])
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["turns"][1]["error"]["type"] == "ContextLimitError"
    assert flow["turns"][1]["http_attempts"][0]["status_code"] == 400
    assert flow["status"] == "success"


def test_context_retry_is_bounded_and_fixed_base_is_reported(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(400, json={"error": {"code": "context_length_exceeded"}})

    provider = OpenAICompatible(
        Settings("https://test.invalid/v1", "test"), httpx.MockTransport(handle)
    )
    agent = Agent(Repository(tmp_path), provider)
    with pytest.raises(ModelError, match="não cabem|rejeitou"):
        agent.ask("Investigue.")
    assert len(requests) <= 3
    assert not agent.turns


@pytest.mark.parametrize(
    "status,body,context_error",
    [
        (400, {"error": {"code": "context_length_exceeded"}}, True),
        (413, {"error": {"message": "maximum context length is 4096 tokens"}}, True),
        (422, {"error": "the input length exceeds the context length"}, True),
        (400, {"error": {"message": "invalid tool schema"}}, False),
        (401, {"error": {"code": "context_length_exceeded"}}, False),
    ],
)
def test_provider_distinguishes_context_rejections_from_other_errors(status, body, context_error):
    provider = OpenAICompatible(
        Settings("https://test.invalid/v1", "test"),
        httpx.MockTransport(lambda _: httpx.Response(status, json=body)),
    )
    with pytest.raises(ContextLimitError if context_error else ModelError) as error:
        provider.complete([])
    assert isinstance(error.value, ContextLimitError) == context_error
    assert str(body) not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("context_window", 100),
        ("context_window", True),
        ("max_output_tokens", 0),
        ("max_output_tokens", 16000),
        ("token_encoding", "unknown"),
    ],
)
def test_context_settings_validate(field, value):
    with pytest.raises(ValueError):
        Settings("https://test.invalid/v1", "test", **{field: value})


def test_context_environment_and_flag_override(monkeypatch):
    monkeypatch.setenv("CODARO_CONTEXT_WINDOW", "8192")
    monkeypatch.setenv("CODARO_MAX_OUTPUT_TOKENS", "512")
    settings = Settings.from_env()
    assert settings.context_window == 8192 and settings.max_output_tokens == 512
    assert Settings.from_env(context_window=16384).context_window == 16384
    monkeypatch.setenv("CODARO_CONTEXT_WINDOW", "NaN")
    with pytest.raises(ValueError, match="inteiros"):
        Settings.from_env()


def test_identical_commands_do_not_execute_twice_after_compaction(tmp_path):
    approvals = []
    argv = [sys.executable, "-c", "print('done' * 1500)"]
    model = configured_model(
        [
            call("run_command", {"argv": argv}, "one"),
            call("run_command", {"argv": argv}, "two"),
            {"content": "Concluído."},
        ]
    )
    agent = Agent(
        Repository(tmp_path), model, approve_command=lambda *args: approvals.append(args) or True
    )
    agent.ask("Execute uma vez.")
    assert len(approvals) == 1
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    results = [entry["result"] for step in flow["turns"] for entry in step["tool_results"]]
    assert results[1]["reused_result"]
    assert results[1]["exit_code"] == 0


def test_compacted_read_no_longer_authorizes_an_edit(tmp_path):
    (tmp_path / "x.py").write_text("x = '" + "a" * 2900 + "'\n")
    old_text = (tmp_path / "x.py").read_text().strip()
    model = configured_model(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}, "read"),
            call("compact_context", {}, "compact"),
            call(
                "propose_edit",
                {"path": "x.py", "old_text": old_text, "new_text": "x = 'b'", "reason": "Ajuste."},
                "edit",
            ),
            {"content": "É necessário reler o código."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, allow_edits=True)
    agent.ask("Altere x.")
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["compactions"]
    assert not agent.edits.pending
    result = flow["turns"][2]["tool_results"][0]["result"]
    assert "novamente" in result["error"] or "espaço" in result["error"]
    assert (tmp_path / "x.py").read_text().strip() == old_text


def test_http_stream_context_error_before_output_can_be_recovered(tmp_path):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content='data: {"error":{"code":"context_length_exceeded"}}\n\n',
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    provider = OpenAICompatible(
        Settings("https://test.invalid/v1", "test"), httpx.MockTransport(handle)
    )
    agent = Agent(Repository(tmp_path), provider, history_budget=20000)
    agent.turns = [
        [{"role": "user", "content": "Anterior."}, {"role": "assistant", "content": "x" * 6000}]
    ]
    parts = []
    assert agent.ask("Continue.", on_delta=parts.append) == "OK"
    assert "".join(parts) == "OK"
    assert len(requests) == 2
    assert all(request["stream"] for request in requests)


def test_impossible_fixed_context_fails_before_contacting_server(tmp_path):
    model = configured_model([])
    model.settings = Settings("https://test.invalid/v1", "test", context_window=4096)
    agent = Agent(Repository(tmp_path), model)
    with pytest.raises(ModelError, match="não cabem"):
        agent.ask("x" * 8000)
    assert not model.requests


def test_overlapping_reads_send_only_new_lines_without_losing_citations(tmp_path):
    (tmp_path / "x.py").write_text("\n".join(f"x{i} = {i}" for i in range(70)))
    model = configured_model(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 40}, "first"),
            call("read_lines", {"path": "x.py", "start": 20, "end": 60}, "overlap"),
            call("read_lines", {"path": str(tmp_path / "x.py"), "start": 25, "end": 30}, "inside"),
            {"content": "Leituras concluídas."},
        ],
        window=16384,
    )
    Agent(Repository(tmp_path), model).ask("Leia os intervalos.")
    results = [
        json.loads(item["content"]) for item in model.requests[-1][0] if item["role"] == "tool"
    ]
    assert results[1]["start_line"] == 41
    assert results[1]["overlap_skipped"]["end_line"] == 40
    assert results[2]["already_read"]
    assert results[0]["start_line"] == 1 and results[0]["end_line"] == 40


def test_four_references_and_guidance_share_small_context_budget(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use pytest.\n" * 100)
    for i in range(4):
        (tmp_path / f"x{i}.py").write_text("x = 1\n" * 100)
    model = configured_model([{"content": "Referências lidas."}])
    agent = Agent(Repository(tmp_path), model)
    agent.ask("Leia @x0.py @x1.py @x2.py @x3.py")
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert len(flow["local_retrievals"]) == 5
    assert all("error" not in read["result"] for read in flow["local_retrievals"])
    assert agent.counter.count(flow["turns"][0]["request"]) <= agent.input_limit


@pytest.mark.parametrize("start,end", [(3, 1), (1, 161)])
def test_read_deduplication_preserves_invalid_interval_errors(tmp_path, start, end):
    (tmp_path / "x.py").write_text("x = 1\n" * 170)
    model = configured_model(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 160}, "first"),
            call("read_lines", {"path": "x.py", "start": start, "end": end}, "invalid"),
            {"content": "Intervalo inválido."},
        ]
    )
    Agent(Repository(tmp_path), model).ask("Leia x.")
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert "error" in flow["turns"][1]["tool_results"][0]["result"]


def test_configured_output_limit_matches_accounted_payload_and_wire_request(tmp_path):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    settings = Settings(
        "https://test.invalid/v1", "test", context_window=8192, max_output_tokens=512
    )
    agent = Agent(Repository(tmp_path), OpenAICompatible(settings, httpx.MockTransport(handle)))
    agent.ask("Investigue.")
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert requests[0] == flow["turns"][0]["request"]
    assert requests[0]["max_tokens"] == 512
    assert flow["turns"][0]["budget"]["input_token_limit"] == 8192 - 512 - 512
