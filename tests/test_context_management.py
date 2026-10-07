import json

import httpx
import pytest
from test_agent import FakeModel, call
from test_context import assert_protocol

from codaro.agent import Agent
from codaro.provider import OpenAICompatible, Settings, reported_context_window
from codaro.repository import Repository


@pytest.mark.parametrize(
    "message,expected",
    [
        ("maximum context length is 4096 tokens; requested 10000", 4096),
        ("Context window: 8,192 tokens", 8192),
        ("requested 20000 tokens", None),
        ("context window 9999999999999", None),
    ],
)
def test_context_limit_parser_does_not_confuse_requested_tokens(message, expected):
    assert reported_context_window(json.dumps({"error": {"message": message}})) == expected


def test_small_window_loads_tools_on_demand_and_reports_budget(tmp_path):
    model = FakeModel(
        [
            call("get_context_status", {}),
            call("request_tools", {"names": ["read_symbol"]}, "load"),
            {"content": "Pronto."},
        ]
    )
    model.settings = Settings("http://localhost/v1", "small", context_window=4096)
    agent = Agent(Repository(tmp_path), model, mode="execute")
    assert agent.ask("Consulte o contexto.") == "Pronto."
    initial = {tool["function"]["name"] for tool in model.requests[0][1]}
    final = {tool["function"]["name"] for tool in model.requests[-1][1]}
    assert "request_tools" in initial
    assert "read_symbol" not in initial and "read_symbol" in final
    status = next(item for item in model.requests[1][0] if item.get("name") == "get_context_status")
    assert json.loads(status["content"])["compact_tools"]
    for messages, _ in model.requests:
        assert_protocol(messages)


def test_explicit_compaction_keeps_protocol_and_execution_outcomes(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n" * 100)
    model = FakeModel(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 100}),
            call("compact_context", {}, "compact"),
            {"content": "Contexto liberado."},
        ]
    )
    model.settings = Settings("http://localhost/v1", "test", context_window=16384)
    agent = Agent(Repository(tmp_path), model)
    assert agent.ask("Leia e libere o contexto.") == "Contexto liberado."
    final = model.requests[-1][0]
    assert "source_removed" in str(final)
    assert not any(item.get("name") == "read_lines" for item in final)
    assert_protocol(final)
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert any(item["kind"] == "requested_context" for item in flow["compactions"])


def test_context_recovery_learns_budget_across_agent_restart(tmp_path):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "context_length_exceeded",
                        "message": "maximum context length is 4096 tokens",
                    }
                },
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    provider = OpenAICompatible(
        Settings("http://localhost/v1", "test"), httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider, mode="execute")
    assert agent.ask("Analise o contexto.") == "OK"
    assert len(requests) == 2
    limit = agent.adaptive_input_limit
    assert limit < agent.input_limit
    restarted = Agent(Repository(tmp_path), provider, mode="execute")
    assert restarted.ask("Continue.") == "OK"
    assert restarted.adaptive_input_limit == limit
    assert len(requests) == 3


def test_context_tool_cannot_enable_commands_in_ask_mode(tmp_path):
    model = FakeModel([call("request_tools", {"names": ["run_command"]}), {"content": "OK"}])
    model.settings = Settings("http://localhost/v1", "test")
    Agent(Repository(tmp_path), model, mode="ask").ask("Consulte.")
    result = next(item for item in model.requests[-1][0] if item.get("name") == "request_tools")
    assert "não disponível" in json.loads(result["content"])["error"]


def test_recovery_can_continue_after_more_than_two_context_rejections(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) <= 3:
            return httpx.Response(400, json={"error": {"code": "context_length_exceeded"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Recuperado."}}]})

    provider = OpenAICompatible(
        Settings("http://localhost/v1", "test", context_window=131072), httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider, history_budget=60000)
    agent.turns = [
        [{"role": "user", "content": f"Pergunta {i}"}, {"role": "assistant", "content": "x" * 8000}]
        for i in range(3)
    ]
    assert agent.ask("Continue.") == "Recuperado."
    assert len(requests) == 4
    sizes = [agent.counter.count(payload) for payload in requests]
    assert all(current < previous for previous, current in zip(sizes, sizes[1:], strict=False))
    for payload in requests:
        assert_protocol(payload["messages"])
