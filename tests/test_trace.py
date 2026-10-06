import json
import os
import threading

import httpx
import pytest
from test_agent import FakeModel, call
from test_streaming import chunk, encode

from codaro.agent import Agent
from codaro.provider import ModelError, OpenAICompatible, RequestCancelled, Settings
from codaro.repository import Repository
from codaro.trace import PromptFlow, atomic_write, current_flow


def dump(root):
    return json.loads((root / ".codaro/prompt.json").read_text())


def test_last_flow_records_exact_requests_tools_and_overwrites_previous(tmp_path):
    (tmp_path / "auth.py").write_text("def can_edit(user): return user.is_admin\n")
    model = FakeModel(
        [
            call("list_files", {"limit": "10", "offset": "0"}),
            {"content": "Há um arquivo: auth.py."},
            {"content": "Segunda resposta."},
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    agent.ask("Quais arquivos existem?")
    first = dump(tmp_path)
    assert first["status"] == "success"
    assert first["repository_root"] == str(tmp_path)
    assert first["final_answer"] == "Há um arquivo: auth.py."
    assert len(first["turns"]) == 2
    turn = first["turns"][0]
    assert turn["request"]["messages"] == model.requests[0][0]
    assert turn["request"]["tools"] == model.requests[0][1]
    assert json.loads(turn["response"]["tool_calls"][0]["function"]["arguments"])["limit"] == "10"
    tool = turn["tool_results"][0]
    assert tool["normalized_arguments"] == {"limit": 10, "offset": 0}
    assert tool["result"]["files"] == ["auth.py"]
    assert tool["message"]["tool_call_id"] == "call-1"
    assert tool["message"]["name"] == "list_files"
    assert tool["message"] in first["turns"][1]["request"]["messages"]
    assert first["duration_ms"] >= 0
    assert any(item["kind"] == "tool_end" for item in first["events"])
    agent.ask("Explique novamente.")
    second = dump(tmp_path)
    assert first["run_id"] != second["run_id"]
    assert len(second["turns"]) == 1
    assert second["final_answer"] == "Segunda resposta."
    assert (tmp_path / ".codaro/prompt.json").stat().st_mode & 0o777 == 0o600
    assert current_flow.get() is None


def test_malformed_model_response_and_tool_arguments_are_recorded(tmp_path):
    invalid = call("list_files", {})
    invalid["tool_calls"][0]["function"]["arguments"] = "[" * 1500 + "]" * 1500
    agent = Agent(Repository(tmp_path), FakeModel([invalid, {"content": []}]))
    with pytest.raises(ModelError):
        agent.ask("Liste arquivos.")
    flow = dump(tmp_path)
    assert flow["status"] == "error"
    assert flow["error"]["type"] == "ModelError"
    assert flow["turns"][0]["tool_results"][0]["result"]["error"]
    assert flow["turns"][1]["response"] == {"content": []}
    assert "final_answer" not in flow
    assert agent.turns == []


def test_cancelled_before_request_is_saved(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    agent = Agent(Repository(tmp_path), FakeModel([]))
    with pytest.raises(RequestCancelled):
        agent.ask("Investigue.", cancelled=cancelled)
    assert dump(tmp_path)["status"] == "cancelled"
    assert dump(tmp_path)["turns"] == []


def test_stream_cancellation_keeps_raw_events_without_saving_answer(tmp_path):
    raw = encode([chunk({"content": "Resposta parcial"}), chunk(reason="stop")])
    provider = OpenAICompatible(
        Settings("https://example.test/v1", "model"),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=raw, headers={"content-type": "text/event-stream"}
            )
        ),
    )
    cancelled = threading.Event()
    agent = Agent(Repository(tmp_path), provider)
    with pytest.raises(RequestCancelled):
        agent.ask("Investigue.", on_delta=lambda _: cancelled.set(), cancelled=cancelled)
    flow = dump(tmp_path)
    assert flow["status"] == "cancelled"
    assert "Resposta parcial" in flow["turns"][0]["http_attempts"][0]["sse_events"][0]
    assert "final_answer" not in flow
    assert not agent.turns


def test_http_retries_usage_and_key_redaction(tmp_path, monkeypatch):
    monkeypatch.setattr("codaro.provider.time.sleep", lambda _: None)
    requests = []
    key = "private-key-do-not-write"

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + key
        if len(requests) == 1:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": key + " OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 123},
            },
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "model", key), httpx.MockTransport(handler)
    )
    agent = Agent(Repository(tmp_path), provider)
    agent.ask("Pergunta " + key)
    text = (tmp_path / ".codaro/prompt.json").read_text()
    assert key not in text
    assert "Authorization" not in text
    assert "[REDACTED]" in text
    attempts = dump(tmp_path)["turns"][0]["http_attempts"]
    assert [item["status_code"] for item in attempts] == [503, 200]
    assert attempts[0]["error_body"] == "temporarily unavailable"
    assert attempts[-1]["usage"] == {"prompt_tokens": 123}
    assert attempts[-1]["finish_reason"] == "stop"
    assert "response_body" in attempts[-1]


