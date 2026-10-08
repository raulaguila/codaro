import copy
import hashlib
import json
import re
import sys
import threading

import httpx
import pytest
from test_agent import FakeModel, call
from typer.testing import CliRunner

from codaro.agent import Agent
from codaro.artifacts import ArtifactStore
from codaro.cli import app
from codaro.context import TokenCounter
from codaro.continuity import FIELDS, ContextController
from codaro.features import DEFAULTS, FeatureStore
from codaro.provider import OpenAICompatible, RequestCancelled, Settings
from codaro.repository import Repository
from codaro.rpc import HttpRPC, StdioRPC
from codaro.runtime import RunBudget, request_budget
from codaro.session_catalog import SessionCatalog
from codaro.tool_registry import Tool, ToolRegistry, check_schema, definition
from codaro.trace import PromptFlow, atomic_write, current_flow


def features(**values):
    return {**copy.deepcopy(DEFAULTS), **values}


def test_artifact_pages_redaction_and_session_isolation(tmp_path):
    store = ArtifactStore(tmp_path, redact=lambda text: text.replace("secret", "[REDACTED]"))
    item = store.save("prefix secret\n" + "abc" * 5000 + "\nTAIL", source="command")
    assert item["complete"]
    assert "secret" not in store.text(item["id"])
    assert store.search(item["id"], "TAIL")["matches"]
    page = store.read(item["id"], limit=200)
    assert page["next_offset"] == 200
    assert page["source"] == "historical_tool_output_not_current_evidence"
    with pytest.raises(ValueError):
        ArtifactStore(tmp_path, session_id="different").text(item["id"])
    with pytest.raises(ValueError):
        store.text("../secret")


def test_artifact_limits_cleanup_and_corrupt_index(tmp_path, monkeypatch):
    import codaro.artifacts as artifacts

    monkeypatch.setattr(artifacts, "MAX_ARTIFACTS", 2)
    store = ArtifactStore(tmp_path)
    first = store.save("first", source="one")
    orphan = store.directory / ("f" * 32 + ".txt")
    orphan.write_text("orphan")
    store.save("second", source="two")
    assert not orphan.exists()
    store.save("third", source="three")
    assert len(store.index()) == 2
    assert not (store.directory / (first["id"] + ".txt")).exists()
    atomic_write(store.index_path, b'[{"id": "../invalid"}]')
    before = list(store.directory.iterdir())
    with pytest.raises(ValueError):
        store.save("must not be written", source="four")
    assert list(store.directory.iterdir()) == before


def test_artifact_rejects_symlinks_and_preserves_unicode(tmp_path):
    store = ArtifactStore(tmp_path)
    item = store.save("á" * 1_100_000, source="huge")
    assert not item["complete"]
    assert store.text(item["id"]).endswith("á")
    target = store.directory / (item["id"] + ".txt")
    target.unlink()
    target.symlink_to(tmp_path / "secret")
    with pytest.raises(ValueError):
        store.text(item["id"])


def test_external_schema_and_authorization_fail_closed():
    registry = ToolRegistry()
    schema = definition(
        "remote_write",
        "test",
        {"count": {"type": "integer", "minimum": 1, "maximum": 4}},
        ["count"],
    )
    registry.register(Tool(schema, source="mcp:test", read_only=False))
    for mode, advertised, closed in [
        ("ask", {"remote_write"}, False),
        ("execute", set(), False),
        ("execute", {"remote_write"}, True),
    ]:
        with pytest.raises(ValueError):
            registry.authorize("remote_write", mode, advertised, closed=closed)
    registry.authorize("remote_write", "execute", {"remote_write"})
    for args in [{"count": True}, {"count": 5}, {"count": 1, "extra": 0}, {}]:
        with pytest.raises(ValueError):
            registry.validate("remote_write", args)
    for spec in [
        {"$ref": "https://invalid"},
        {"type": ["string", "null"]},
        {"type": "string", "pattern": "(a+)+$"},
    ]:
        with pytest.raises((ValueError, TypeError)):
            check_schema(spec)


