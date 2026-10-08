"""Regressions for M01–M13 from the model-flow audit."""

import json
import sys
import time
from dataclasses import replace

import httpx
import pytest
from test_agent import FakeModel, call
from test_streaming import chunk, encode

from codaro.agent import Agent
from codaro.anthropic import Anthropic
from codaro.provider import (
    ModelError,
    OpenAICompatible,
    Settings,
    create_provider,
    validate_message,
)
from codaro.repository import Repository
from codaro.trace import MAX_EVENT_BYTES, MAX_TRACE_BYTES, PromptFlow


class Model(FakeModel):
    def complete(self, messages, tools=None):
        self.requests.append((list(messages), tools))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def flow(root):
    return json.loads((root / ".codaro/prompt.json").read_text())


def test_m01_provider_switch_redacts_previous_and_current_secrets_everywhere(tmp_path):
    previous, current = "old-secret-KEY", "new-secret-KEY"
    model = Model([{"content": "Olá."}])
    model.settings = Settings("https://example.test/v1", "m", previous)
    agent = Agent(Repository(tmp_path), model)
    agent.ask("Olá")
    agent.turns[-1][-1]["content"] = f"{previous} {current}"
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer " + current
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Pronto."}, "finish_reason": "stop"}]}
        )

    agent.set_provider(
        OpenAICompatible(replace(model.settings, api_key=current), httpx.MockTransport(handler))
    )
    agent.ask(f"Conte {previous} {current}")
    assert bodies
    for text in [
        json.dumps(bodies),
        json.dumps(agent.turns),
        *[p.read_text() for p in (tmp_path / ".codaro").glob("*.json*")],
    ]:
        assert previous not in text and current not in text
    assert agent.memory.redact({previous: current}) == {"[REDACTED]": "[REDACTED]"}


def test_m02_all_user_constraints_survive_small_context(tmp_path):
    model = Model([{"content": "Olá."}])
    model.settings = Settings("https://example.test/v1", "m", context_window=4096)
    agent = Agent(Repository(tmp_path), model, mode="ask")
    for i in range(8):
        agent.memory.remember("constraint", f"RULE_{i} " + "x" * 270)
    agent.ask("Olá")
    sent = json.dumps(model.requests[0][0])
    assert all(f"RULE_{i}" in sent for i in range(8))


