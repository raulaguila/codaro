import json

import httpx
import pytest

from codaro.agent import Agent
from codaro.provider import OutputLimitError, Settings, build_payload, create_provider
from codaro.repository import Repository


@pytest.mark.parametrize("style", ["openai", "ollama", "anthropic"])
@pytest.mark.parametrize("manual", [None, 512])
def test_wire_output_defaults_and_manual_override(style, manual):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if style == "ollama":
            body = {"done": True, "done_reason": "stop", "message": {"content": "OK"}}
        elif style == "anthropic":
            body = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "OK"}]}
        else:
            body = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=body)

    settings = Settings(
        "http://localhost:11434/v1",
        "model",
        api_style=style,
        max_output_tokens=manual,
        context_window=32768,
        model_max_output_tokens=6144,
    )
    provider = create_provider(settings, transport=httpx.MockTransport(handler))
    assert provider.complete([{"role": "user", "content": "Olá"}])["content"] == "OK"
    wire = requests[0]
    if style == "ollama":
        assert wire["options"].get("num_predict") == manual
        assert ("num_predict" in wire["options"]) == (manual is not None)
    elif style == "anthropic":
        assert wire["max_tokens"] == (manual or 6144)
    else:
        assert wire.get("max_tokens") == manual
        assert ("max_tokens" in wire) == (manual is not None)


def test_environment_automatic_and_manual_modes(monkeypatch, tmp_path):
    monkeypatch.setenv("CODARO_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODARO_MODEL", "test")
    monkeypatch.delenv("CODARO_MAX_OUTPUT_TOKENS", raising=False)
    assert Settings.from_env().max_output_tokens is None
    monkeypatch.setenv("CODARO_MAX_OUTPUT_TOKENS", "auto")
    assert Settings.from_env().max_output_tokens is None
    monkeypatch.setenv("CODARO_MAX_OUTPUT_TOKENS", "2048")
    assert Settings.from_env().max_output_tokens == 2048
    assert "max_tokens" not in build_payload("test", [], None)


@pytest.mark.parametrize("streaming", [False, True])
def test_text_truncation_continues_without_tools_and_preserves_complete_answer(tmp_path, streaming):
    requests = []
    deltas = []

    def handler(request):
        requests.append(json.loads(request.content))
        text, reason = (
            ("Primeira parte. ", "length") if len(requests) == 1 else ("Conclusão.", "stop")
        )
        if streaming:
            events = [
                {"choices": [{"delta": {"content": text}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": reason}]},
            ]
            body = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": text}, "finish_reason": reason}
                ]
            },
        )

    provider = create_provider(
        Settings("https://model.test/v1", "test"), transport=httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider, mode="ask")
    answer = agent.ask("Olá", on_delta=deltas.append if streaming else None)
    assert answer == "Primeira parte. Conclusão."
    assert all("max_tokens" not in r for r in requests)
    assert "tools" not in requests[1]
    assert "Primeira parte. " in [m["content"] for m in requests[1]["messages"]]
    if streaming:
        assert "".join(deltas) == answer
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["turns"][0]["outcome"] == "output_continuation"
    assert flow["status"] == "success"
    assert flow["limits"]["output_tokens"] is None
    assert flow["limits"]["output_reserve_tokens"] == 4096


def test_repeated_text_truncation_returns_marked_partial_instead_of_losing_answer(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": f"Parte {len(requests)}. "}, "finish_reason": "length"}
                ]
            },
        )

    provider = create_provider(
        Settings("https://model.test/v1", "test"), transport=httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider, mode="ask")
    answer = agent.ask("Olá")
    assert answer.startswith("Parte 1. Parte 2. Parte 3.")
    assert "Resposta parcial" in answer
    assert len(requests) == 3
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["status"] == "incomplete"
    assert agent.turns[-1][-1]["content"] == answer


