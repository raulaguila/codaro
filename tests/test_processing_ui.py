from test_tui import UIModel, run_ui

from codaro.agent import Agent, AgentEvent
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.widgets import ActivityGroup, ReasoningGroup


def test_activity_updates_preserve_manual_collapse_after_error(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            group = ActivityGroup()
            await app.mount(group)
            group.add(AgentEvent("tool_end", "Ler arquivo", "Falha", state="error"))
            group.collapsed = False
            await pilot.pause()
            group.collapsed = True
            group.add(AgentEvent("tool_end", "Buscar código", "OK", state="success"))
            await pilot.pause()
            assert group.collapsed
            assert "1 erro" in group.title

    run_ui(scenario())


def test_reasoning_steps_are_chronological_and_keep_manual_collapse(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            app.append_reasoning("Primeira etapa.")
            await pilot.pause()
            group = app.reasoning_group
            assert not group.collapsed
            group.collapsed = True
            app.finish_preview("tools")
            app.activity(AgentEvent("tool_end", "Ler arquivo", "main.py", state="success"))
            app.append_reasoning("Segunda etapa.")
            await pilot.pause()
            assert group.collapsed
            assert app.reasoning_group is not group
            assert not app.reasoning_group.collapsed
            assert len(app.query(ReasoningGroup)) == 2
            assert len(app.query(".reasoning-preview")) == 2
            sequence = app.activity_group.sequence
            assert sequence[0] is group
            assert "Ler arquivo" in str(sequence[1].render())
            assert sequence[2] is app.reasoning_group

    run_ui(scenario())
