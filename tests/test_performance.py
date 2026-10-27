"""Performance regressions use bounded work and ownership assertions, not CPU deadlines."""

import asyncio
import json
import subprocess
import sys
import threading

import pytest
from test_tui import UIModel, run_ui
from textual.containers import VerticalScroll
from textual.widgets import Static

from codaro.agent import Agent
from codaro.jobs import CommandJob, JobManager
from codaro.llm.errors import RequestCancelled
from codaro.repository import Repository
from codaro.sessions import MAX_SESSION_BYTES, SessionStore
from codaro.ui.app import CodaroApp
from codaro.ui.conversations import compact_history
from codaro.ui.message_actions import MessageActions
from codaro.ui.messages import ArchivedAssistantMessage, AssistantMessage, DecisionMessage


def history(count, answer):
    return [
        [{"role": "user", "content": f"Pergunta {i}"}, {"role": "assistant", "content": answer}]
        for i in range(count)
    ]


def test_dense_restored_history_has_bounded_widgets_and_keeps_actions_and_decisions(tmp_path):
    answer = "## Resultado\n\n- Evidência\n- Validação\n\n```python\nvalue = 42\n```\n\n" * 12
    turns = history(30, answer)
    decisions = [[{"question": "Contrato?", "answer": f"Preservar {i}"}] for i in range(30)]
    store = SessionStore(tmp_path)
    store.save(turns, "test", decisions=decisions)
    model = UIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model), resume=True)

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            assert len(app._registry) < 250
            messages = list(app.query(ArchivedAssistantMessage))
            assert len(messages) == 30
            assert all(message.message_text == answer for message in messages)
            assert len(app.query(DecisionMessage)) == 30
            assert app.agent.turns == turns
            assert app.session_decisions == decisions
            messages[0].focus()
            await pilot.press("ctrl+o")
            assert isinstance(app.screen, MessageActions)
            assert app.screen.text == answer
            assert app.screen.codes[0] == ("python", "value = 42\n")
            assert not model.started.is_set()

    run_ui(scenario())


def test_live_history_compaction_keeps_order_source_and_recent_interactive_replies(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
    texts = [f"## Resposta {i}\n\n```python\nvalue = {i}\n```" for i in range(7)]

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            conversation = app.query_one("#conversation", VerticalScroll)
            await conversation.mount(*(AssistantMessage(text) for text in texts))
            await compact_history(app)
            await pilot.pause()
            replies = [
                child
                for child in conversation.children
                if isinstance(child, (AssistantMessage, ArchivedAssistantMessage))
            ]
            assert [reply.message_text for reply in replies] == texts
            assert len(app.query(AssistantMessage)) == 3
            assert len(app.query(ArchivedAssistantMessage)) == 4
            await compact_history(app)
            assert len(app.query(ArchivedAssistantMessage)) == 4
            replies[0].focus()
            await pilot.press("ctrl+o")
            assert app.screen.text == texts[0]

    run_ui(scenario())


def test_quit_signals_work_before_dom_teardown_and_stops_owned_descendants(tmp_path):
    child = (
        "import time; from pathlib import Path; p=Path('heartbeat');\n"
        "while True: p.write_text(str(time.time())); time.sleep(.02)"
    )
    parent = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "print('ready',flush=True); time.sleep(30)"
    )
    turns = history(10, "## Saved\n\n- Preserved\n" * 4)
    store = SessionStore(tmp_path)
    store.save(turns, "test")
    agent = Agent(Repository(tmp_path), UIModel())
    job_id = agent.jobs.start([sys.executable, "-u", "-c", parent])["job_id"]
    job = agent.jobs.jobs[job_id]
    app = CodaroApp(agent, resume=True)
    observed = []

    class QuitProbe(Static):
        def on_unmount(self):
            observed.append(
                app.cancelled.is_set()
                and app.shutdown_requested.is_set()
                and agent.services.stop.is_set()
                and job.cancelled.is_set()
            )

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await app.query_one("#conversation").mount(QuitProbe(""))
            for _ in range(200):
                if (tmp_path / "heartbeat").exists():
                    break
                await pilot.pause(0.01)
            assert (tmp_path / "heartbeat").exists()
            await pilot.press("ctrl+q")
        assert job.done.is_set()
        assert agent.jobs.poll(job_id)["state"] == "cancelled"
        before = (tmp_path / "heartbeat").read_bytes()
        await asyncio.sleep(0.1)
        assert (tmp_path / "heartbeat").read_bytes() == before

    try:
        run_ui(scenario())
        assert observed == [True]
        assert store.load() == turns
    finally:
        agent.close()


