import json

from test_agent import FakeModel, call

from codaro.agent import Agent
from codaro.diagnostics import fingerprint, observe_stream
from codaro.llm import Settings
from codaro.repository import Repository
from codaro.trace import PromptFlow


def test_stream_summary_covers_events_beyond_inline_preview_and_masks_secrets(tmp_path):
    flow = PromptFlow(
        tmp_path,
        "question",
        Settings("http://localhost/v1", "test", api_key="private-token"),
        allow_edits=False,
        limits={},
    )
    flow.add_turn({"messages": []}, {})
    flow.turn["http_attempts"].append({})
    for _ in range(60):
        flow.capture(
            "sse", json.dumps({"choices": [{"delta": {"reasoning": "private-token" + "x" * 750}}]})
        )
    flow.finish("error", error=ValueError("stop"))
    data = json.loads(flow.path.read_text())
    attempt = data["turns"][0]["http_attempts"][0]
    assert attempt["events_in_archive"]
    assert attempt["stream_summary"]["events"] == 60
    assert attempt["stream_summary"]["reasoning_chars"] == 60 * 763
    assert attempt["stream_summary"]["classification"] == "reasoning_only"
    assert "private-token" not in flow.path.read_text()
    assert "private-token" not in flow.archive_path.read_text()


def test_empty_retries_change_payload_and_keep_goal_and_code_evidence(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')\n")
    model = FakeModel(
        [
            call("read_lines", {"path": "app.py", "start": 1, "end": 1}),
            {"content": None},
            {"content": None},
            {"content": "Conclusão."},
        ]
    )
    Agent(Repository(tmp_path), model).ask("Leia app.py e explique")
    requests = model.requests[-2:]
    assert fingerprint(requests[0][0]) != fingerprint(requests[1][0])
    for messages, tools in requests:
        assert tools is None
        assert all(m["role"] in {"system", "user"} for m in messages)
        assert "hello" in str(messages)
        assert "Leia app.py e explique" in messages[-1]["content"]
    data = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert data["schema_version"] == 2
    assert data["diagnosis"]["empty_response_attempts"] == 2
    assert data["diagnosis"]["implementation_files_read"] == 1
    assert data["diagnosis"]["identical_retry_payloads"] == 0
    assert data["runtime"]["codaro_version"]
    assert data["turns"][-1]["budget"]["breakdown"]["tool_results"]["serialized_chars"] == 2
    assert any(d["reason"] == "synthesis" for d in data["decisions"])


def test_tool_reduction_preserves_original_and_sent_measurements(tmp_path):
    flow = PromptFlow(
        tmp_path, "question", Settings("http://localhost/v1", "test"), allow_edits=False, limits={}
    )
    flow.add_turn({"messages": []}, {})
    original = {"path": "AUDIT.md", "content": "x" * 9000}
    result = {"path": "AUDIT.md", "content": "small", "truncated": True}
    flow.tool_result(
        {"name": "read_lines", "content": json.dumps(result)}, {}, result, 2, original=original
    )
    flow.finish("success", answer="Partial analysis")
    reduction = flow.turn["tool_results"][0]["reduction"]
    assert reduction["original_serialized_chars"] > reduction["sent_serialized_chars"]
    assert reduction["result_reduced"]
    assert flow.data["diagnosis"]["read_files"] == {"AUDIT.md": 1}
    assert flow.data["diagnosis"]["historical_audit_share"] == 1
    assert flow.data["diagnosis"]["implementation_files_read"] == 0


def test_usage_event_does_not_move_last_meaningful_fragment_time():
    attempt = {}
    observe_stream(attempt, "sse", {"choices": [{"delta": {"content": "answer"}}]}, 10)
    observe_stream(attempt, "sse", {"choices": [], "usage": {"total_tokens": 20}}, 30)
    assert attempt["stream_summary"]["last_meaningful_fragment_ms"] == 10
