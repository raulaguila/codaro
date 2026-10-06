import pytest

from codaro.index import CodeIndex
from codaro.repository import Repository


@pytest.fixture
def project(tmp_path):
    (tmp_path / "auth.py").write_text(
        "class Policy:\n"
        "    def can_edit(self, user):\n"
        '        """Check editing permissions."""\n'
        "        return user.is_admin\n",
        encoding="utf-8",
    )
    return tmp_path


def test_symbol_search_and_current_read(project):
    index = CodeIndex(Repository(project))
    try:
        assert index.update()["changed"] == 1
        assert index.update()["changed"] == 0
        results = index.search("Policy.can_edit")
        assert results[0]["symbol"] == "Policy.can_edit"
        result = index.read_symbol("auth.py", "Policy.can_edit")
        assert "return user.is_admin" in result["content"]
        assert result["start_line"] == 2
        assert index.search("editing permissions")
    finally:
        index.close()


def test_changed_and_deleted_files(project):
    index = CodeIndex(Repository(project))
    try:
        index.update()
        (project / "auth.py").write_text("def verify_token():\n    return True\n")
        assert index.update()["changed"] == 1
        assert not index.search("can_edit")
        assert index.search("verify_token")[0]["symbol"] == "verify_token"
        (project / "auth.py").unlink()
        assert index.update()["removed"] == 1
        assert not index.search("verify_token")
    finally:
        index.close()


def test_ignores_apply_to_search_and_direct_read(project):
    (project / ".gitignore").write_text("ignored.py\n")
    (project / ".codaroignore").write_text("private.py\n")
    for name in ["ignored.py", "private.py", ".env", "credentials.json"]:
        (project / name).write_text("confidential")
    repository = Repository(project)
    assert [path.name for path in repository.files()] == [".codaroignore", ".gitignore", "auth.py"]
    for name in ["ignored.py", "private.py", ".env", "credentials.json"]:
        with pytest.raises(ValueError):
            repository.read_lines(name)


def test_external_paths_and_symlinks_are_rejected(project, tmp_path_factory):
    external = tmp_path_factory.mktemp("outside") / "outside.py"
    external.write_text("private = True")
    (project / "linked.py").symlink_to(external)
    repository = Repository(project)
    with pytest.raises(ValueError):
        repository.read_lines(str(external))
    with pytest.raises(ValueError):
        repository.read_lines("linked.py")
    assert external not in repository.files()


def test_large_lines_are_truncated(project):
    (project / "long.py").write_text("x = '" + "a" * 10_000 + "'")
    result = Repository(project).read_lines("long.py", 1, 1)
    assert result["truncated"]
    assert len(result["content"]) == 6000