def test_continuation_rejects_native_tools_even_after_text_truncation(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            message = {"content": "Texto parcial."}
        else:
            message = {
                "content": None,
                "tool_calls": [
                    {
                        "id": "x",
                        "type": "function",
                        "function": {
                            "name": "apply_changes",
                            "arguments": json.dumps(
                                {
                                    "operations": [
                                        {"kind": "create", "path": "x.py", "content": "x=1"}
                                    ]
                                }
                            ),
                        },
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": message,
                        "finish_reason": "length" if len(requests) == 1 else "tool_calls",
                    }
                ]
            },
        )

    provider = create_provider(
        Settings("https://model.test/v1", "test"), transport=httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider, mode="ask")
    from codaro.provider import ModelError

    with pytest.raises(ModelError, match="Ferramentas não são permitidas"):
        agent.ask("Olá")
    assert not (tmp_path / "x.py").exists()


def test_manual_limit_is_clamped_to_model_metadata():
    settings = Settings(
        "https://model.test", "model", max_output_tokens=2048, model_max_output_tokens=512
    )
    assert settings.max_output_tokens == settings.output_reserve == 512


def test_ollama_stream_preserves_terminal_fragment_and_continues(tmp_path):
    requests, deltas = [], []

    def handler(request):
        requests.append(json.loads(request.content))
        events = (
            [
                {"message": {"content": "A"}, "done": False},
                {"message": {"content": "B"}, "done": True, "done_reason": "length"},
            ]
            if len(requests) == 1
            else [
                {"message": {"content": "C"}, "done": True, "done_reason": "stop"},
            ]
        )
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content="\n".join(json.dumps(e) for e in events) + "\n",
        )

    provider = create_provider(
        Settings("http://localhost:11434", "model", api_style="ollama"),
        transport=httpx.MockTransport(handler),
    )
    assert (
        Agent(Repository(tmp_path), provider, mode="ask").ask("Olá", on_delta=deltas.append)
        == "ABC"
    )
    assert "".join(deltas) == "ABC"
    assert all("num_predict" not in r["options"] for r in requests)
    assert "tools" not in requests[1]


def test_anthropic_truncated_tool_json_is_not_parsed_or_preserved_as_text():
    events = [
        {"type": "message_start", "message": {"usage": {}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "x", "name": "apply_changes", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"operations":['},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {}},
        {"type": "message_stop"},
    ]
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    provider = create_provider(
        Settings("https://anthropic.test", "model", api_style="anthropic"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=body
            )
        ),
    )
    with pytest.raises(OutputLimitError) as exc:
        provider.stream([], on_delta=lambda _: None)
    assert exc.value.partial_text == ""


def test_continuation_keeps_request_tail_bounded_and_partial_notice_fits(tmp_path):
    from codaro.provider import MAX_MESSAGE_CHARS

    requests = []
    parts = ["a" * 7900, "b" * 7900, "c" * 100]

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": parts[len(requests) - 1]}, "finish_reason": "length"}
                ]
            },
        )

    provider = create_provider(
        Settings("https://model.test", "model"), transport=httpx.MockTransport(handler)
    )
    answer = Agent(Repository(tmp_path), provider, mode="ask").ask("Olá")
    assert answer.startswith(parts[0])
    assert "Resposta parcial" in answer
    assert len(answer) <= MAX_MESSAGE_CHARS
    for request in requests[1:]:
        partial_messages = [m for m in request["messages"] if m["role"] == "assistant"]
        assert len(partial_messages) == 1
        assert len(partial_messages[0]["content"]) <= 2400


def test_user_continue_after_partial_consultation_preserves_pending_implementation(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "Mais informações."},
                        "finish_reason": "length" if len(calls) <= 3 else "stop",
                    }
                ]
            },
        )

    provider = create_provider(
        Settings("https://model.test", "model"), transport=httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider, mode="execute")
    agent.tasks.start("Implemente uma funcionalidade")
    agent.tasks.update(lambda task: task.update(revision=1, state="blocked"))
    previous = agent.tasks.current()
    assert "Resposta parcial" in agent.ask("O que pode me falar sobre o projeto atual?")
    assert agent.ask("Continue") == "Mais informações."
    assert agent.tasks.current() == previous
    assert not {"apply_changes", "finish_task", "run_command"}.intersection(
        tool["function"]["name"] for tool in calls[-1].get("tools", [])
    )


def test_continuation_with_explicit_mutation_is_not_a_consultation():
    from codaro.agent import is_information_request

    assert is_information_request("Continue a explicação")
    assert not is_information_request("Continue a explicação e implemente o módulo")
    assert not is_information_request("Continue a tarefa de implementação")
