import json

import pytest

from codaro.agent import Agent
from codaro.repository import Repository


def test_explicit_references_and_agents_are_bounded_traced_and_read_before_model(tmp_path):
    from test_agent import FakeModel

    (tmp_path / "main file.py").write_text("def main(): return 1\n")
    (tmp_path / "AGENTS.md").write_text("Use pytest para validar.\n" + "x" * 10000)
    model = FakeModel([{"content": "main file.py:1 define main."}])
    events = []
    Agent(Repository(tmp_path), model).ask('Explique @"main file.py".', on_detail=events.append)
    system = model.requests[0][0][0]["content"]
    assert "Use pytest para validar" in system
    assert "def main(): return 1" in system
    assert "x" * 10000 in system
    assert len(system) < 24_000
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert len(flow["local_retrievals"]) == 2
    assert len([event for event in events if event.kind == "tool_end"]) == 2
    assert not any(message["role"] == "tool" for message in model.requests[0][0])


@pytest.mark.parametrize("reference", ["@missing.py", "@.env", "@../outside.py"])
def test_bad_or_protected_references_do_not_reach_model(tmp_path, reference):
    from test_agent import FakeModel

    (tmp_path / ".env").write_text("private")
    model = FakeModel([])
    with pytest.raises(ValueError, match="Referência"):
        Agent(Repository(tmp_path), model).ask("Leia " + reference)
    assert not model.requests
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["status"] == "error"


def test_explicit_read_can_be_used_for_edit_observation(tmp_path):
    from test_agent import FakeModel, call

    path = tmp_path / "main.py"
    path.write_text("x = 1\n")
    model = FakeModel(
        [
            call(
                "propose_edit",
                {"path": "main.py", "old_text": "x = 1", "new_text": "x = 2", "reason": "Pedido"},
            ),
            {"content": "Diff proposto."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, allow_edits=True)
    agent.ask("Mude x em @main.py")
    assert len(agent.edits.pending) == 1
    assert path.read_text() == "x = 1\n"
