import json
import sys

import pytest
from test_agent import FakeModel, call
from test_tui import UIModel, run_ui

from codaro.agent import Agent
from codaro.edits import EditManager, EditProposal
from codaro.extension_screens import IntegrationRegistration
from codaro.lsp import diagnostics
from codaro.repository import Repository
from codaro.trace import PromptFlow, current_flow
from codaro.tui import CodaroApp, ExternalToolReview
from codaro.undo_history import UndoHistory
from codaro.workflow_evaluation import evaluate_workflows


def applied_checkpoint(edits, path, before, after):
    proposal = EditProposal("abc123", path, "change", before, after, "")
    identifier = edits.checkpoints.prepare(proposal)
    (edits.repository.root / path).write_bytes(after)
    edits.checkpoints.mark(identifier, "applied")
    return identifier


def test_interaction_undo_redo_merges_repeated_writes_and_preflights_all(tmp_path):
    (tmp_path / "a.py").write_bytes(b"a=0\n")
    (tmp_path / "b.py").write_bytes(b"b=0\n")
    edits = EditManager(Repository(tmp_path))
    flow = PromptFlow(tmp_path, "edit", None, allow_edits=True, limits={})
    token = current_flow.set(flow)
    try:
        applied_checkpoint(edits, "a.py", b"a=0\n", b"a=1\n")
        applied_checkpoint(edits, "a.py", b"a=1\n", b"a=2\n")
        applied_checkpoint(edits, "b.py", b"b=0\n", b"b=2\n")
    finally:
        current_flow.reset(token)
    history = UndoHistory(edits)
    record, proposals = history.preview()
    assert len(proposals) == 2
    assert proposals[0].after == b"a=0\n"
    (tmp_path / "b.py").write_bytes(b"manual\n")
    with pytest.raises(ValueError):
        history.apply(record, proposals)
    assert (tmp_path / "a.py").read_bytes() == b"a=2\n"
    (tmp_path / "b.py").write_bytes(b"b=2\n")
    history.apply(record, proposals)
    assert (tmp_path / "a.py").read_bytes() == b"a=0\n"
    assert (tmp_path / "b.py").read_bytes() == b"b=0\n"
    record, proposals = history.preview(redo=True)
    history.apply(record, proposals, redo=True)
    assert (tmp_path / "a.py").read_bytes() == b"a=2\n"
    assert (tmp_path / "b.py").read_bytes() == b"b=2\n"
    assert history.load()[-1]["state"] == "redone"


def test_interaction_undo_retains_partial_journal(tmp_path, monkeypatch):
    edits = EditManager(Repository(tmp_path))
    flow = PromptFlow(tmp_path, "edit", None, allow_edits=True, limits={})
    token = current_flow.set(flow)
    try:
        for path in ("a.py", "b.py"):
            (tmp_path / path).write_bytes(b"before\n")
            applied_checkpoint(edits, path, b"before\n", b"after\n")
    finally:
        current_flow.reset(token)
    history = UndoHistory(edits)
    record, proposals = history.preview()
    apply = edits.apply
    calls = []

    def fail_second(identifier):
        calls.append(identifier)
        if len(calls) == 2:
            raise OSError("simulated disk failure")
        return apply(identifier)

    monkeypatch.setattr(edits, "apply", fail_second)
    with pytest.raises(OSError):
        history.apply(record, proposals)
    assert history.load()[-1]["state"] == "partial"
    assert history.load()[-1]["applied_paths"] == ["a.py"]
    assert (tmp_path / "b.py").read_bytes() == b"after\n"


LSP_SERVER = r"""
import json, sys
def send(value):
    raw = json.dumps(value).encode()
    sys.stdout.buffer.write(("Content-Length: %s\r\n\r\n" % len(raw)).encode() + raw)
    sys.stdout.buffer.flush()
while True:
    line = sys.stdin.buffer.readline()
    if not line: break
    n = int(line.split(b":")[1])
    sys.stdin.buffer.readline()
    value = json.loads(sys.stdin.buffer.read(n))
    if value["method"] == "initialize":
        send({"jsonrpc": "2.0", "id": value["id"], "result": {"capabilities": {}}})
    elif value["method"] == "textDocument/didOpen":
        document = value["params"]["textDocument"]
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
              "params": {"uri": document["uri"], "version": 1,
                         "diagnostics": [{"message": "Undefined name", "severity": 1,
                                          "range": {"start": {"line": 0, "character": 0},
                                                    "end": {"line": 0, "character": 1}}}]}})
"""


