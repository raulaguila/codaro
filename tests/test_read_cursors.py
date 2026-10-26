"""Continuation cursors must make progress without granting unseen-line access."""

import json

import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent
from codaro.agent.results import fit_result
from codaro.edits import EditManager
from codaro.index import CodeIndex
from codaro.repository import Repository


def test_long_unicode_line_can_be_recovered_without_missing_or_duplicate_characters():
    text = "á🙂" * 7000 + "TAIL_MARKER"
    pieces, column = [], 0
    for _ in range(100):
        page = Repository.render_lines("data.json", text, 1, 1, column=column, max_chars=500)
        pieces.append(page["content"].removeprefix("1: "))
        if page["next_start_line"] is None:
            break
        assert page["next_start_line"] == 1
        assert page["next_column"] > column
        column = page["next_column"]
    else:
        pytest.fail("Continuation did not finish")
    assert "".join(pieces) == text


def test_result_budget_adjusts_cursor_relative_to_start_column():
    text = "á" * 9000
    page = Repository.render_lines("data.json", text, 1, 1, column=100)
    reduced = fit_result(page, 600)
    received = reduced["content"].removeprefix("1: ")
    assert reduced["next_column"] == 100 + len(received)
    following = Repository.render_lines("data.json", text, 1, 1, column=reduced["next_column"])
    assert (
        received + following["content"].removeprefix("1: ")
        == text[100 : 100 + len(received) + 5997]
    )


def test_agent_reads_tail_instead_of_caching_partial_line(tmp_path):
    (tmp_path / "data.json").write_text("x" * 7000 + "TAIL_MARKER")
    model = FakeModel(
        [
            call("read_lines", {"path": "data.json", "start": 1, "end": 1}),
            call("read_lines", {"path": "data.json", "start": 1, "end": 1, "column": 5997}, "tail"),
            {"content": "O fim contém TAIL_MARKER."},
        ]
    )
    Agent(Repository(tmp_path), model, persist_memory=False).ask("Leia a linha 1 de data.json.")
    results = [
        json.loads(item["content"]) for item in model.requests[-1][0] if item["role"] == "tool"
    ]
    assert results[0]["next_column"] == 5997
    assert "TAIL_MARKER" in results[-1]["content"]
    assert not results[-1].get("already_read")


def test_partial_read_can_be_retried_and_changed_snapshot_is_rejected(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("x" * 7000)
    agent = Agent(Repository(tmp_path), FakeModel([]))
    with CodeIndex(agent.repository) as index:
        first = agent.execute(index, "read_lines", {"path": "data.json", "start": 1, "end": 1})
        path.write_text("y" * 7000)
        with pytest.raises(ValueError, match="mudou"):
            agent.execute(
                index,
                "read_lines",
                {
                    "path": "data.json",
                    "start": 1,
                    "end": 1,
                    "column": first["next_column"],
                    "file_hash": first["file_hash"],
                },
            )


def test_continuation_does_not_authorize_unseen_beginning_of_line(tmp_path):
    path = tmp_path / "code.py"
    raw = b"UNSEEN = 1; " + b"x" * 7000 + b"\n"
    path.write_bytes(raw)
    repository = Repository(tmp_path)
    manager = EditManager(repository)
    page = Repository.render_lines("code.py", raw.decode(), 1, 1, column=6500)
    manager.observe(page, raw)
    assert not manager.observed
    with pytest.raises(ValueError, match="Leia novamente"):
        manager.propose("code.py", "UNSEEN = 1", "UNSEEN = 2", "Alterar")


@pytest.mark.parametrize("column,max_chars", [(-1, 500), (True, 500), (0, 199), (0, 6001)])
def test_cursor_limits_are_validated(column, max_chars):
    with pytest.raises(ValueError):
        Repository.render_lines("data.json", "text", 1, 1, column=column, max_chars=max_chars)
