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


def test_reasoning_steps_share_one_group_and_respect_collapse(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            app.append_reasoning("Primeira etapa.")
            await pilot.pause()
            group = app.reasoning_group
            assert group.collapsed
            group.collapsed = False
            app.finish_preview("tools")
            app.append_reasoning("Segunda etapa.")
            await pilot.pause()
            assert not group.collapsed
            group.collapsed = True
            app.finish_preview("tools")
            app.append_reasoning("Terceira etapa.")
            await pilot.pause()
            assert group.collapsed
            assert len(app.query(ReasoningGroup)) == 1
            assert group.steps == 3
            assert len(group.query(".reasoning-preview")) == 3

    run_ui(scenario())