def test_m03_full_root_and_scoped_guidance_are_preserved(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Root rule\n" + "style\n" * 85 + "LAST_RULE\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/AGENTS.md").write_text("SCOPED_RULE\n")
    (tmp_path / "src/app.py").write_text("x = 1\n")
    model = Model(
        [call("read_lines", {"path": "src/app.py", "start": 1, "end": 1}), {"content": "x vale 1."}]
    )
    agent = Agent(Repository(tmp_path), model, mode="ask")
    agent.ask("Leia src/app.py")
    assert "LAST_RULE" in model.requests[0][0][0]["content"]
    assert "SCOPED_RULE" in model.requests[1][0][0]["content"]


def test_m04_implementation_cannot_complete_without_changes(tmp_path):
    (tmp_path / "calc.py").write_text("x = 1\n")
    agent = Agent(
        Repository(tmp_path), Model([{"content": "Implementação concluída."}]), mode="execute"
    )
    answer = agent.ask("Corrija calc.py para x valer 2.")
    assert "não foi concluída" in answer
    assert agent.tasks.current()["state"] == "blocked"
    assert not agent.tasks.validation_ready()
    assert (tmp_path / "calc.py").read_text() == "x = 1\n"


def test_m04_already_correct_code_requires_reads_and_real_validation(tmp_path):
    (tmp_path / "calc.py").write_text("x = 2\n")
    model = Model(
        [
            call("read_lines", {"path": "calc.py", "start": 1, "end": 1}),
            call(
                "run_command",
                {
                    "argv": [sys.executable, "-c", "import calc; assert calc.x == 2"],
                    "purpose": "validation",
                },
            ),
            call(
                "finish_task",
                {
                    "status": "completed",
                    "summary": "Já correto e validado.",
                    "verified_no_change": True,
                },
            ),
            {"content": "Já correto e validado."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, mode="execute", approve_command=lambda *_: True)
    assert agent.ask("Corrija calc.py para x valer 2.") == "Já correto e validado."
    assert agent.tasks.current()["state"] == "completed"


def test_m05_stream_stops_at_task_deadline_and_closes_transport(tmp_path):
    class Slow(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            for _ in range(6):
                time.sleep(0.25)
                yield encode([chunk({"content": "x"})], done=False)

        def close(self):
            self.closed = True

    stream = Slow()
    provider = OpenAICompatible(
        Settings("https://example.test/v1", "m"),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ),
    )
    agent = Agent(Repository(tmp_path), provider, max_seconds=1)
    started = time.monotonic()
    deltas = []
    with pytest.raises(ModelError, match="Prazo"):
        agent.ask("Olá", on_delta=lambda _: deltas.append(time.monotonic() - started))
    assert stream.closed and deltas and max(deltas) < 1
    assert time.monotonic() - started < 1.5
    assert flow(tmp_path)["status"] == "error"


@pytest.mark.parametrize("ndjson", [True, False])
def test_m06_m10_native_oom_recovers_and_persists_viable_window(tmp_path, ndjson):
    windows = []

    def handler(request):
        window = json.loads(request.content)["options"]["num_ctx"]
        windows.append(window)
        if window > 16384:
            error = {"error": "out of memory"}
            return (
                httpx.Response(
                    200,
                    text=json.dumps(error) + "\n",
                    headers={"content-type": "application/x-ndjson"},
                )
                if ndjson
                else httpx.Response(200, json=error)
            )
        return httpx.Response(
            200, json={"message": {"content": "Olá."}, "done": True, "done_reason": "stop"}
        )

    settings = Settings(
        "http://localhost:11434", "llama3.1:8b", api_style="ollama", context_window=131072
    )

    def new_agent():
        return Agent(
            Repository(tmp_path), create_provider(settings, transport=httpx.MockTransport(handler))
        )

    assert new_agent().ask("Olá") == "Olá."
    assert windows == [131072, 65536, 32768, 16384]
    second = new_agent()
    assert second.ask("Olá") == "Olá."
    assert windows[-1] == 16384 and len(windows) == 5
    second.reset_calibration()
    assert second.context_window == 131072
    assert second.ask("Olá") == "Olá."
    assert windows[-4:] == [131072, 65536, 32768, 16384]


def test_m07_unadvertised_tool_is_returned_as_error_without_execution(tmp_path):
    model = Model([call("get_repository_info", {}), {"content": "Não disponível."}])
    model.settings = Settings("https://example.test/v1", "m", context_window=4096)
    agent = Agent(Repository(tmp_path), model, mode="execute")
    agent.ask("Consulte o diretório")
    definitions = {tool["function"]["name"] for tool in model.requests[0][1]}
    assert "get_repository_info" not in definitions
    result = flow(tmp_path)["turns"][0]["tool_results"][0]["result"]
    assert "não anunciada" in result["error"]
    assert "root" not in result


@pytest.mark.parametrize("style", ["openai", "anthropic", "ollama"])
def test_m08_9000_character_tool_arguments_supported_by_all_transports(style):
    args = {
        "reason": "Criar arquivo",
        "operations": [{"kind": "create", "path": "x.py", "content": "#" + "x" * 8999}],
    }
    message = call("apply_changes", args)
    validate_message(message)
    if style == "anthropic":
        body = {
            "content": [{"type": "tool_use", "id": "c", "name": "apply_changes", "input": args}],
            "stop_reason": "tool_use",
        }
        provider_class = Anthropic
    elif style == "ollama":
        body = {
            "message": {"tool_calls": [{"function": {"name": "apply_changes", "arguments": args}}]},
            "done": True,
            "done_reason": "stop",
        }
        provider_class = create_provider
    else:
        body = {"choices": [{"message": message, "finish_reason": "tool_calls"}]}
        provider_class = OpenAICompatible
    provider = provider_class(
        Settings("https://example.test", "m", api_style=style),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    result = provider.complete([{"role": "user", "content": "Crie"}])
    assert json.loads(result["tool_calls"][0]["function"]["arguments"]) == args


def test_m09_failed_turn_and_full_tool_result_can_be_recovered_after_next_run(tmp_path):
    excerpt = "x" * 3000 + "RECOVER_LAST_DETAIL"
    (tmp_path / "x.py").write_text(excerpt)
    model = Model(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}),
            ModelError("Falhou"),
            {"content": "Olá."},
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    with pytest.raises(ModelError):
        agent.ask("FAILED_REQUEST")
    failed = flow(tmp_path)["run_id"]
    agent.ask("Olá")
    assert flow(tmp_path)["run_id"] != failed
    assert agent.memory.search("FAILED_REQUEST")["results"][0]["turn_id"] == failed
    pages = []
    offset = 0
    while True:
        page = agent.memory.read(failed, offset, 4000)
        pages.append(page["text"])
        offset = page["next_offset"]
        if offset is None:
            break
    historical = json.loads("".join(pages))
    assert historical["archive_available"]
    assert "RECOVER_LAST_DETAIL" in json.dumps(historical["historical_tool_results"])
    assert historical["source"] == "historical_not_current_evidence"


def test_m11_stream_usage_negotiates_unsupported_option_once():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if "stream_options" in payload:
            return httpx.Response(400, json={"error": "unsupported stream_options"})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=encode([chunk({"content": "Olá."}), chunk(reason="stop")]),
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "m", include_stream_usage=True),
        httpx.MockTransport(handler),
    )
    for _ in range(2):
        assert provider.stream([], on_delta=lambda _: None)["content"] == "Olá."
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert len(requests) == 3 and all("stream_options" not in p for p in requests[1:])


def test_m12_truncated_tool_json_is_not_executed_and_short_response_recovers(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            events = [
                chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c",
                                "type": "function",
                                "function": {
                                    "name": "apply_changes",
                                    "arguments": '{"operations":[',
                                },
                            }
                        ]
                    }
                ),
                chunk(reason="length"),
            ]
        else:
            events = [chunk({"content": "Resposta curta."}), chunk(reason="stop")]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=encode(events)
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "m"), httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider)
    assert agent.ask("Olá", on_delta=lambda _: None) == "Resposta curta."
    assert len(requests) == 2
    assert "truncada" in requests[-1]["messages"][0]["content"]
    assert not any(turn["tool_results"] for turn in flow(tmp_path)["turns"])


