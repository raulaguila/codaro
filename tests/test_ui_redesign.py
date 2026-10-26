"""Native acceptance for the approved composer and persistent plan layout."""

import asyncio

import pytest
from test_tui import UIModel, run_ui, wait_ready
from textual.containers import VerticalScroll
from textual.widgets import OptionList, RadioButton, Static

from codaro.agent import Agent
from codaro.index import CodeIndex
from codaro.policies import Mode
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Composer, Prompt
from codaro.ui.modes import ModePicker
from codaro.ui.plan import ExecutionPlan


def start_plan(agent, count=4):
    agent.tasks.start("Corrigir a autenticação")
    agent.tasks.plan(
        [{"title": f"Etapa {i + 1} da implementação", "state": "todo"} for i in range(count)],
        ["Verificação aprovada"],
    )


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35), (160, 50)])
def test_plan_and_input_stay_visible_while_chat_scrolls(tmp_path, size):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    start_plan(agent, 24)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=size) as pilot:
            plan = app.query_one(ExecutionPlan)
            assert plan.display and plan.expanded
            assert plan.parent is app.query_one(Composer)
            conversation = app.query_one("#conversation", VerticalScroll)
            for i in range(20):
                app.mount_message(Static(f"Mensagem {i}\n" * 3))
            await pilot.pause()
            conversation.scroll_home(animate=False)
            await pilot.pause()
            before = plan.region
            await pilot.press("/")
            await pilot.pause()
            assert conversation.region.height >= 4
            for widget in (plan, app.query_one(Prompt), app.query_one("#prompt-hint")):
                clip = app.screen._compositor.find_widget(widget).clip
                assert widget.region.intersection(clip) == widget.region
            assert plan.region.y >= conversation.region.bottom
            assert before.height == plan.region.height
            await pilot.press("escape")
            await pilot.click("#toggle-plan")
            assert not plan.expanded
            agent.tasks.plan([{"title": "Próxima etapa", "state": "doing"}], [])
            app.show_plan()
            await pilot.pause()
            assert not plan.expanded and plan.display
            assert not app.query_one("#plan-steps").display
            await app.action_clear_chat()
            assert plan.display and not plan.expanded
            agent.tasks.state("completed")
            app.show_plan()
            assert not plan.display

    run_ui(scenario())


def test_mode_selection_is_explicit_and_preserves_draft(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel(), mode="ask")
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            prompt = app.query_one(Prompt)
            prompt.value = "Rascunho ainda não enviado"
            prompt.move_cursor((0, 8))
            selection = prompt.selection
            await pilot.click("#select-mode")
            await pilot.pause()
            assert isinstance(app.screen, ModePicker)
            await pilot.press("down", "down")
            assert agent.mode == Mode.ASK
            assert app.screen.query_one(OptionList).highlighted == 2
            await pilot.press("escape")
            assert agent.mode == Mode.ASK and not agent.policy.auto_edits
            assert prompt.value == "Rascunho ainda não enviado" and prompt.selection == selection
            app.open_mode_picker()
            await pilot.pause()
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert agent.mode == Mode.EXECUTE and not agent.policy.auto_edits
            app.open_mode_picker()
            await pilot.pause()
            assert app.screen.query_one("#edit-policy").display
            await pilot.click("#edit-auto")
            assert app.screen.query_one("#edit-auto", RadioButton).value
            assert not agent.policy.auto_edits
            await pilot.click("#mode-apply")
            await pilot.pause()
            assert agent.policy.auto_edits
            assert "automáticas" in str(app.query_one("#prompt-hint", Static).render())
            start_plan(agent)
            identifier = agent.tasks.current()["id"]
            previous_store = agent.tasks
            await app.reset_conversation("")
            assert not agent.policy.auto_edits
            previous = next(t for t in previous_store.load()["tasks"] if t["id"] == identifier)
            assert previous["state"] == "cancelled"

    run_ui(scenario())