def test_lsp_installed_protocol_hash_and_missing_server(tmp_path):
    (tmp_path / "main.py").write_text("unknown\n")
    server = tmp_path / "server.py"
    server.write_text(LSP_SERVER)
    result = diagnostics(
        Repository(tmp_path),
        "main.py",
        {"enabled": True, "servers": {"python": [sys.executable, str(server)]}},
    )
    assert result["status"] == "current"
    assert len(result["file_hash"]) == 64
    assert result["diagnostics"][0]["message"] == "Undefined name"
    assert result["validation_passed"] is False
    result = diagnostics(
        Repository(tmp_path),
        "main.py",
        {"enabled": True, "servers": {"python": ["codaro-nonexistent-server-abc"]}},
    )
    assert not result["available"]
    with pytest.raises(ValueError):
        diagnostics(Repository(tmp_path), "../main.py", {"enabled": True, "servers": {}})


def test_workflow_benchmark_executes_in_copy_and_checks_real_files(tmp_path):
    project = tmp_path / "fixture"
    project.mkdir()
    (project / "main.py").write_text("x = 1\n")
    cases = tmp_path / "cases.json"
    argv = [sys.executable, "-c", "from main import x; assert x == 2"]
    cases.write_text(
        json.dumps(
            [
                {
                    "id": "change",
                    "repo": "fixture",
                    "query": "Altere x para 2 e valide.",
                    "expected_files": {"main.py": ["x = 2"]},
                    "validation_commands": [argv],
                }
            ]
        )
    )
    model = FakeModel(
        [
            call("read_lines", {"path": "main.py", "start": 1, "end": 1}),
            call(
                "apply_changes",
                {
                    "operations": [
                        {
                            "kind": "edit",
                            "path": "main.py",
                            "old_text": "x = 1",
                            "new_text": "x = 2",
                        }
                    ],
                    "reason": "Corrigir x",
                },
                "change",
            ),
            {"content": "Alteração aplicada."},
            {"content": "Alteração aplicada."},
            {"content": "Alteração aplicada."},
            {"content": "Alteração aplicada."},
        ]
    )
    with pytest.raises(ValueError):
        evaluate_workflows(cases, model)
    report = evaluate_workflows(cases, model, allow_execution=True)
    assert report["passed"] == 1, report
    assert (project / "main.py").read_text() == "x = 1\n"
    assert report["results"][0]["validations"][0]["exit_code"] == 0
    assert report["results"][0]["cost"] is None


def test_tui_session_switch_feature_and_external_review(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel())

    async def scenario():
        ui = CodaroApp(agent)
        async with ui.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            await ui.local_command("/session new Projeto A")
            assert agent.session_id != "default"
            assert ui.session.path.name == "session-" + agent.session_id + ".json"
            await ui.local_command("/features exploration on")
            assert agent.features["exploration"]
            await ui.local_command("/session default")
            assert agent.session_id == "default"
            dialog = ExternalToolReview(tmp_path, "mcp:remote", "write", {"name": "value"})
            ui.push_screen(dialog)
            await pilot.pause()
            assert ui.focused.id == "reject-command"
            await pilot.press("escape")
            await pilot.pause()
            assert ui.screen is not dialog

    run_ui(scenario())


def test_tui_integration_registration_tests_and_saves_only_after_trust(tmp_path):
    import shlex

    from test_extensions import MCP_SERVER
    from textual.widgets import Checkbox, Input, Static

    from codaro.features import FeatureStore

    server = tmp_path / "server.py"
    server.write_text(MCP_SERVER)

    async def scenario():
        ui = CodaroApp(Agent(Repository(tmp_path), UIModel()))
        async with ui.run_test(size=(110, 40)) as pilot:
            await ui.local_command("/integrations")
            await pilot.pause()
            screen = ui.screen
            assert isinstance(screen, IntegrationRegistration)
            screen.query_one("#integration-name", Input).value = "local"
            screen.query_one("#integration-command", Input).value = shlex.join(
                [sys.executable, str(server)]
            )
            await pilot.click("#integration-save")
            await pilot.pause()
            assert not FeatureStore(tmp_path).load()["mcp"]
            assert "confiança" in str(screen.query_one("#integration-status", Static).render())
            screen.query_one("#integration-trust", Checkbox).value = True
            screen.query_one("#integration-readonly", Input).value = "echo"
            await pilot.click("#integration-test")
            for _ in range(100):
                await pilot.pause(0.02)
                if "Conectado" in str(screen.query_one("#integration-status", Static).render()):
                    break
            assert "Conectado" in str(screen.query_one("#integration-status", Static).render())
            # Connection testing must not register the integration.
            assert not FeatureStore(tmp_path).load()["mcp"]
            await pilot.click("#integration-save")
            await pilot.pause()
            assert not isinstance(ui.screen, IntegrationRegistration)
            assert FeatureStore(tmp_path).load()["mcp"]["local"]["read_only_tools"] == ["echo"]
            assert ui.agent.features["mcp"]["local"]["enabled"]

    run_ui(scenario())
