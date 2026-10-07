import json

import httpx
import pytest

from codaro.agent import Agent
from codaro.provider import ModelError, Settings, create_provider
from codaro.providers import ProviderStore
from codaro.repository import Repository


def test_anthropic_catalog_and_native_tool_loop(tmp_path):
    calls = []

    def handler(request):
        assert request.headers["x-api-key"] == "anthropic-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "claude-test",
                            "display_name": "Claude Test",
                            "max_input_tokens": 200000,
                            "max_tokens": 8192,
                        }
                    ]
                },
            )
        assert request.url.path == "/v1/messages"
        payload = json.loads(request.content)
        calls.append(payload)
        assert "system" in payload and all(item["role"] != "system" for item in payload["messages"])
        assert all("input_schema" in tool and "function" not in tool for tool in payload["tools"])
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"type": "tool_use", "id": "tool-1", "name": "list_files", "input": {}}
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 100},
                },
            )
        result = payload["messages"][-1]["content"][0]
        assert result["type"] == "tool_result" and result["tool_use_id"] == "tool-1"
        assert json.loads(result["content"])["files"] == ["main.py"]
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Arquivo: main.py."}],
                "stop_reason": "end_turn",
            },
        )

    transport = httpx.MockTransport(handler)
    store = ProviderStore(tmp_path / "config", transport=transport)
    store.register("anthropic", "anthropic-key")
    settings = store.select("anthropic", "claude-test")
    assert settings.context_window == 200000 and settings.api_style == "anthropic"
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    agent = Agent(Repository(repo), create_provider(settings, transport=transport))
    assert agent.ask("Quais arquivos existem?") == "Arquivo: main.py."
    trace = json.loads((repo / ".codaro/prompt.json").read_text())
    assert trace["turns"][0]["request"]["system"] == calls[0]["system"]
    assert "anthropic-key" not in json.dumps(trace)


def sse(events):
    return "".join(
        "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events
    ).encode()


def test_anthropic_streams_text_and_thinking_separately():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Nota provisória"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Olá"}},
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": ", mundo!"},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 3},
        },
        {"type": "message_stop"},
    ]
    provider = create_provider(
        Settings("https://anthropic.test/v1", "claude-test", "key", api_style="anthropic"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=sse(events), headers={"content-type": "text/event-stream"}
            )
        ),
    )
    content, reasoning = [], []
    result = provider.stream(
        [{"role": "user", "content": "Oi"}], on_delta=content.append, on_reasoning=reasoning.append
    )
    assert content == ["Olá", ", mundo!"]
    assert reasoning == ["Nota provisória"] and result["content"] == "Olá, mundo!"


@pytest.mark.parametrize("reason", ["max_tokens", "pause_turn", None])
def test_anthropic_incomplete_answer_is_not_accepted(reason):
    provider = create_provider(
        Settings("https://anthropic.test/v1", "claude-test", "key", api_style="anthropic"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"content": [{"type": "text", "text": "parcial"}], "stop_reason": reason}
            )
        ),
    )
    with pytest.raises(ModelError):
        provider.complete([{"role": "user", "content": "Oi"}])


@pytest.mark.parametrize("close_block", [True, False])
def test_anthropic_stream_reassembles_native_tool_arguments(close_block):
    events = [
        {"type": "message_start", "message": {"usage": {}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "tool-stream",
                "name": "list_files",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"limit":'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "10}"},
        },
    ]
    if close_block:
        events.append({"type": "content_block_stop", "index": 0})
    events.extend(
        [{"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"}]
    )
    provider = create_provider(
        Settings("https://anthropic.test/v1", "claude-test", "key", api_style="anthropic"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=sse(events), headers={"content-type": "text/event-stream"}
            )
        ),
    )
    if not close_block:
        with pytest.raises(ModelError, match="interrompida"):
            provider.stream([{"role": "user", "content": "Liste."}])
    else:
        call = provider.stream([{"role": "user", "content": "Liste."}])["tool_calls"][0]
        assert call["id"] == "tool-stream" and call["function"]["name"] == "list_files"
        assert json.loads(call["function"]["arguments"]) == {"limit": 10}


def test_anthropic_doctor_probe_completes_native_tool_result_cycle():
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            assert body["tools"][0]["name"] == "codaro_probe"
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"type": "tool_use", "id": "probe", "name": "codaro_probe", "input": {}}
                    ],
                    "stop_reason": "tool_use",
                },
            )
        result = body["messages"][-1]["content"][0]
        assert result["tool_use_id"] == "probe"
        marker = json.loads(result["content"])["probe_result"]
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": marker}], "stop_reason": "end_turn"}
        )

    provider = create_provider(
        Settings("https://anthropic.test/v1", "claude-test", "key", api_style="anthropic"),
        transport=httpx.MockTransport(handler),
    )
    provider.check_tool_calling()
    assert len(requests) == 2
