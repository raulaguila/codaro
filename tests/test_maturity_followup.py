"""Contracts for persistent process UI, transport bounds and interrupted operations."""

import json
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import replace

import httpx
import pytest
from test_streaming import chunk, encode, model
from test_tui import UIModel, run_ui
from textual.widgets import Collapsible, Static

from codaro.agent import Agent, AgentEvent
from codaro.agent.progress import Phase, TurnProgress, active_progress, waiting
from codaro.edits import EditManager
from codaro.index import CodeIndex
from codaro.llm import ModelError, Settings
from codaro.llm.protocol import MAX_STREAM_EVENT_BYTES
from codaro.llm.streaming import sse_events
from codaro.qualification import REQUIRED, qualification
from codaro.recovery import recovery_report, recovery_text
from codaro.repository import Repository
from codaro.retrieval_services import RetrievalServices
from codaro.ui.app import CodaroApp
from codaro.ui.widgets import GenerationPreview


def test_valid_stream_larger_than_two_mb_with_many_small_reasoning_frames():
    event = encode([chunk({"reasoning_content": "x" * 200})], done=False)
    assert len(event) * 12_000 > 2_000_000
    provider, stream = model(
        iter([event] * 12_000 + [encode([chunk({"content": "OK"}), chunk(reason="stop")])])
    )
    assert provider.stream([])["content"] == "OK"
    assert stream.closed


def test_configured_transport_limit_closes_stream_and_mentions_setting():
    event = encode([chunk({"reasoning_content": "x" * 200})], done=False)
    provider, stream = model(iter([event] * 5000))
    provider.settings = replace(provider.settings, max_stream_bytes=1024 * 1024)
    with pytest.raises(ModelError, match="CODARO_MAX_STREAM_MB"):
        provider.stream([])
    assert stream.closed