def test_m13_incremental_trace_is_bounded_and_rotates_archives(tmp_path):
    settings = Settings("https://example.test/v1", "m", "trace-secret")
    trace = PromptFlow(tmp_path, "Olá", settings, allow_edits=False, limits={})
    trace.add_turn({"messages": [], "stream": True}, {})
    trace.turn["http_attempts"].append({})
    for _ in range(4):
        trace.capture("sse", {"content": "x" * 800_000 + "trace-secret"})
        trace.checkpoint()
    assert trace.path.stat().st_size < MAX_TRACE_BYTES
    assert trace.archive_path.stat().st_size < MAX_EVENT_BYTES
    assert "trace-secret" not in trace.archive_path.read_text()
    assert trace.turn["http_attempts"][0]["events_in_archive"]
    trace.finish("success", answer="Olá")
    for _ in range(21):
        other = PromptFlow(tmp_path, "Outra", settings, allow_edits=False, limits={})
        other.finish("success", answer="Olá")
    assert len(list((tmp_path / ".codaro").glob("run-*.jsonl"))) <= 20


def test_m03_new_scoped_rules_require_reconsidering_a_mutation(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/AGENTS.md").write_text("Use type hints.\n")
    args = {
        "reason": "Create",
        "operations": [{"kind": "create", "path": "src/app.py", "content": "x = 1\n"}],
    }
    model = Model([call("apply_changes", args), {"content": "Preciso reavaliar."}])
    agent = Agent(
        Repository(tmp_path), model, mode="execute", approve_edit=lambda *_: pytest.fail()
    )
    agent.ask("Analise as alterações possíveis.")
    assert not (tmp_path / "src/app.py").exists()
    assert "Use type hints" in model.requests[1][0][0]["content"]
    result = flow(tmp_path)["turns"][0]["tool_results"][0]["result"]
    assert "Reavalie" in result["error"]


def test_m07_closed_task_rejects_later_mutations(tmp_path):
    model = Model(
        [
            call("finish_task", {"status": "completed", "summary": "Consulta concluída."}),
            call(
                "apply_changes",
                {
                    "reason": "Create",
                    "operations": [{"kind": "create", "path": "x.py", "content": "x = 1\n"}],
                },
            ),
            {"content": "Consulta concluída."},
        ]
    )
    agent = Agent(
        Repository(tmp_path), model, mode="execute", approve_edit=lambda *_: pytest.fail()
    )
    agent.ask("Explique o projeto.")
    assert not (tmp_path / "x.py").exists()
    assert agent.tasks.current()["state"] == "completed"
    assert "finalizada" in flow(tmp_path)["turns"][1]["tool_results"][0]["result"]["error"]


def test_m11_usage_is_requested_and_calibrates_real_wire_payload(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        events = [
            chunk({"content": "Olá."}),
            chunk(reason="stop"),
            {"choices": [], "usage": {"prompt_tokens": 5000, "completion_tokens": 3}},
        ]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=encode(events)
        )

    provider = OpenAICompatible(
        Settings("https://api.openai.com/v1", "m"), httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider)
    assert agent.ask("Olá", on_delta=lambda _: None) == "Olá."
    assert requests[0]["stream_options"] == {"include_usage": True}
    turn = flow(tmp_path)["turns"][0]
    assert turn["request"] == turn["http_attempts"][0]["http_request"] == requests[0]
    assert turn["budget"]["reported_prompt_tokens"] == 5000
    assert agent.counter.samples


def test_m12_output_recovery_is_bounded_and_keeps_failure_history(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Parcial"}, "finish_reason": "length"}]}
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "m"), httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider)
    with pytest.raises(ModelError, match="progresso foi salvo"):
        agent.ask("OUTPUT_RETRY_REQUEST")
    assert len(requests) == 3
    assert agent.memory.search("OUTPUT_RETRY_REQUEST")["results"]
    assert agent.turns == []