def test_feature_store_requires_explicit_trust_and_bounded_config(tmp_path):
    store = FeatureStore(tmp_path)
    assert store.load()["semantic_compaction"] is False
    assert store.toggle("semantic_compaction", True)["semantic_compaction"]
    config = features(mcp={"untrusted": {"enabled": True, "command": ["bad"]}})
    with pytest.raises(ValueError):
        store.save(config)
    with pytest.raises(ValueError):
        store.save(features(max_run_requests=1))
    with pytest.raises(ValueError):
        store.save(
            features(
                mcp={
                    "x": {
                        "enabled": True,
                        "trusted": True,
                        "transport": "http",
                        "url": "https://user:secret@example.com",
                    }
                }
            )
        )


def summary_value():
    return {
        "objective": "Corrigir autenticação",
        **{key: ["Preservar API pública"] if key == "details" else [] for key in FIELDS[1:]},
    }


def test_continuity_does_not_steal_main_trace_iteration(tmp_path):
    settings = Settings("https://test.invalid/v1", "model")

    def handle(request):
        body = json.loads(request.content)
        assert "tools" not in body
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(summary_value())}}]}
        )

    provider = OpenAICompatible(settings, httpx.MockTransport(handle))
    controller = ContextController(TokenCounter(), features(semantic_compaction=True))
    flow = PromptFlow(tmp_path, "current", settings, allow_edits=False, limits={})
    flow.add_turn({"messages": []}, {})
    original = flow.turn
    token = current_flow.set(flow)
    try:
        result = controller.summarize(
            provider,
            [{"role": "user", "content": "Preservar API"}],
            input_limit=12_000,
            redact=lambda value: value,
        )
        assert result["details"] == ["Preservar API pública"]
        assert flow.turn is original
        assert len(flow.data["turns"]) == 1
    finally:
        current_flow.reset(token)


def test_continuity_invalid_output_falls_back_and_cancellation_propagates():
    provider = OpenAICompatible(
        Settings("https://test.invalid/v1", "model"),
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": "not JSON"}}]}
            )
        ),
    )
    controller = ContextController(TokenCounter(), features(semantic_compaction=True))
    controller.summary = summary_value()
    old = copy.deepcopy(controller.summary)
    assert (
        controller.summarize(
            provider, [{"role": "user", "content": "foo"}], input_limit=12_000, redact=lambda x: x
        )
        is None
    )
    assert controller.summary == old and controller.failures == 1
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RequestCancelled):
        controller.summarize(
            provider,
            [{"role": "user", "content": "foo"}],
            input_limit=12_000,
            redact=lambda x: x,
            cancelled=cancelled,
        )


def test_session_catalog_migrates_default_and_isolates_memory_tasks(tmp_path):
    agent = Agent(Repository(tmp_path), FakeModel([{"content": "Olá"}]), mode="execute")
    agent.sessions.store("default").save(
        [[{"role": "user", "content": "old"}, {"role": "assistant", "content": "old answer"}]],
        "test",
    )
    original_memory = agent.memory.path
    agent.memory.remember("decision", "private decision", source="user")
    agent.tasks.start("Original")
    new_id = agent.sessions.create("Outra atividade")
    agent.activate_session(new_id)
    assert agent.turns == []
    assert agent.tasks.current() is None
    assert agent.memory.path != original_memory
    assert "private decision" not in json.dumps(agent.memory.task())
    agent.tasks.start("Nova")
    agent.activate_session("default")
    assert agent.turns[0][0]["content"] == "old"
    assert agent.tasks.current()["objective"] == "Original"
    assert "private decision" in json.dumps(agent.memory.task())
    assert SessionCatalog(tmp_path).load()["active"] == "default"
    with pytest.raises(ValueError):
        agent.activate_session("../outside")


def test_global_budget_shared_and_restored_after_ask(tmp_path):
    budget = RunBudget(4, 100_000)
    token = request_budget.set(budget)
    try:
        agent = Agent(Repository(tmp_path), FakeModel([{"content": "Olá"}]))
        agent.ask("Oi")
        assert request_budget.get() is budget and budget.requests == 1
        budget.charge(1, 1)
        budget.charge(1, 1)
        budget.charge(1, 1)
        with pytest.raises(RuntimeError):
            budget.charge(1, 1)
    finally:
        request_budget.reset(token)
    assert request_budget.get() is None


