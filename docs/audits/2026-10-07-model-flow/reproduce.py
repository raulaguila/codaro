"""Read-only audit of application behavior using isolated repos and synthetic API responses."""

# ruff: noqa: E402

import json
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
from test_agent import FakeModel, call

from codaro.agent import Agent
from codaro.anthropic import Anthropic
from codaro.context import TokenCounter
from codaro.provider import (
    ContextCapacityError,
    ModelError,
    OpenAICompatible,
    Settings,
    build_payload,
)
from codaro.repository import Repository
from codaro.trace import PromptFlow

OUT = Path(__file__).parent
results = []


class Model(FakeModel):
    def complete(self, messages, tools=None):
        self.requests.append((list(messages), tools))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def root(base, name):
    p = base / name
    p.mkdir()
    return p


def record(case, **values):
    results.append({"case": case, **values})


def run(base):
    p = root(base, "constraints")
    model = Model([{"content": "Olá."}])
    model.settings = Settings("http://audit.invalid/v1", "small", context_window=4096)
    agent = Agent(Repository(p), model, mode="ask")
    for i in range(8):
        agent.memory.remember("constraint", f"AUDIT_CONSTRAINT_{i} " + "x" * 270)
    agent.ask("Olá")
    sent = json.dumps(model.requests[0][0])
    record(
        "user_constraints", stored=8, sent=sum(f"AUDIT_CONSTRAINT_{i}" in sent for i in range(8))
    )

    p = root(base, "guidance")
    (p / "AGENTS.md").write_text("AUDIT_REQUIRED_PROJECT_RULE\n" + "Orientação de estilo.\n" * 70)
    model = Model([{"content": "Olá."}])
    model.settings = Settings("http://audit.invalid/v1", "small", context_window=4096)
    agent = Agent(Repository(p), model, mode="ask")
    agent.ask("Olá")
    dump = json.loads((p / ".codaro/prompt.json").read_text())
    record(
        "project_guidance",
        locally_read=bool(dump.get("local_retrievals")),
        sent="AUDIT_REQUIRED_PROJECT_RULE" in json.dumps(model.requests[0][0]),
        compactions=[x["kind"] for x in dump.get("compactions", [])],
    )

    p = root(base, "proposals")
    (p / "x.py").write_text("x = 1\n")
    model = Model(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}),
            call(
                "propose_edit",
                {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2", "reason": "audit"},
                "edit",
            ),
            {"content": "Diff preparado."},
            {"content": "Olá."},
        ]
    )
    agent = Agent(Repository(p), model, allow_edits=True)
    agent.ask("Prepare um diff.")
    before = len(agent.edits.pending)
    blocked = False
    try:
        agent.ask("Olá")
    except ValueError:
        blocked = True
    record(
        "pending_proposal_next_turn",
        blocked=blocked,
        before=before,
        after=len(agent.edits.pending),
        file=(p / "x.py").read_text(),
    )

    p = root(base, "proposal_failure")
    (p / "x.py").write_text("x = 1\n")
    model = Model(
        [
            call("read_lines", {"path": "x.py", "start": 1, "end": 1}),
            call(
                "propose_edit",
                {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2", "reason": "audit"},
                "edit",
            ),
            ContextCapacityError("Conversa preservada."),
        ]
    )
    agent = Agent(Repository(p), model, allow_edits=True)
    try:
        agent.ask("Prepare um diff.")
    except ContextCapacityError:
        pass
    record(
        "pending_proposal_failure",
        states=[x.state for x in agent.edits.proposals.values()],
        pending=len(agent.edits.pending),
    )

    p = root(base, "old_key")
    old = "AUDIT_SYNTHETIC_OLD_KEY"
    model = Model([{"content": "Entendido."}])
    model.settings = Settings("http://audit.invalid/v1", "one", api_key=old)
    agent = Agent(Repository(p), model, mode="ask")
    agent.ask("Sentinela de teste: " + old)
    second = Model([{"content": "Olá."}])
    second.settings = Settings("http://audit.invalid/v1", "two", api_key="AUDIT_SYNTHETIC_NEW_KEY")
    agent.set_provider(second)
    agent.ask("Olá")
    record(
        "old_key_after_provider_change",
        present_in_new_trace=old in (p / ".codaro/prompt.json").read_text(),
        present_in_new_request=old in json.dumps(second.requests[0][0]),
    )

    p = root(base, "false_completion")
    (p / "x.py").write_text("x = 1\n")
    agent = Agent(Repository(p), Model([{"content": "Alterado e validado."}]), mode="execute")
    answer = agent.ask("Altere x.py de x = 1 para x = 2 e valide.")
    record(
        "completion_without_work",
        task_state=agent.tasks.current()["state"],
        revision=agent.tasks.current()["revision"],
        validations=agent.tasks.current()["validations"],
        file_unchanged=(p / "x.py").read_text() == "x = 1\n",
        answer=answer,
    )

    p = root(base, "unadvertised_tool")
    model = Model([call("get_repository_info", {}), {"content": "Pronto."}])
    model.settings = Settings("http://audit.invalid/v1", "small", context_window=4096)
    agent = Agent(Repository(p), model, mode="ask")
    agent.ask("Consulte o diretório.")
    advertised = [x["function"]["name"] for x in model.requests[0][1]]
    output = json.loads((p / ".codaro/prompt.json").read_text())["turns"][0]["tool_results"][0][
        "result"
    ]
    record(
        "unadvertised_tool",
        advertised="get_repository_info" in advertised,
        executed_successfully="repository_root" in output,
    )

    payload = build_payload("test", [{"role": "user", "content": "x"}], None, streaming=True)
    record("openai_stream_usage", requests_usage="stream_options" in payload)
    native = Anthropic.wire_payload(
        build_payload(
            "test",
            [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "x"},
                {"role": "assistant", "content": "y"},
            ],
            None,
        )
    )
    counter = TokenCounter()
    record(
        "anthropic_framing",
        native_messages=len(native["messages"]),
        counted_message_framing=counter.base_count(native)
        - ((len(json.dumps(native, ensure_ascii=False, separators=(",", ":")).encode()) + 1) // 2)
        - 32,
        expected_message_framing=16 * len(native["messages"]),
    )

    p = root(base, "failed_memory")
    (p / "README.md").write_text("AUDIT_FAILED_TOOL_EXCERPT\n")
    agent = Agent(
        Repository(p),
        Model(
            [
                call("read_lines", {"path": "README.md", "start": 1, "end": 1}),
                ModelError("Falha sintética."),
                {"content": "Outra resposta."},
            ]
        ),
        mode="ask",
    )
    try:
        agent.ask("AUDIT_FAILED_REQUEST")
    except ModelError:
        pass
    agent.ask("Outra pergunta.")
    record(
        "failed_conversation_recovery",
        search=agent.memory.search("AUDIT_FAILED_REQUEST", 5),
        last_trace_contains_failed_question="AUDIT_FAILED_REQUEST"
        in (p / ".codaro/prompt.json").read_text(),
        last_trace_contains_failed_excerpt="AUDIT_FAILED_TOOL_EXCERPT"
        in (p / ".codaro/prompt.json").read_text(),
    )

    p = root(base, "deadline")

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(6):
                time.sleep(0.25)
                yield (
                    "data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]}) + "\n\n"
                ).encode()
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    provider = OpenAICompatible(
        Settings("http://audit.invalid/v1", "test"),
        httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=SlowStream()
            )
        ),
    )
    agent = Agent(Repository(p), provider, mode="ask", max_seconds=1)
    emitted = []
    started = time.monotonic()
    try:
        agent.ask("Olá", on_delta=lambda delta: emitted.append(time.monotonic() - started))
    except ModelError:
        pass
    record(
        "stream_deadline",
        configured_seconds=1,
        elapsed_seconds=round(time.monotonic() - started, 3),
        deltas_after_deadline=sum(x > 1 for x in emitted),
    )

    args = {
        "reason": "audit",
        "operations": [{"kind": "create", "path": "new.txt", "content": "x" * 9000}],
    }
    Agent.validate_arguments("apply_changes", args)
    from codaro.provider import create_provider, validate_message

    try:
        validate_message(call("apply_changes", args))
        rejected = False
    except ModelError:
        rejected = True
    record(
        "schema_transport_limits",
        valid_for_tool=True,
        argument_chars=len(json.dumps(args)),
        rejected_by_transport=rejected,
    )

    p = root(base, "memory_restart")
    windows = []

    def low_memory(request):
        options = json.loads(request.content)["options"]
        windows.append(options["num_ctx"])
        if options["num_ctx"] > 16384:
            return httpx.Response(500, json={"error": "out of memory"})
        return httpx.Response(200, json={"done": True, "message": {"content": "Pronto."}})

    for _ in range(2):
        native = create_provider(
            Settings("http://audit.invalid/v1", "test", api_style="ollama", context_window=131072),
            transport=httpx.MockTransport(low_memory),
        )
        Agent(Repository(p), native, mode="ask").ask("Olá")
    record("memory_window_after_restart", requested_windows=windows)

    p = root(base, "stream_memory_error")
    windows = []

    def streamed_memory_error(request):
        windows.append(json.loads(request.content)["options"]["num_ctx"])
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content='{"error":"out of memory"}\n',
        )

    native = create_provider(
        Settings("http://audit.invalid/v1", "test", api_style="ollama", context_window=131072),
        transport=httpx.MockTransport(streamed_memory_error),
    )
    try:
        Agent(Repository(p), native, mode="ask").ask("Olá", on_delta=lambda delta: None)
    except ModelError as exc:
        record(
            "memory_error_in_stream",
            error_type=type(exc).__name__,
            attempts=len(windows),
            requested_windows=windows,
        )

    p = root(base, "output_limit")
    calls_count = []

    def length_reply(request):
        calls_count.append(1)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Parcial."}, "finish_reason": "length"}]}
        )

    limited = OpenAICompatible(
        Settings("http://audit.invalid/v1", "test"), httpx.MockTransport(length_reply)
    )
    try:
        Agent(Repository(p), limited, mode="ask").ask("Explique.")
    except ModelError as exc:
        record("output_limit_recovery", attempts=len(calls_count), error_type=type(exc).__name__)

    p = root(base, "trace_size")
    flow = PromptFlow(
        p, "audit", Settings("http://audit.invalid/v1", "test"), allow_edits=False, limits={}
    )
    for _ in range(4):
        flow.add_turn({"model": "test", "messages": [], "stream": True}, {})
        flow.turn["http_attempts"].append({"attempt": 1, "sse_events": ["x" * 800000]})
        flow.checkpoint()
    record("trace_budget", file_bytes=flow.path.stat().st_size, write_error=flow.write_error)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="codaro-model-audit-") as directory:
        run(Path(directory))
    (OUT / "observations.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))
