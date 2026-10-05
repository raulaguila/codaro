import os

import pytest

from codaro.edits import EditManager
from codaro.repository import Repository


def manager_for(tmp_path, data=b"x = 1\ny = 2\n", start=1, end=2):
    path = tmp_path / "code.py"
    path.write_bytes(data)
    manager = EditManager(Repository(tmp_path))
    result = manager.repository.read_lines("code.py", start, end)
    manager.observe(result, data)
    return manager, path


def propose(manager, old="x = 1", new="x = 3"):
    output = manager.propose("code.py", old, new, "Corrigir valor")
    return manager.proposals[output["proposal_id"]]


def test_proposal_is_only_a_diff_until_apply(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    assert path.read_bytes() == proposal.before
    assert "-x = 1" in proposal.diff
    assert "+x = 3" in proposal.diff
    manager.apply(proposal.id)
    assert path.read_bytes() == b"x = 3\ny = 2\n"
    assert proposal.state == "applied"
    with pytest.raises(ValueError, match="resolvida"):
        manager.apply(proposal.id)


def test_reject_preserves_file(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    manager.reject(proposal.id)
    assert path.read_bytes() == proposal.before
    assert proposal.state == "rejected"
    with pytest.raises(ValueError):
        manager.apply(proposal.id)


def test_concurrent_edit_blocks_application(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    path.write_bytes(b"Changed by editor\n")
    with pytest.raises(ValueError, match="mudou"):
        manager.apply(proposal.id)
    assert path.read_bytes() == b"Changed by editor\n"
    assert proposal.state == "conflict"


@pytest.mark.parametrize(
    "data", [b"\xef\xbb\xbfx = 1\r\ny = 2\r\n", b"x = 1\ny = 2", b"x = 1\r\ny = 2\r\n"]
)
def test_preserves_bom_newlines_and_executable_mode(tmp_path, data):
    manager, path = manager_for(tmp_path, data)
    path.chmod(0o755)
    proposal = propose(manager, "x = 1\ny = 2", "x = 4\ny = 8")
    manager.apply(proposal.id)
    assert path.read_bytes() == data.replace(b"1", b"4").replace(b"2", b"8")
    assert path.stat().st_mode & 0o777 == 0o755
    if not data.endswith(b"\n"):
        assert "No newline at end of file" in proposal.diff


def test_requires_read_and_unique_match(tmp_path):
    manager, path = manager_for(tmp_path, b"x = 1\nx = 1\n")
    with pytest.raises(ValueError, match="exatamente uma"):
        propose(manager)
    manager.observed.clear()
    with pytest.raises(ValueError, match="Leia novamente"):
        propose(manager)


def test_requires_read_of_entire_replacement(tmp_path):
    manager, _ = manager_for(tmp_path, start=1, end=1)
    with pytest.raises(ValueError, match="inteiramente"):
        propose(manager, "y = 2", "y = 5")


def test_changed_file_requires_new_read(tmp_path):
    manager, path = manager_for(tmp_path)
    path.write_text("x = 1\ny = 7\n")
    with pytest.raises(ValueError, match="Leia novamente"):
        propose(manager)


def test_budget_truncation_cannot_authorize_hidden_lines(tmp_path):
    manager, _ = manager_for(tmp_path)
    manager.observed.clear()
    result = manager.repository.read_lines("code.py", 1, 2)
    result["content"] = "1: x = 1\n2: y"
    manager.observe(result, b"x = 1\ny = 2\n")
    with pytest.raises(ValueError, match="inteiramente"):
        propose(manager, "y = 2", "y = 5")
    propose(manager)


def test_symlink_and_new_ignore_block_apply(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    outside = tmp_path / "outside.py"
    outside.write_text("private")
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        manager.apply(proposal.id)
    assert outside.read_text() == "private"
    path.unlink()
    path.write_bytes(proposal.before)
    manager.observe(manager.repository.read_lines("code.py", 1, 2), proposal.before)
    proposal = propose(manager)
    (tmp_path / ".codaroignore").write_text("code.py\n")
    with pytest.raises(ValueError):
        manager.apply(proposal.id)
    assert path.read_bytes() == proposal.before


def test_hard_link_is_rejected(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    link = tmp_path / "linked.py"
    os.link(path, link)
    with pytest.raises(ValueError, match="hard links"):
        manager.apply(proposal.id)
    assert link.read_bytes() == proposal.before


def test_replacement_failure_preserves_source_and_cleans_temporary(tmp_path, monkeypatch):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)

    def fail(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr("codaro.edits.os.replace", fail)
    with pytest.raises(OSError):
        manager.apply(proposal.id)
    assert path.read_bytes() == proposal.before
    assert not list(tmp_path.glob(".codaro-edit-*"))


def test_change_during_staging_blocks_apply(tmp_path, monkeypatch):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    sync = os.fsync

    def change(fd):
        path.write_bytes(b"external change")
        sync(fd)

    monkeypatch.setattr("codaro.edits.os.fsync", change)
    with pytest.raises(ValueError, match="mudou"):
        manager.apply(proposal.id)
    assert path.read_bytes() == b"external change"
    assert not list(tmp_path.glob(".codaro-edit-*"))


def test_duplicate_and_noop_proposals_are_rejected(tmp_path):
    manager, _ = manager_for(tmp_path)
    with pytest.raises(ValueError, match="não altera"):
        propose(manager, new="x = 1")
    propose(manager)
    with pytest.raises(ValueError, match="pendente"):
        propose(manager, "y = 2", "y = 3")


def test_empty_replacement_deletes_a_read_fragment(tmp_path):
    manager, path = manager_for(tmp_path)
    proposal = propose(manager, "x = 1\n", "")
    manager.apply(proposal.id)
    assert path.read_bytes() == b"y = 2\n"
