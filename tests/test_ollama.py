import json
import threading

import httpx
import pytest
from test_agent import FakeModel, call
from test_context import assert_protocol

from codaro.agent import Agent
from codaro.provider import (
    ContextLimitError,
    ModelError,
    RequestCancelled,
    Settings,
    create_provider,
)
from codaro.providers import ProviderStore
from codaro.repository import Repository


def provider(handler, window=131072):
    return create_provider(
        Settings(
            "http://localhost:11434/v1", "llama3.1:8b", api_style="ollama", context_window=window
        ),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("configured,expected", [("", 131072), ("num_ctx 8192", 8192)])
def test_ollama_metadata_controls_native_request_window(tmp_path, configured, expected):
    requests = []

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "parameters": configured,
                    "model_info": {
                        "general.architecture": "llama",
                        "llama.context_length": 131072,
                        "clip.context_length": 77,
                    },
                    "capabilities": ["tools"],
                },
            )
        assert request.url.path == "/api/chat"
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": "Olá!"},
            },
        )

    store = ProviderStore(tmp_path / "config", transport=httpx.MockTransport(handler))
    store.register("ollama", "")
    settings = store.select("ollama", "llama3.1:8b")
    assert settings.context_window == expected
    assert settings.api_style == "ollama"
    # Persisted fallback metadata is refreshed automatically on startup.
    store.mutate(
        lambda value: value["profiles"]["ollama"]["models"][0].update(
            context_window=None, context_source="fallback: 4.096 tokens"
        )
    )
    settings = store.active_settings()
    assert settings.context_window == expected
    assert (
        create_provider(settings, transport=store.transport).complete(
            [{"role": "user", "content": "Olá"}]
        )["content"]
        == "Olá!"
    )
    assert requests[0]["options"]["num_ctx"] == expected
    assert "max_tokens" not in requests[0]


def test_native_stream_separates_thinking_content_tools_and_reports_usage(tmp_path):
    events = [
        {"message": {"thinking": "Uma nota.", "content": ""}, "done": False},
        {"message": {"content": "Olá "}, "done": False},
        {"message": {"content": "mundo."}, "done": False},
        {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "list_files", "arguments": {"limit": 5}}}],
            },
            "done": False,
        },
        {"done": True, "done_reason": "stop", "prompt_eval_count": 23, "eval_count": 4},
    ]
    native = provider(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content="\n".join(json.dumps(event) for event in events),
        )
    )
    deltas, reasoning = [], []
    result = native.stream(
        [{"role": "user", "content": "Olá"}], on_delta=deltas.append, on_reasoning=reasoning.append
    )
    assert deltas == ["Olá ", "mundo."]
    assert reasoning == ["Uma nota."]
    assert result["content"] == "Olá mundo."
    assert json.loads(result["tool_calls"][0]["function"]["arguments"]) == {"limit": 5}
    assert result["tool_calls"][0]["id"]
    payload = native.wire_payload(
        {
            "model": "test",
            "messages": [
                {"role": "user", "content": "Olá"},
                result,
                {"role": "tool", "tool_call_id": result["tool_calls"][0]["id"], "content": "[]"},
            ],
        }
    )
    assert payload["messages"][-1]["tool_name"] == "list_files"
    assert isinstance(payload["messages"][1]["tool_calls"][0]["function"]["arguments"], dict)
    assert isinstance(result["tool_calls"][0]["function"]["arguments"], str)  # original untouched


def test_native_agent_usage_and_debug_are_preserved(tmp_path):
    events = [
        {"message": {"content": "Resposta."}, "done": False},
        {"done": True, "done_reason": "stop", "prompt_eval_count": 200, "eval_count": 8},
    ]
    native = provider(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content="\n".join(json.dumps(event) for event in events),
        )
    )
    agent = Agent(Repository(tmp_path), native, mode="ask")
    deltas = []
    assert agent.ask("Olá", on_delta=deltas.append) == "Resposta."
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    turn = flow["turns"][0]
    assert turn["request"]["options"]["num_ctx"] == 131072
    assert turn["budget"]["reported_prompt_tokens"] == 200
    assert len(turn["http_attempts"][0]["ndjson_events"]) == 2


@pytest.mark.parametrize(
    "body,error",
    [
        ({"message": {"content": "Parcial"}, "done": False}, ModelError),
        ({"error": "context length exceeded"}, ContextLimitError),
        ({"message": {"content": ""}, "done": True}, ModelError),
        ({"message": {"content": "Parcial"}, "done": True, "done_reason": "length"}, ModelError),
    ],
)
def test_native_incomplete_context_empty_or_truncated_answers_are_not_accepted(body, error):
    native = provider(
        lambda request: httpx.Response(
            200, headers={"content-type": "application/x-ndjson"}, content=json.dumps(body)
        )
    )
    with pytest.raises(error):
        native.stream([{"role": "user", "content": "Olá"}], on_delta=lambda text: None)