@pytest.mark.parametrize(
    "raw",
    [
        b"data: " + b"x" * (MAX_STREAM_EVENT_BYTES + 1),
        b"data: "
        + b"x" * (MAX_STREAM_EVENT_BYTES // 2)
        + b"\n"
        + b"data: "
        + b"x" * (MAX_STREAM_EVENT_BYTES // 2)
        + b"\n\n",
    ],
)
def test_single_and_multiline_events_have_independent_bounds(raw):
    with pytest.raises(ModelError, match="Evento"):
        list(sse_events(httpx.Response(200, content=raw), None))


@pytest.mark.parametrize("value", ["0", "129", "no", "1.5"])
def test_invalid_stream_setting_is_rejected(monkeypatch, value):
    monkeypatch.setenv("CODARO_MAX_STREAM_MB", value)
    with pytest.raises(ValueError, match="CODARO_MAX_STREAM_MB"):
        Settings("http://localhost", "test")


def test_stream_setting_is_applied_to_new_provider_settings(monkeypatch):
    monkeypatch.setenv("CODARO_MAX_STREAM_MB", "32")
    assert Settings("http://localhost", "test").max_stream_bytes == 32 * 1024 * 1024


def test_process_retains_thought_and_activity_order_and_final_answer_is_separate(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            app.active_question = "Investigue a API"
            app.busy = True
            app.append_reasoning("Primeira análise do modelo.")
            await pilot.pause()
            first = app.reasoning_preview
            app.finish_preview("tools")
            app.activity(AgentEvent("tool_end", "Ler arquivo", "api.py", "success", 2))
            app.append_reasoning("Segunda análise após a leitura.")
            await pilot.pause()
            second = app.reasoning_preview
            app.finish_preview("tools")
            app.activity(AgentEvent("tool_end", "Buscar referências", "consumers.py", "success", 3))
            group = app.activity_group
            assert not group.collapsed
            assert first.content.visible and second.content.visible
            assert group.sequence[0].query_one(GenerationPreview) is first
            assert isinstance(group.sequence[1], Static)
            assert group.sequence[2].query_one(GenerationPreview) is second
            app.append_delta("Resposta final verificada.")
            app.finish("Resposta final verificada.")
            await pilot.pause()
            assert group.collapsed and "Ver processo" in group.title
            assert app.reply.parent is app.query_one("#conversation")
            assert app.reply.visible and not group.query_one(Collapsible.Contents).display
            group.collapsed = False
            await pilot.pause()
            assert first.content.visible and second.content.visible
            assert "Primeira análise" not in str(app.session_turns)
            assert "Segunda análise" not in str(app.session_turns)

    run_ui(scenario())


def test_process_can_be_manually_collapsed_while_new_stages_arrive(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.append_reasoning("Etapa inicial.")
            await pilot.pause()
            group = app.activity_group
            group.collapsed = True
            app.finish_preview("tools")
            app.activity(AgentEvent("tool_end", "Ler arquivo", "code.py", "success", 1))
            app.append_reasoning("Outra etapa.")
            await pilot.pause()
            assert group.collapsed
            assert len(group.sequence) == 3

    run_ui(scenario())


def test_process_stages_arriving_before_first_refresh_are_never_lost(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            app.append_reasoning("Pensamento inicial.")
            app.finish_preview("tools")
            app.activity(AgentEvent("tool_start", "Ler arquivo", "one.py", "running"))
            app.activity(AgentEvent("tool_end", "Ler arquivo", "one.py", "success", 1))
            app.append_reasoning("Segundo pensamento.")
            app.finish_preview("tools")
            app.append_delta("Resultado provisório.")
            group = app.activity_group
            await pilot.pause()
            assert group.mounted_steps == len(group.sequence) == 4
            contents = group.query_one(Collapsible.Contents)
            assert list(contents.children)[1:] == group.sequence
            assert "Pensamento inicial" in str(
                group.sequence[0].query_one(GenerationPreview).content.render()
            )

    run_ui(scenario())


def test_incremental_index_preserves_ignore_rules_and_indexes_new_and_deleted_files(tmp_path):
    (tmp_path / "one.py").write_text("def one(): return 1\n")
    (tmp_path / "two.py").write_text("def two(): return 2\n")
    repository = Repository(tmp_path)
    with CodeIndex(repository) as index:
        index.update()
        (tmp_path / "one.py").write_text("def changed(): return 3\n")
        (tmp_path / "three.py").write_text("def added(): return 4\n")
        (tmp_path / ".gitignore").write_text("two.py\n")
        stats = index.update(changed_paths={"one.py"})
        assert stats["removed"] == 1
        assert (
            index.db.execute("SELECT symbol FROM chunks WHERE path='one.py'").fetchone()[0]
            == "changed"
        )
        assert (
            index.db.execute("SELECT symbol FROM chunks WHERE path='three.py'").fetchone()[0]
            == "added"
        )
        assert not index.db.execute("SELECT path FROM files WHERE path='two.py'").fetchall()


def test_native_project_watcher_refreshes_and_stops_without_changing_sources(tmp_path):
    pytest.importorskip("watchfiles")
    path = tmp_path / "main.py"
    path.write_text("def before(): return 1\n")
    repository = Repository(tmp_path)
    with CodeIndex(repository) as index:
        index.update()
    services = RetrievalServices(repository)
    try:
        services.start_watch()
        watcher = services.watcher
        assert watcher.ready.wait(5)
        assert watcher.status == "watching"
        path.write_text("def after(): return 2\n")
        assert watcher.updated.wait(5)
        with sqlite3.connect(tmp_path / ".codaro/index.sqlite3") as db:
            assert (
                db.execute("SELECT symbol FROM chunks WHERE path='main.py'").fetchone()[0]
                == "after"
            )
        assert path.read_text() == "def after(): return 2\n"
    finally:
        services.close()
    assert not services.watcher.thread.is_alive()


def test_checkpoint_reconciliation_detects_before_after_conflict_without_replaying(tmp_path):
    path = tmp_path / "main.py"
    path.write_text("before\n")
    edits = EditManager(Repository(tmp_path))
    proposal = edits._proposal("main.py", b"before\n", b"after\n", "Fix")
    identifier = edits.checkpoints.prepare(proposal)
    assert edits.checkpoints.reconcile()[0]["state"] == "matches_before"
    path.write_text("after\n")
    assert edits.checkpoints.reconcile()[0]["state"] == "matches_after"
    path.write_text("external\n")
    assert edits.checkpoints.reconcile()[0]["state"] == "conflict"
    assert path.read_text() == "external\n"
    assert edits.checkpoints.load()[0]["id"] == identifier
    assert edits.checkpoints.load()[0]["state"] == "ready"


def test_unconfirmed_command_receipts_are_reported_and_never_reexecuted(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    agent.tasks.start("Validate")
    agent.tasks.event("command_started", {"argv": ["python", "validate.py"], "cwd": "."})
    report = recovery_report(agent)
    assert report["commands"][0]["state"] == "unknown"
    assert not report["commands"][0]["automatic_replay"]
    assert "Nenhuma ação foi repetida" in recovery_text(report)
    agent.tasks.event("command_finished", {"argv": ["python", "validate.py"], "cwd": "."})
    assert not recovery_report(agent)["review_required"]
    agent.close()


def test_execution_phases_restore_waits_and_do_not_resurrect_terminal_run():
    progress = TurnProgress()
    with pytest.raises(ValueError):
        progress.transition(Phase.TOOLS)
    progress.transition(Phase.PREPARING)
    progress.transition(Phase.TOOLS)
    token = active_progress.set(progress)
    try:
        with pytest.raises(RuntimeError), waiting(Phase.APPROVAL):
            assert progress.phase == Phase.APPROVAL
            raise RuntimeError("User cancelled")
        assert progress.phase == Phase.TOOLS
        progress.transition(Phase.INTERRUPTED)
        with pytest.raises(ValueError):
            progress.transition(Phase.REQUESTING)
    finally:
        active_progress.reset(token)


@pytest.mark.parametrize("state", ["passed", "skipped", "failure"])
def test_qualification_requires_all_cases_and_workflow_evidence(tmp_path, state):
    root = ET.Element("testsuite")
    for name in REQUIRED:
        case = ET.SubElement(root, "testcase", name=name, time="1")
        if name == "test_catalog_and_metadata":
            identity = ET.SubElement(case, "properties")
            ET.SubElement(identity, "property", name="model_id", value="fixture")
            ET.SubElement(identity, "property", name="api_style", value="ollama")
        if name == "test_implementation_and_validation":
            props = ET.SubElement(case, "properties")
            ET.SubElement(
                props, "property", name="workflows", value=json.dumps({"passed": 5, "total": 5})
            )
            if state != "passed":
                ET.SubElement(case, state)
    path = tmp_path / "report.xml"
    ET.ElementTree(root).write(path)
    report = qualification(path)
    assert report["qualified_for_fixture_suite"] is (state == "passed")
    if state == "passed":
        root.remove(next(case for case in root if case.get("name") == "test_native_tool_roundtrip"))
        ET.ElementTree(root).write(path)
        assert not qualification(path)["qualified_for_fixture_suite"]
