import httpx
import pytest
from test_tui import UIModel, run_ui
from textual.widgets import Button, Checkbox, Input, OptionList, TextArea

from codaro.agent import Agent
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.details import DetailsDialog
from codaro.ui.message_actions import FilePreview, MessageActions
from codaro.ui.plan import ExecutionPlan, PlanSteps
from codaro.ui.providers import ModelPicker, ProviderSetup
from codaro.ui.reviews import ChangesReview
from codaro.ui.sessions import SessionPicker


def visible(app):
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


def seed_plan(agent):
    agent.tasks.start("Plano de demonstração")
    agent.tasks.plan(
        [
            {
                "title": f"ETAPA_{i + 1}: validar contrato e consumidores existentes",
                "state": "done" if i < 18 else "doing" if i == 18 else "todo",
            }
            for i in range(24)
        ],
        ["Testes aprovados"],
    )


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35)])
def test_long_question_options_stay_visible_and_return_complete_choice(tmp_path, size):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    seed_plan(agent)
    app = CodaroApp(agent)
    replies = []
    question = {
        "question": "Qual contrato devemos preservar? " * 9,
        "explanation": "Esta decisão altera o contrato público. " * 12,
        "choices": [f"OPCAO_{i}: " + "preservar os consumidores existentes " * 5 for i in range(3)],
        "recommended": 0,
    }

    async def scenario():
        async with app.run_test(size=size) as pilot:
            app.show_user_question(question, replies.append)
            await pilot.pause()
            for i in range(3):
                assert f"OPCAO_{i}" in visible(app)
            await pilot.press("down", "down")
            assert "OPCAO_2" in visible(app)
            await pilot.click("#question-details")
            assert isinstance(app.screen, DetailsDialog)
            await pilot.press("escape")
            assert not replies and app.pending_user_question
            app.answer_user_question("")
            assert replies == [question["choices"][2]]

    run_ui(scenario())


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35)])
def test_plan_tracks_active_step_and_preserves_manual_scrolling(tmp_path, size):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    seed_plan(agent)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            plan = app.query_one(ExecutionPlan)
            scroll = app.query_one(PlanSteps)
            assert scroll.scroll_y > 0
            assert "ETAPA_19" in visible(app)
            scroll.action_scroll_home()
            await pilot.pause()
            app.show_plan()
            await pilot.pause()
            assert not plan.following and scroll.scroll_y == 0
            await pilot.click("#follow-plan")
            await pilot.pause()
            assert plan.following and scroll.scroll_y > 0
            plan.toggle()
            app.show_plan()
            await pilot.pause()
            assert not plan.expanded

    run_ui(scenario())


def test_message_copy_and_file_preview_are_local_and_preserve_draft(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("value = 1\nprint(value)\n")
    agent = Agent(Repository(tmp_path), UIModel())
    app = CodaroApp(agent)
    copied = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)

    async def scenario():
        async with app.run_test() as pilot:
            app.active_question = "Explique"
            app.finish("Veja `main.py:2`.\n\n```python\nprint(value)\n```\n")
            app.query_one(Prompt).value = "Rascunho mantido"
            await pilot.pause()
            await pilot.press("ctrl+o")
            assert isinstance(app.screen, MessageActions)
            app.screen.query_one(OptionList).highlighted = 1
            await pilot.press("enter")
            assert copied == ["print(value)\n"]
            await pilot.press("ctrl+o")
            actions = app.screen.query_one(OptionList)
            actions.highlighted = actions.get_option_index("file-0")
            await pilot.press("enter")
            assert isinstance(app.screen, FilePreview)
            assert app.screen.query_one(TextArea).read_only
            assert "value = 1" in app.screen.query_one(TextArea).text
            assert not agent.edits.observed
            await pilot.press("escape", "escape")
            assert app.query_one(Prompt).value == "Rascunho mantido"
            assert not agent.provider.started.is_set()

    run_ui(scenario())


def test_session_picker_search_archive_restore_and_resume(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel())
    identifier = agent.sessions.create("Análise da API")
    agent.sessions.store(identifier).save(
        [
            [
                {"role": "user", "content": "Contrato"},
                {"role": "assistant", "content": "Manter API v2."},
            ]
        ],
        "model",
    )
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test() as pilot:
            app.query_one(Prompt).value = "Meu rascunho"
            await app.open_provider_menu("/sessions")
            await pilot.pause()
            assert isinstance(app.screen, SessionPicker)
            app.screen.query_one(Input).value = "Análise da API"
            await pilot.pause()
            assert app.screen.query_one(OptionList).option_count == 1
            await pilot.click("#session-archive")
            await pilot.pause()
            assert app.screen.query_one(OptionList).option_count == 0
            app.screen.query_one(Checkbox).value = True
            await pilot.pause()
            assert app.screen.query_one("#session-archive", Button).label.plain == "Restaurar"
            # Textual debounces mouse clicks while the button's active effect runs.
            await pilot.pause(0.3)
            await pilot.click("#session-archive")
            await pilot.pause()
            await pilot.click("#session-use")
            for _ in range(30):
                await pilot.pause(0.01)
                if agent.session_id == identifier and "Manter API v2" in visible(app):
                    break
            await pilot.pause()
            assert agent.session_id == identifier
            assert app.query_one(Prompt).value == "Meu rascunho"
            assert "Manter API v2" in visible(app)

    run_ui(scenario())


def test_small_diff_shows_changes_without_defaulting_to_apply(tmp_path):
    path = tmp_path / "main.py"
    path.write_text("value = 1\n")
    agent = Agent(Repository(tmp_path), UIModel())
    agent.edits.observe(agent.repository.read_lines("main.py", 1, 1), path.read_bytes())
    result = agent.edits.propose("main.py", "value = 1", "value = 2", "Preservar contrato " * 20)
    proposal = agent.edits.proposals[result["proposal_id"]]
    app = CodaroApp(agent)
    replies = []

    async def scenario():
        async with app.run_test(size=(60, 20)) as pilot:
            app.push_screen(ChangesReview([proposal]), replies.append)
            await pilot.pause()
            assert "-value = 1" in visible(app)
            assert "+value = 2" in visible(app)
            assert app.focused.id == "back-edit"
            await pilot.press("enter")
            assert replies == ["back"] and path.read_text() == "value = 1\n"

    run_ui(scenario())


def test_provider_form_model_rows_and_auto_indicator_are_compact(tmp_path):
    store = ProviderStore(
        tmp_path / "config",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen-company-finetuned-production-model-version-very-long-name",
                            "context_window": 32768,
                        },
                        {"id": "small", "context_window": 8192},
                    ]
                },
            )
        ),
    )
    store.register(
        "openai-compatible", "test-key", name="Local", base_url="https://example.test/v1"
    )
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    agent.policy.auto_edits = True
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(60, 20)) as pilot:
            assert "Auto" in visible(app)
            app.push_screen(ProviderSetup(store))
            await pilot.pause()
            assert app.screen.query_one("#provider-form").region.height <= 18
            assert "Testar catálogo" in visible(app)
            assert "Salvar e escolher" in visible(app)
            await pilot.press("escape")
            app.push_screen(ModelPicker(store))
            await pilot.pause()
            assert "32.8k" in visible(app)
            assert "…" in visible(app)
            test = app.screen.query_one("#model-test")
            clip = app.screen._compositor.find_widget(test).clip
            assert test.region.intersection(clip) == test.region

    run_ui(scenario())