def test_http_error_body_is_bounded_and_never_echoed_to_user(tmp_path):
    provider = OpenAICompatible(
        Settings("https://example.test/v1", "model", "api-secret"),
        httpx.MockTransport(
            lambda _: httpx.Response(
                401, text="api-secret sensitive-server-details " + "x" * 100_000
            )
        ),
    )
    with pytest.raises(ModelError) as failure:
        Agent(Repository(tmp_path), provider).ask("Investigue.")
    assert "sensitive-server-details" not in str(failure.value)
    flow = dump(tmp_path)
    body = flow["turns"][0]["http_attempts"][0]["error_body"]
    assert body.startswith("[REDACTED] sensitive-server-details")
    assert len(body) <= 64_010
    assert "api-secret" not in json.dumps(flow)


def test_stream_records_reasoning_finish_and_usage_without_displaying_reasoning(tmp_path):
    raw = encode(
        [
            chunk({"reasoning_content": "internal planning"}),
            chunk({"content": "Resposta final."}),
            chunk(reason="stop"),
            {"choices": [], "usage": {"completion_tokens": 17}},
        ]
    )
    provider = OpenAICompatible(
        Settings("https://example.test/v1", "model"),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=raw, headers={"content-type": "text/event-stream"}
            )
        ),
    )
    received = []
    answer = Agent(Repository(tmp_path), provider).ask("Investigue.", on_delta=received.append)
    flow = dump(tmp_path)
    attempt = flow["turns"][0]["http_attempts"][0]
    assert "internal planning" in attempt["sse_events"][0]
    assert attempt["finish_reason"] == "stop"
    assert attempt["usage"] == {"completion_tokens": 17}
    assert answer == "".join(received) == "Resposta final."


def test_failed_tool_in_batch_is_not_hidden_by_following_success(tmp_path):
    batch = call("read_lines", {"path": "missing.py", "start": 1, "end": 1})
    batch["tool_calls"].extend(call("list_files", {}, "second")["tool_calls"])
    model = FakeModel(
        [
            batch,
            {"content": 'Vou repetir.\n{"name":"list_files","arguments":{}}'},
            {"content": "Não há arquivos permitidos."},
        ]
    )
    Agent(Repository(tmp_path), model).ask("Quais arquivos existem?")
    flow = dump(tmp_path)
    assert len(flow["turns"][0]["tool_results"]) == 2
    assert flow["turns"][1]["outcome"] == "protocol_repair"
    assert len(model.requests) == 3


@pytest.mark.parametrize("body", [b"not json", b'{"choices":[]}'])
def test_provider_decode_errors_keep_original_body(tmp_path, body):
    provider = OpenAICompatible(
        Settings("https://example.test/v1", "model"),
        httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    )
    with pytest.raises(ModelError):
        Agent(Repository(tmp_path), provider).ask("Investigue.")
    flow = dump(tmp_path)
    assert flow["status"] == "error"
    assert flow["turns"][0]["http_attempts"][0]["response_body"] == body.decode()


def test_trace_failure_warns_without_breaking_answer_or_masking_error(
    tmp_path, monkeypatch, caplog
):
    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr("codaro.trace.atomic_write", fail)
    agent = Agent(Repository(tmp_path), FakeModel([{"content": "OK"}, {"content": []}]))
    assert agent.ask("Pergunta.") == "OK"
    with pytest.raises(ModelError, match="textual inválida"):
        agent.ask("Outra pergunta.")
    assert "Não foi possível salvar" in caplog.text
    assert current_flow.get() is None
    assert not agent._lock.locked()