def test_job_cleanup_uses_one_shared_wait_budget(tmp_path, monkeypatch):
    manager = JobManager(Repository(tmp_path))
    waits = []
    now = [0.0]

    class SlowDone:
        def is_set(self):
            return False

        def wait(self, timeout):
            waits.append(timeout)
            now[0] += timeout

    for i in range(4):
        job = CommandJob(str(i), [], ".", "operation", "", 0)
        job.done = SlowDone()
        manager.jobs[job.id] = job
    monkeypatch.setattr("codaro.jobs.time.monotonic", lambda: now[0])
    manager.close()
    assert waits == [3, 0, 0, 0]
    assert all(job.cancelled.is_set() for job in manager.jobs.values())


def test_file_listing_can_be_cancelled_and_reaps_its_process(tmp_path, monkeypatch):
    real_popen = subprocess.Popen
    processes = []
    cancelled = threading.Event()
    timer = threading.Timer(0.1, cancelled.set)

    def slow_listing(args, **kwargs):
        process = real_popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        processes.append(process)
        timer.start()
        return process

    monkeypatch.setattr("codaro.repository.subprocess.Popen", slow_listing)
    try:
        with pytest.raises(RequestCancelled, match="cancelada"):
            Repository(tmp_path).files(cancelled=cancelled)
        assert len(processes) == 1
        assert processes[0].poll() is not None
        assert processes[0].stdout.closed
        assert processes[0].stderr.closed
    finally:
        timer.cancel()
        timer.join()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()


def test_cancellable_listing_preserves_ignore_and_file_policy(tmp_path):
    (tmp_path / "ok.py").write_text("x=1")
    (tmp_path / "ignored.py").write_text("x=2")
    (tmp_path / ".env").write_text("password")
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    repository = Repository(tmp_path)
    assert repository.files(cancelled=threading.Event()) == repository.files()
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RequestCancelled, match="cancelada"):
        repository.files(cancelled=cancelled)


def test_session_trimming_masks_once_and_keeps_longest_fitting_suffix(tmp_path, monkeypatch):
    secret = 'private-"key'
    store = SessionStore(tmp_path, secret)
    turns = history(50, "á🙂" * 8000 + secret)
    decisions = [[{"question": "Manter?", "answer": str(i)}] for i in range(50)]
    original_redact = store.redact
    calls = []

    def counted(value):
        calls.append(1)
        return original_redact(value)

    monkeypatch.setattr(store, "redact", counted)
    store.save(turns, "test", decisions=decisions)
    assert len(calls) == 1
    saved = json.loads(store.path.read_bytes())
    size = store.path.stat().st_size
    assert size <= MAX_SESSION_BYTES
    assert secret not in store.path.read_text()
    count = len(store.turns)
    assert 0 < count < 50
    assert store.decisions == decisions[-count:]
    assert store.turns == saved["turns"]
    assert "[REDACTED]" in store.turns[-1][-1]["content"]
    saved["turns"].insert(0, original_redact(turns[-count - 1]))
    saved["decisions"].insert(0, decisions[-count - 1])
    assert len(json.dumps(saved, ensure_ascii=False).encode()) > MAX_SESSION_BYTES
    assert turns[-1][-1]["content"].endswith(secret)
