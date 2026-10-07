import os

import pytest
from test_edits import manager_for, propose

from codaro.checkpoints import Checkpoints
from codaro.edits import EditManager
from codaro.repository import Repository


def test_checkpoint_undo_survives_restart_and_is_only_a_proposal(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    manager.apply(proposal.id)
    checkpoint = proposal.checkpoint_id
    assert Checkpoints(Repository(tmp_path)).list()[0]["can_undo"]
    restored = EditManager(Repository(tmp_path))
    undo = restored.propose_undo(checkpoint)
    assert path.read_bytes() == proposal.after
    assert undo.undo_of == checkpoint
    restored.apply(undo.id)
    assert path.read_bytes() == proposal.before
    rows = restored.checkpoints.list()
    assert rows[0]["can_undo"]
    assert next(item for item in rows if item["id"] == checkpoint)["state"] == "undone"
    if os.name == "posix":
        assert restored.checkpoints.path.stat().st_mode & 0o777 == 0o600


def test_undo_rechecks_at_review_and_at_application(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    manager.apply(proposal.id)
    undo = manager.propose_undo()
    path.write_bytes(b"External change\n")
    with pytest.raises(ValueError, match="mudou"):
        manager.apply(undo.id)
    assert path.read_bytes() == b"External change\n"
    with pytest.raises(ValueError, match="mudou"):
        EditManager(Repository(tmp_path)).propose_undo(proposal.checkpoint_id)


def test_checkpoint_failure_blocks_edit_before_changing_file(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    (tmp_path / ".codaro").mkdir()
    outside = tmp_path / "outside"
    outside.write_text("private")
    manager.checkpoints.path.symlink_to(outside)
    with pytest.raises(ValueError):
        manager.apply(proposal.id)
    assert path.read_bytes() == proposal.before
    assert outside.read_text() == "private"


def test_undo_rejects_pending_proposals_and_ignored_files(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    with pytest.raises(ValueError, match="pendentes"):
        manager.propose_undo()
    manager.apply(proposal.id)
    (tmp_path / ".codaroignore").write_text("code.py\n")
    assert not manager.checkpoints.list()[0]["can_undo"]
    with pytest.raises(ValueError):
        manager.propose_undo()


def test_undo_preserves_mode_bom_and_crlf(tmp_path):
    data = b"\xef\xbb\xbfx = 1\r\ny = 2\r\n"
    manager, path = manager_for(tmp_path, data)
    path.chmod(0o755)
    proposal = propose(manager)
    manager.apply(proposal.id)
    undo = manager.propose_undo()
    manager.apply(undo.id)
    assert path.read_bytes() == data
    assert path.stat().st_mode & 0o777 == 0o755


def test_checkpoint_status_write_failure_does_not_report_an_unapplied_edit(tmp_path, monkeypatch):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)

    def fail_mark(*args):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(manager.checkpoints, "mark", fail_mark)
    manager.apply(proposal.id)
    assert proposal.state == "applied"
    assert path.read_bytes() == proposal.after
    assert proposal.checkpoint_warning
    assert Checkpoints(Repository(tmp_path)).list()[0]["can_undo"]


def test_undo_of_file_without_final_newline_has_an_unambiguous_diff(tmp_path):
    manager, _ = manager_for(tmp_path, b"x = 1")
    proposal = propose(manager)
    manager.apply(proposal.id)
    undo = manager.propose_undo()
    assert "\\ No newline at end of file" in undo.diff
    assert "-x = 3\n" in undo.diff and "+x = 1\n" in undo.diff
