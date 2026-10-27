"""Copied outside checkout by CI to exercise the installed package on each OS."""

import sys
import time

import pytest

from codaro.commands import run_command
from codaro.edits import EditManager
from codaro.jobs import JobManager
from codaro.repository import Repository


def test_native_edit_conflict_and_undo_preserve_exact_bytes(tmp_path):
    path = tmp_path / "código.py"
    original = b"\xef\xbb\xbfx = 1\r\ny = 2\r\n"
    path.write_bytes(original)
    repository = Repository(tmp_path)
    edits = EditManager(repository)
    edits.observe(repository.read_lines(path.name, 1, 2), original)
    proposal = edits.propose_many(
        path.name,
        [{"old_text": "x = 1", "new_text": "x = 9"}, {"old_text": "y = 2", "new_text": "y = 8"}],
        "Check native source edits",
    )
    edits.apply(proposal.id)
    undo = edits.propose_undo(proposal.checkpoint_id)
    edits.apply(undo.id)
    assert path.read_bytes() == original
    edits.observe(repository.read_lines(path.name, 1, 2), original)
    proposal = edits.propose_many(
        path.name, [{"old_text": "x = 1", "new_text": "x = 4"}], "Check conflict"
    )
    path.write_bytes(b"external = 7\n")
    with pytest.raises(ValueError):
        edits.apply(proposal.id)
    assert path.read_bytes() == b"external = 7\n"


def test_native_create_rename_delete_and_recovery(tmp_path):
    edits = EditManager(Repository(tmp_path))
    create = edits.prepare_operations(
        [{"kind": "create", "path": "nested/a.py", "content": "x=1\n"}], "Create"
    )[0]
    edits.apply(create.id)
    data = (tmp_path / "nested/a.py").read_bytes()
    edits.observe(edits.repository.read_lines("nested/a.py", 1, 1), data)
    rename = edits.prepare_operations(
        [{"kind": "rename", "path": "nested/a.py", "destination": "nested/b.py"}], "Rename"
    )
    for proposal in rename:
        edits.apply(proposal.id)
    assert not (tmp_path / "nested/a.py").exists()
    assert (tmp_path / "nested/b.py").read_bytes() == data
    edits.observe(edits.repository.read_lines("nested/b.py", 1, 1), data)
    deletion = edits.prepare_operations([{"kind": "delete", "path": "nested/b.py"}], "Delete")[0]
    edits.apply(deletion.id)
    assert not (tmp_path / "nested/b.py").exists()
    edits.apply(edits.propose_undo(deletion.checkpoint_id).id)
    assert (tmp_path / "nested/b.py").read_bytes() == data


def test_native_commands_output_and_background_cancellation(tmp_path):
    result = run_command(tmp_path, [sys.executable, "-c", 'print("native")'])
    assert result["exit_code"] == 0 and "native" in result["output"]
    manager = JobManager(Repository(tmp_path))
    try:
        started = manager.start(
            [sys.executable, "-u", "-c", 'import time; print("ready",flush=True); time.sleep(30)']
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = manager.poll(started["job_id"], wait_ms=100)
            if "ready" in result["output"]:
                break
        assert result["state"] == "running" and "ready" in result["output"]
        assert manager.cancel(started["job_id"])["state"] == "cancelled"
    finally:
        manager.close()