def test_m13_archive_cap_is_explicit_when_events_are_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr("codaro.trace.MAX_EVENT_BYTES", 4000)
    trace = PromptFlow(
        tmp_path, "Olá", Settings("https://example.test", "m"), allow_edits=False, limits={}
    )
    trace.append_event("sse", {"text": "x" * 3000})
    trace.append_event("sse", {"text": "x" * 3000})
    trace.finish("success", answer="Olá")
    assert trace.archive_path.stat().st_size <= 4000
    assert not flow(tmp_path)["archive"]["complete"]
    assert flow(tmp_path)["archive"]["omitted_events"] >= 1


def test_m10_expired_calibration_is_not_reused(tmp_path):
    import hashlib

    settings = Settings(
        "http://localhost:11434", "llama", api_style="ollama", context_window=131072
    )
    model = Model([{"content": "Olá."}])
    model.settings = settings
    agent = Agent(Repository(tmp_path), model)
    key = hashlib.sha256(
        json.dumps(
            [
                settings.base_url,
                settings.model,
                settings.token_encoding,
                settings.api_style,
                settings.context_window,
                settings.max_output_tokens,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    agent.memory.calibration(key, {"effective_window": 16384, "learned_at": time.time() - 90000})
    assert agent.ask("Olá") == "Olá."
    assert agent._calibration_key == key
    assert agent.context_window == 131072


def test_m10_recalibration_is_accessible_in_tui(tmp_path):
    from test_tui import UIModel, run_ui

    from codaro.tui import CodaroApp

    agent = Agent(Repository(tmp_path), UIModel())
    original = agent.adaptive_input_limit
    agent.adaptive_input_limit = 1000

    async def scenario():
        app = CodaroApp(agent)
        async with app.run_test(size=(120, 35)) as pilot:
            await app.local_command("/recalibrate")
            await pilot.pause()
            assert agent.adaptive_input_limit == original
            assert "Calibração removida" in str(app.query(".question").last().render())

    run_ui(scenario())


def test_m11_option_negotiation_has_room_after_transient_retries(monkeypatch):
    monkeypatch.setattr("codaro.provider.time.sleep", lambda _: None)
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        if len(attempts) < 3:
            return httpx.Response(503)
        if "stream_options" in attempts[-1]:
            return httpx.Response(400, json={"error": "unsupported stream_options"})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=encode([chunk({"content": "Olá."}), chunk(reason="stop")]),
        )

    provider = OpenAICompatible(
        Settings("https://api.openai.com/v1", "m"), httpx.MockTransport(handler)
    )
    assert provider.stream([], on_delta=lambda _: None)["content"] == "Olá."
    assert len(attempts) == 4 and "stream_options" not in attempts[-1]