def test_slash_menu_enter_opens_highlighted_command(tmp_path):
    model = UIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model, mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("/", "m", "o")
            await pilot.pause()
            assert app.query_one(Prompt).suggesting
            assert app.completion_options()[0].value.strip() == "/models"
            await pilot.press("down", "enter")
            await pilot.pause()
            assert isinstance(app.screen, ModePicker)
            assert not model.started.is_set()
            await pilot.press("escape")
            assert app.focused is app.query_one(Prompt)

    run_ui(scenario())


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35), (160, 50)])
def test_expanded_mode_policy_keeps_confirmation_visible(tmp_path, size):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="execute"))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            await pilot.click("#select-mode")
            await pilot.pause()
            assert app.screen.query_one("#edit-policy").display
            for selector in ("#mode-apply", "#mode-help"):
                button = app.screen.query_one(selector)
                clip = app.screen._compositor.find_widget(button).clip
                assert button.region.intersection(clip) == button.region
            before = app.screen.query_one("#mode-apply").region
            await pilot.hover("#mode-apply")
            await pilot.pause()
            assert app.screen.query_one("#mode-apply").region == before
            text = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
            assert "Aplicar" in text and "Esc fechar" in text
            await pilot.press("escape")
            assert not app.agent.policy.auto_edits

    run_ui(scenario())


def test_interrupt_generation_preserves_plan_until_explicit_cancellation(tmp_path):
    model = UIModel(blocked=True)
    agent = Agent(Repository(tmp_path), model, mode="execute")
    start_plan(agent)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            await app.submit_question("Continue o plano")
            try:
                assert await asyncio.to_thread(model.started.wait, 5)
                await pilot.press("ctrl+x")
            finally:
                model.release.set()
            await wait_ready(app, pilot)
            plan = app.query_one(ExecutionPlan)
            assert plan.display and agent.tasks.current()["state"] == "blocked"
            await pilot.click("#cancel-plan")
            await pilot.pause()
            assert not plan.display and agent.tasks.current()["state"] == "cancelled"

    run_ui(scenario())


def test_automatic_edits_do_not_authorize_deletion_or_commands(tmp_path):
    decisions = []
    agent = Agent(
        Repository(tmp_path),
        UIModel(),
        mode="execute",
        approve_edit=lambda *args: decisions.append(args) or False,
    )
    agent.policy.auto_edits = True
    with CodeIndex(agent.repository) as index:
        created = agent.execute(
            index,
            "apply_changes",
            {
                "reason": "Criar exemplo",
                "operations": [
                    {"kind": "create", "path": "example.py", "content": "value = 1\n"},
                ],
            },
        )
        assert created["state"] == "applied" and not decisions
        result = agent.execute(index, "read_lines", {"path": "example.py", "start": 1, "end": 1})
        agent.edits.observe(result, (tmp_path / "example.py").read_bytes())
        removed = agent.execute(
            index,
            "apply_changes",
            {
                "reason": "Remover exemplo",
                "operations": [{"kind": "delete", "path": "example.py"}],
            },
        )
    assert removed["state"] == "rejected" and decisions
    assert (tmp_path / "example.py").exists()
    assert not agent.policy.permits_command(agent.tasks.current()["id"], ["pytest"])
    agent.set_mode("ask")
    assert not agent.policy.auto_edits


def test_cancelling_active_plan_waits_for_generation_and_preserves_files(tmp_path):
    original = tmp_path / "existing.py"
    original.write_text("value = 1\n")
    model = UIModel(blocked=True)
    agent = Agent(Repository(tmp_path), model, mode="execute")
    start_plan(agent)
    agent.policy.auto_edits = True
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await app.submit_question("Continue o plano")
            try:
                assert await asyncio.to_thread(model.started.wait, 5)
                await pilot.click("#cancel-plan")
                assert app.cancel_plan_id == agent.tasks.current()["id"]
                assert app.cancelled.is_set()
            finally:
                model.release.set()
            await wait_ready(app, pilot)
            assert agent.tasks.current()["state"] == "cancelled"
            assert not app.query_one(ExecutionPlan).display
            assert not agent.policy.auto_edits
            assert original.read_text() == "value = 1\n"

    run_ui(scenario())


def test_model_selection_clears_onboarding_and_previous_context_meter(tmp_path):
    model = UIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model, mode="ask"), setup_required=True)
    app.start_setup = lambda: None

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            assert app.query_one("#welcome").display
            assert app.query_one("#configure-start").display
            app.query_one("#context-meter", Static).update("Contexto antigo · 99%")
            app.activate_provider(model.settings)
            await pilot.pause()
            assert not app.query_one("#welcome").display
            assert str(app.query_one("#context-meter", Static).render()) == ""
            assert "Janela:" in app.query_one("#context-meter", Static).tooltip
            assert not app.query(".notice")
            assert "Modelo selecionado" in str(app.query_one("#status", Static).render())

    run_ui(scenario())