MCP_SERVER = r"""
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request: continue
    method = request["method"]
    if method == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo",
                             "inputSchema": {"type": "object", "properties": {
                                "text": {"type": "string"}}, "required": ["text"],
                                "additionalProperties": False}}]}
    else:
        result = {"content": [{"type": "text", "text": request["params"]["arguments"]["text"]}]}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""


def mcp_config(server, readonly=True):
    return {
        "local": {
            "enabled": True,
            "trusted": True,
            "transport": "stdio",
            "command": [sys.executable, str(server)],
            "read_only_tools": ["echo"] if readonly else [],
        }
    }


def test_stdio_mcp_lifecycle_schema_call_and_cleanup(tmp_path):
    server = tmp_path / "server.py"
    server.write_text(MCP_SERVER)
    agent = Agent(Repository(tmp_path), FakeModel([]), features=features(mcp=mcp_config(server)))
    hub = agent.integrations
    try:
        hub.discover()
        assert not hub.errors
        tool = next(tool for tool in agent.registry.tools.values() if tool.source == "mcp:local")
        assert tool.lazy and tool.read_only
        agent.registry.validate(tool.name, {"text": "hello"})
        result = tool.handler({"text": "hello"})
        assert "hello" in result["output"]
        process = hub.clients["mcp:local"].process
    finally:
        hub.close()
    assert process.poll() is not None
    assert not any(tool.source == "mcp:local" for tool in agent.registry.tools.values())


def test_mcp_mutation_denied_without_callback_and_approved_once(tmp_path):
    server = tmp_path / "server.py"
    server.write_text(MCP_SERVER)
    approvals = []
    agent = Agent(
        Repository(tmp_path),
        FakeModel([]),
        mode="execute",
        features=features(mcp=mcp_config(server, False)),
    )
    agent.integrations.discover()
    try:
        tool = next(tool for tool in agent.registry.tools.values() if tool.source == "mcp:local")
        assert not tool.read_only
        assert tool.handler({"text": "hello"})["state"] == "rejected"
        agent.approve_external = lambda *args: approvals.append(args) or True
        assert "hello" in tool.handler({"text": "hello"})["output"]
        assert len(approvals) == 1
    finally:
        agent.integrations.close()


def test_rpc_cancellation_and_malformed_response_close_process(tmp_path):
    server = tmp_path / "hang.py"
    server.write_text("import time\nwhile True: time.sleep(1)\n")
    rpc = StdioRPC([sys.executable, str(server)], tmp_path)
    cancelled = threading.Event()
    cancelled.set()
    try:
        with pytest.raises(RequestCancelled):
            rpc.request("initialize", cancelled=cancelled)
    finally:
        rpc.close()
    assert rpc.process.poll() is not None


def test_mcp_streamable_http_session_and_sse():
    received = []

    def handle(request):
        received.append(request)
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream", "Mcp-Session-Id": "session"},
            content="data: "
            + json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"ok": True}})
            + "\n\n",
        )

    rpc = HttpRPC(
        "https://example.invalid/mcp", token="private", transport=httpx.MockTransport(handle)
    )
    try:
        assert rpc.request("initialize") == {"ok": True}
        assert rpc.request("tools/list") == {"ok": True}
        assert received[-1].headers["Mcp-Session-Id"] == "session"
        assert received[-1].headers["Authorization"] == "Bearer private"
    finally:
        rpc.close()


PLUGIN = """
def register():
    return {"api_version": 1, "tools": [{
        "name": "sum", "description": "Adds",
        "parameters": {"type": "object", "properties": {
            "a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"], "additionalProperties": False},
        "handler": lambda args: args["a"] + args["b"]
    }]}