def test_index_symlink_rejected(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("storage")
    (project / ".codaro").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        CodeIndex(Repository(project))


def test_symbol_read_tracks_current_source_without_reindexing(project):
    with CodeIndex(Repository(project)) as index:
        index.update()
        original = (project / "auth.py").read_text()
        (project / "auth.py").write_text("# shifted\n\n" + original)
        result = index.read_symbol("auth.py", "Policy.can_edit")
        assert result["start_line"] == 4
        assert "return user.is_admin" in result["content"]


def test_class_read_includes_methods(project):
    with CodeIndex(Repository(project)) as index:
        result = index.read_symbol("auth.py", "Policy")
        assert "def can_edit" in result["content"]
        assert "return user.is_admin" in result["content"]


def test_duplicate_names_require_disambiguation(project):
    (project / "auth.py").write_text("def f():\n    return 1\n\ndef f():\n    return 2\n")
    with CodeIndex(Repository(project)) as index:
        with pytest.raises(ValueError, match="ambíguo"):
            index.read_symbol("auth.py", "f")
        assert "return 2" in index.read_symbol("auth.py", "f", 4)["content"]


def test_long_symbol_includes_continuation_information(project):
    (project / "auth.py").write_text("def large():\n" + "    x = 1\n" * 200)
    with CodeIndex(Repository(project)) as index:
        result = index.read_symbol("auth.py", "large")
        assert result["truncated"]
        assert result["symbol_end_line"] == 201
        assert result["next_start_line"] == 161


def test_search_refreshes_ignore_changes(project):
    with CodeIndex(Repository(project)) as index:
        assert index.search("can_edit")
        (project / ".codaroignore").write_text("auth.py\n")
        assert not index.search("can_edit")


def test_search_preserves_terms_after_fortieth_word(project):
    (project / "data.js").write_text(" ".join(f"word{i}" for i in range(80)) + " uniqueTailTerm")
    with CodeIndex(Repository(project)) as index:
        assert index.search("uniqueTailTerm")[0]["path"] == "data.js"


def test_acronym_and_camelcase_search(project):
    (project / "http.py").write_text("def HTTPRequestHandler():\n    pass\n")
    with CodeIndex(Repository(project)) as index:
        assert index.search("request handler")[0]["symbol"] == "HTTPRequestHandler"


def test_exact_method_outranks_calls_and_class(project):
    with CodeIndex(Repository(project)) as index:
        assert index.search("can_edit")[0]["symbol"] == "Policy.can_edit"


def test_binary_and_non_utf8_are_skipped(project):
    (project / "binary.py").write_bytes(b"hello\0world")
    (project / "invalid.py").write_bytes(b"\xff\xfe")
    with CodeIndex(Repository(project)) as index:
        assert index.update()["skipped"] == 2
        assert not index.search("hello")
    with pytest.raises(ValueError):
        Repository(project).read_text("invalid.py")


def test_empty_file_read(project):
    (project / "empty.py").touch()
    result = Repository(project).read_lines("empty.py")
    assert result["total_lines"] == 0
    assert result["content"] == ""


@pytest.mark.parametrize("start,end", [(0, 1), (4, 2), (1, 161), (True, 3)])
def test_invalid_line_ranges(project, start, end):
    with pytest.raises(ValueError):
        Repository(project).read_lines("auth.py", start, end)


def test_nested_generated_directory_is_ignored(project):
    generated = project / "sub" / "node_modules"
    generated.mkdir(parents=True)
    (generated / "generated.py").write_text("should_not_be_indexed = True")
    assert "generated.py" not in [path.name for path in Repository(project).files()]


def test_newline_filename_does_not_break_file_listing(project):
    (project / "bad\nname.py").write_text("x = 1")
    assert [path.name for path in Repository(project).files()] == ["auth.py"]


def test_terminal_controls_are_sanitized(project):
    (project / "control.py").write_text("# \x1b[31m red")
    assert "\x1b" not in Repository(project).read_lines("control.py")["content"]
    with CodeIndex(Repository(project)) as index:
        assert "\x1b" not in index.search("red")[0]["preview"]


def test_bom_and_crlf_line_numbers_match_parser(project):
    (project / "bom.py").write_bytes(b"\xef\xbb\xbf# header\r\ndef f():\r\n    return 1\r\n")
    with CodeIndex(Repository(project)) as index:
        assert index.search("f")[0]["start_line"] == 2
        result = index.read_symbol("bom.py", "f")
        assert result["content"].startswith("2: def f():")
        assert "\r" not in result["content"]


def test_ripgrep_timeout_is_readable(project, monkeypatch):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("rg", 20)

    monkeypatch.setattr("codaro.repository.subprocess.run", timeout)
    with pytest.raises(ValueError, match="20 segundos"):
        Repository(project).files()


def test_character_limit_does_not_add_extra_newline(project):
    (project / "boundary.py").write_text("a" * 5997 + "\nsecond line\n")
    result = Repository(project).read_lines("boundary.py", 1, 2)
    assert len(result["content"]) == 6000
    assert result["truncated"]
    assert result["end_line"] == 1
    assert result["next_start_line"] == 2


def test_symbol_continuation_respects_character_truncation(project):
    (project / "large.py").write_text(
        "def large():\n" + '    x = "' + "a" * 7000 + '"\n' + "    return x\n"
    )
    with CodeIndex(Repository(project)) as index:
        result = index.read_symbol("large.py", "large")
        assert result["truncated"]
        assert result["partial_line"] == 2
        assert result["next_start_line"] == 2
        assert result["symbol_end_line"] == 3


def test_file_read_rejects_fifo_without_blocking(project):
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("POSIX only")
    fifo = project / "fifo.py"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        Repository(project).read_bytes(fifo)


def test_legacy_index_is_migrated(project):
    import sqlite3

    storage = project / ".codaro"
    storage.mkdir()
    with sqlite3.connect(storage / "index.sqlite3") as db:
        db.executescript(
            "CREATE TABLE files(path TEXT, digest TEXT); CREATE TABLE chunks(id INTEGER);"
        )
    with CodeIndex(Repository(project)) as index:
        assert index.search("can_edit")
        assert index.db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_index_transaction_rolls_back_on_parser_failure(project, monkeypatch):
    with CodeIndex(Repository(project)) as index:
        index.update()
        before = list(index.db.execute("SELECT path,digest FROM files"))
        (project / "auth.py").write_text("def changed(): pass")

        def fail(*args):
            raise RuntimeError("Parser failed")

        monkeypatch.setattr("codaro.index.chunks_for", fail)
        with pytest.raises(RuntimeError):
            index.update()
        assert list(index.db.execute("SELECT path,digest FROM files")) == before


def test_exact_method_does_not_repeat_enclosing_class(project):
    with CodeIndex(Repository(project)) as index:
        results = index.search("can_edit")
        assert results[0]["symbol"] == "Policy.can_edit"
        assert "Policy" not in {item["symbol"] for item in results}