def test_atomic_failure_keeps_previous_valid_json_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / ".codaro/prompt.json"
    atomic_write(path, b'{"previous":true}')

    def fail(*_, **__):
        raise OSError("rename failed")

    monkeypatch.setattr("codaro.trace.os.replace", fail)
    with pytest.raises(OSError):
        atomic_write(path, b'{"next":true}')
    assert json.loads(path.read_text()) == {"previous": True}
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("link_type", ["storage", "symlink", "hardlink"])
def test_trace_rejects_links_without_touching_target(tmp_path, link_type):
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "sentinel.json"
    sentinel.write_text("keep this")
    storage = tmp_path / ".codaro"
    if link_type == "storage":
        storage.symlink_to(target, target_is_directory=True)
    else:
        storage.mkdir()
        if link_type == "symlink":
            (storage / "prompt.json").symlink_to(sentinel)
        else:
            os.link(sentinel, storage / "prompt.json")
    flow = PromptFlow(tmp_path, "Pergunta.", None, allow_edits=False, limits={})
    assert flow.write_error
    assert sentinel.read_text() == "keep this"
    assert list(target.iterdir()) == [sentinel]


def test_running_request_is_checkpointed_before_provider_call(tmp_path):
    class Model:
        def complete(self, messages, tools=None):
            flow = dump(tmp_path)
            assert flow["status"] == "running"
            assert flow["turns"][0]["request"]["messages"] == messages
            assert flow["turns"][0]["request"]["tools"] == tools
            return {"content": "OK"}

    assert Agent(Repository(tmp_path), Model()).ask("Investigue.") == "OK"


def test_trace_matches_real_wire_payload_across_error_recovery_and_final_answer(tmp_path):
    (tmp_path / "auth.py").write_text("enabled = True\n")
    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) < 3:
            args = {"limit": True} if len(payloads) == 1 else {"limit": "10"}
            response = call("list_files", args, f"tool-{len(payloads)}")
            return httpx.Response(
                200, json={"choices": [{"message": response, "finish_reason": "tool_calls"}]}
            )
        outputs = [item for item in payload["messages"] if item["role"] == "tool"]
        assert outputs[0]["tool_call_id"] == "tool-1"
        assert "error" in json.loads(outputs[0]["content"])
        assert outputs[1]["tool_call_id"] == "tool-2"
        assert json.loads(outputs[1]["content"])["files"] == ["auth.py"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "auth.py"}, "finish_reason": "stop"}]}
        )

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "model"), httpx.MockTransport(handler)
    )
    assert Agent(Repository(tmp_path), provider).ask("Quais arquivos existem?") == "auth.py"
    flow = dump(tmp_path)
    assert [turn["request"] for turn in flow["turns"]] == payloads
    assert flow["turns"][0]["tool_results"][0]["result"]["error"]
    assert flow["turns"][1]["tool_results"][0]["normalized_arguments"] == {"limit": 10}
    assert flow["status"] == "success"


def test_json_escaped_key_is_redacted_from_raw_responses(tmp_path):
    key = 'private-"quoted\\key'
    provider = OpenAICompatible(
        Settings("https://example.test/v1", "model", key),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "Echo: " + key + " Nested: " + json.dumps({"key": key}),
                            }
                        }
                    ]
                },
            )
        ),
    )
    Agent(Repository(tmp_path), provider).ask("Investigue.")
    flow = dump(tmp_path)
    assert key not in flow["final_answer"]
    raw_response = flow["turns"][0]["http_attempts"][0]["response_body"]
    assert json.dumps(key)[1:-1] not in raw_response
    assert "[REDACTED]" in raw_response


def test_request_contexts_do_not_mix_between_threads(tmp_path):
    barrier = threading.Barrier(2)
    roots = [tmp_path / "one", tmp_path / "two"]
    failures = []

    def investigate(root):
        try:

            def handler(request):
                barrier.wait(timeout=5)
                return httpx.Response(200, json={"choices": [{"message": {"content": root.name}}]})

            provider = OpenAICompatible(
                Settings("https://example.test/v1", root.name), httpx.MockTransport(handler)
            )
            Agent(Repository(root), provider).ask(root.name)
        except Exception as exc:
            failures.append(exc)

    for root in roots:
        root.mkdir()
    threads = [threading.Thread(target=investigate, args=(root,)) for root in roots]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not failures
    for root in roots:
        flow = dump(root)
        assert flow["model"] == root.name
        assert flow["final_answer"] == root.name
        assert flow["turns"][0]["http_attempts"][0]["status_code"] == 200