"""


def test_plugin_explicit_hash_and_protocol(tmp_path):
    plugin = tmp_path / "plugin.py"
    plugin.write_text(PLUGIN)
    config = {
        "math": {
            "enabled": True,
            "trusted": True,
            "path": str(plugin),
            "sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
            "read_only_tools": ["sum"],
        }
    }
    agent = Agent(Repository(tmp_path), FakeModel([]), features=features(plugins=config))
    try:
        agent.integrations.discover()
        assert not agent.integrations.errors
        tool = next(tool for tool in agent.registry.tools.values() if tool.source == "plugin:math")
        assert '"3"' in tool.handler({"a": 1, "b": 2})["output"]
    finally:
        agent.integrations.close()
    plugin.write_text(PLUGIN + "\n# changed")
    agent.integrations.discover()
    assert "mudou" in agent.integrations.errors["plugin:math"]
    assert not agent.integrations.clients


def test_cli_registration_features_sessions_and_trust(tmp_path):
    runner = CliRunner()
    assert (
        runner.invoke(
            app, ["features", "set", "exploration", "true", "--repo", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert FeatureStore(tmp_path).load()["exploration"]
    result = runner.invoke(app, ["sessions", "new", "Working", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout.strip() == SessionCatalog(tmp_path).load()["active"]
    result = runner.invoke(
        app,
        ["integrations", "add", "mcp", "local", "--command", '["x"]', "--repo", str(tmp_path)],
        color=True,
        env={"FORCE_COLOR": "1"},
    )
    plain_output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)
    assert result.exit_code != 0 and "--trust" in plain_output
    assert not FeatureStore(tmp_path).load()["mcp"]


def test_agent_exploration_lazy_and_separate_trace(tmp_path):
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        system = payload["messages"][0]["content"]
        if len(requests) == 1:
            reply = call("request_tools", {"names": ["explore_code"]})
        elif len(requests) == 2:
            reply = call("explore_code", {"objective": "Liste os arquivos"}, "child")
        elif len(requests) == 3:
            assert "apply_changes" not in json.dumps(payload.get("tools", []))
            assert "explore_code" not in system
            reply = {"content": "Relatório da exploração."}
        else:
            reply = {"content": "Resposta final."}
        return httpx.Response(200, json={"choices": [{"message": reply}]})

    model = OpenAICompatible(
        Settings("https://test.invalid/v1", "model"), httpx.MockTransport(handle)
    )
    agent = Agent(Repository(tmp_path), model, features=features(exploration=True))
    assert agent.ask("Explore o projeto") == "Resposta final."
    assert len(requests) == 4
    parent = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    child = json.loads((tmp_path / ".codaro/exploration.json").read_text())
    assert parent["run_id"] != child["run_id"]
    assert parent["user_question"] == "Explore o projeto"
    assert len(parent["turns"]) == 3 and len(child["turns"]) == 1


def test_tool_catalog_trim_keeps_pagination_and_artifact_receipt():
    result = {
        "offset": 4,
        "tools": [{"name": "a", "description": "x" * 150}, {"name": "b", "description": "y" * 150}],
        "next_offset": 6,
    }
    fitted = Agent.fit_result(result, 310)
    assert len(fitted["tools"]) == 1 and fitted["next_offset"] == 5
    assert "error" in Agent.fit_result(result, 100)
    receipt = Agent.fit_result({"diagnostics": ["x" * 10_000], "artifact_id": "a" * 32}, 250)
    assert receipt["artifact_id"] == "a" * 32


def test_artifact_storage_failure_preserves_command_receipt(tmp_path, monkeypatch):
    approvals = []
    model = FakeModel(
        [
            call("run_command", {"argv": [sys.executable, "-c", "print('x' * 10000)"]}),
            {"content": "Verificação concluída."},
        ]
    )
    agent = Agent(
        Repository(tmp_path), model, approve_command=lambda *args: approvals.append(args) or True
    )

    def failed_save(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(agent.artifacts, "save", failed_save)
    assert agent.ask("Execute uma verificação") == "Verificação concluída."
    outputs = [item for item in model.requests[-1][0] if item.get("role") == "tool"]
    assert json.loads(outputs[-1]["content"])["exit_code"] == 0
    assert len(approvals) == 1
