import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from codaro.agent import EDIT_TOOL, TOOLS, Agent
from codaro.anthropic import Anthropic
from codaro.commands import run_command
from codaro.context import TokenCounter
from codaro.index import CodeIndex
from codaro.provider import Settings, build_payload
from codaro.repository import Repository
from codaro.sessions import SessionStore
from codaro.trace import PromptFlow


class NoNetworkProvider:
    settings = Settings("http://localhost:11434/v1", "audit-model")


results = {}
with tempfile.TemporaryDirectory(prefix="codaro-audit-") as temporary:
    base = Path(temporary)
    project = base / "hardlink-project"
    (project / ".codaro").mkdir(parents=True)
    victim = base / "outside.sqlite3"
    with sqlite3.connect(victim) as db:
        db.execute("CREATE TABLE files(path TEXT PRIMARY KEY, digest TEXT NOT NULL)")
        db.execute("INSERT INTO files VALUES ('outside.py', 'original')")
    victim.chmod(0o644)
    os.link(victim, project / ".codaro/index.sqlite3")
    with CodeIndex(Repository(project)):
        pass
    with sqlite3.connect(victim) as db:
        rows = db.execute("SELECT path FROM files").fetchall()
    results["index_hardlink"] = {
        "outside_rows_before": ["outside.py"],
        "outside_rows_after": rows,
        "outside_mode_after": oct(victim.stat().st_mode & 0o777),
    }

    project = base / "validation-project"
    project.mkdir()
    (project / "x.py").write_text("x = 1\n")
    agent = Agent(
        Repository(project), NoNetworkProvider(), mode="execute", approve_command=lambda *args: True
    )
    agent.tasks.start("Test validation revision binding")
    argv = [sys.executable, "-c", "from pathlib import Path; Path('x.py').write_text('x = 2\\n')"]
    agent.tasks.plan([], [], [argv])
    with CodeIndex(agent.repository) as index:
        index.update()
        agent.sync_workspace(index)
        before = agent.tasks.current()["revision"]
        result = agent.execute(index, "run_command", {"argv": argv, "purpose": "validation"})
        task = agent.tasks.current()
        completion = agent.execute(index, "finish_task", {"status": "completed", "summary": "done"})
    results["validation_mutates_sources"] = {
        "revision_before": before,
        "revision_after": task["revision"],
        "validation_revision": task["validations"][-1]["revision"],
        "exit_code": result["exit_code"],
        "validation_ready": agent.tasks.validation_ready(),
        "completion": completion["state"],
    }

    project = base / "command-project"
    project.mkdir()
    outside = base / "outside.txt"
    os.environ["CODARO_AUDIT_FAKE_KEY"] = "fake-key-for-audit"
    argv = [
        sys.executable,
        "-c",
        "import os; from pathlib import Path; "
        + "Path("
        + repr(str(outside))
        + ").write_text(os.environ['CODARO_AUDIT_FAKE_KEY'])",
    ]
    run_command(project, argv)
    results["command_isolation"] = {
        "wrote_outside_project": outside.exists(),
        "inherited_custom_key_env": outside.read_text() == "fake-key-for-audit",
    }
    del os.environ["CODARO_AUDIT_FAKE_KEY"]

    project = base / "warm-index-project"
    project.mkdir()
    for number in range(200):
        (project / f"file{number}.py").write_text(f"def function{number}():\n    return {number}\n")
    repository = Repository(project)
    with CodeIndex(repository) as index:
        index.update()
        original = repository.read_bytes
        reads = []

        def counted(path):
            reads.append(path)
            return original(path)

        repository.read_bytes = counted
        index.search("function42")
    results["warm_search"] = {"project_files": 200, "file_reads_during_warm_search": len(reads)}

    project = base / "redaction-project"
    project.mkdir()
    previous_key, current_key = "fake-previous-key-123", "fake-current-key-456"
    turns = [
        [
            {"role": "user", "content": "debug " + previous_key},
            {"role": "assistant", "content": "ok"},
        ]
    ]
    session = SessionStore(project, previous_key)
    session.save(turns, "previous-model")
    protected_before = previous_key not in session.path.read_text()
    session.secret = current_key  # Same assignment as CodaroApp.activate_provider.
    session.save(turns, "current-model")
    flow = PromptFlow(
        project,
        "next question",
        Settings("http://localhost/v1", "new", current_key),
        allow_edits=False,
        limits={},
    )
    flow.add_turn({"messages": turns[0]}, {})
    results["redaction_after_provider_switch"] = {
        "previous_key_protected_before_switch": protected_before,
        "previous_key_in_saved_session_after_switch": previous_key in session.path.read_text(),
        "previous_key_in_new_flow": previous_key in flow.path.read_text(),
    }

counter = TokenCounter()
payload = build_payload(
    "audit-model",
    [{"role": "system", "content": "system " * 100}, {"role": "user", "content": "question"}],
    [*TOOLS, EDIT_TOOL],
)
native = Anthropic.wire_payload(payload)
normalized_count = counter.base_count(payload)
native_count = counter.base_count(native)
counter.observe(payload, native_count)
results["anthropic_calibration"] = {
    "normalized_base": normalized_count,
    "native_base": native_count,
    "observed_ratio_using_normalized": counter.samples[-1],
    "correct_ratio_for_native_estimate": 1.0,
}
print(json.dumps(results, indent=2))