def test_native_stream_cancellation_stops_after_callback():
    events = [
        {"message": {"content": "Um"}, "done": False},
        {"message": {"content": "Dois"}, "done": False},
        {"done": True},
    ]
    native = provider(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content="\n".join(json.dumps(event) for event in events),
        )
    )
    cancelled = threading.Event()
    deltas = []

    def delta(text):
        deltas.append(text)
        cancelled.set()

    with pytest.raises(RequestCancelled):
        native.stream([{"role": "user", "content": "Olá"}], on_delta=delta, cancelled=cancelled)
    assert deltas == ["Um"]


def test_minimal_context_keeps_question_and_recovers_single_tool_at_a_time(tmp_path):
    (tmp_path / "README.md").write_text("Projeto de exemplo.\n" * 100)
    model = FakeModel(
        [
            call("read_lines", {"path": "README.md", "start": 1, "end": 100}),
            {"content": "Este é um projeto de exemplo."},
        ]
    )
    model.settings = Settings("http://localhost/v1", "small", context_window=4096)
    agent = Agent(Repository(tmp_path), model, mode="ask")
    # Simulate a server-calibrated smaller input budget without changing model settings.
    agent._calibration_key = ""  # restored calibration normally keys on provider/model/window
    original = agent.counter.count
    agent.counter.count = lambda payload: original(payload) * 2
    assert agent.ask("O que diz o README?") == "Este é um projeto de exemplo."
    for messages, tools in model.requests:
        assert_protocol(messages)
        assert any(
            item.get("role") == "user" and item["content"] == "O que diz o README?"
            for item in messages
        )
        if tools:
            assert "request_tools" in {tool["function"]["name"] for tool in tools}
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert any(item["kind"] == "minimal_tool_context" for item in flow["compactions"])


def test_native_http_context_rejection_recovers_without_repeating_tools(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(500, json={"error": "maximum context length is 4096 tokens"})
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": "Olá!"},
            },
        )

    agent = Agent(Repository(tmp_path), provider(handler), mode="ask")
    assert agent.ask("Olá") == "Olá!"
    assert len(requests) == 2
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["turns"][0]["error"]["type"] == "ContextLimitError"
    assert flow["status"] == "success"


def test_ui_minimum_context_preserves_question_without_technical_error(tmp_path):
    from test_tui import UIModel, run_ui, wait_ready
    from textual.widgets import Static

    from codaro.provider import ContextCapacityError
    from codaro.tui import CodaroApp, Prompt

    class LimitedModel(UIModel):
        def complete(self, messages, tools=None):
            raise ContextCapacityError(
                "Podemos seguir com uma parte menor. A conversa foi preservada."
            )

    app = CodaroApp(Agent(Repository(tmp_path), LimitedModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one(Prompt).value = "Olá"
            await pilot.press("enter")
            await wait_ready(app, pilot)
            notice = " ".join(str(item.render()) for item in app.query(".notice").results(Static))
            assert "preservada" in notice
            assert "Não foi possível concluir" not in notice
            assert "CODARO_CONTEXT_WINDOW" not in notice
            assert app.active_question == "Olá"
            assert not app.session_turns
            assert app.query_one("#retry-question")

    run_ui(scenario())


def test_native_insufficient_memory_shrinks_requested_window_automatically(tmp_path):
    windows = []

    def handler(request):
        window = json.loads(request.content)["options"]["num_ctx"]
        windows.append(window)
        if window > 16384:
            return httpx.Response(
                500, json={"error": "model requires more system memory than available"}
            )
        return httpx.Response(200, json={"done": True, "message": {"content": "Pronto."}})

    agent = Agent(Repository(tmp_path), provider(handler), mode="ask")
    assert agent.ask("Olá") == "Pronto."
    assert windows == [131072, 65536, 32768, 16384]
    assert agent.context_window == agent.provider.settings.context_window == 16384
    assert "memória" in agent.provider.settings.context_source
    assert agent.ask("Outra pergunta") == "Pronto."
    assert windows[-1] == 16384  # keeps the working window for this session


def test_native_minimum_window_memory_failure_is_friendly_and_bounded(tmp_path):
    from codaro.provider import ContextCapacityError

    windows = []

    def handler(request):
        windows.append(json.loads(request.content)["options"]["num_ctx"])
        return httpx.Response(500, json={"error": "out of memory"})

    agent = Agent(Repository(tmp_path), provider(handler, window=4096), mode="ask")
    with pytest.raises(ContextCapacityError, match="preservadas"):
        agent.ask("Olá")
    assert windows == [4096]
    assert not agent.turns


def test_memory_recovery_also_reduces_oversized_output_reservation(tmp_path):
    requests = []

    def handler(request):
        options = json.loads(request.content)["options"]
        requests.append(options)
        if options["num_ctx"] > 4096:
            return httpx.Response(500, json={"error": "out of memory"})
        return httpx.Response(200, json={"done": True, "message": {"content": "Pronto."}})

    settings = Settings(
        "http://localhost:11434/v1",
        "small",
        api_style="ollama",
        context_window=8192,
        max_output_tokens=6000,
    )
    native = create_provider(settings, transport=httpx.MockTransport(handler))
    agent = Agent(Repository(tmp_path), native, mode="ask")
    assert agent.ask("Olá") == "Pronto."
    assert requests[-1]["num_ctx"] == 4096
    assert requests[-1]["num_predict"] < 4096 - 512
