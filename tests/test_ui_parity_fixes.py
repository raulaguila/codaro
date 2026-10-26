"""Regressions for the complete approved UI flow, including lifecycle boundaries."""

import json

import httpx
import pytest
from test_tui import UIModel, run_ui
from textual.widgets import Button, Collapsible, Input, Select

from codaro.agent import Agent
from codaro.llm.profiles import ProviderStore
from codaro.policies import Mode
from codaro.repository import Repository
from codaro.sessions import SessionStore
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.details import DetailsDialog
from codaro.ui.model_list import ModelList
from codaro.ui.plan import PlanProposal
from codaro.ui.providers import ModelPicker, ProviderSetup

QUESTION = {"question": "Qual contrato preservar?", "choices": ["Atual", "Novo"], "recommended": 0}


def seed_plan(app):
    app.agent.tasks.start("Corrigir autenticação")
    app.agent.tasks.plan(
        [
            {"title": "Investigar", "state": "done"},
            {"title": "Implementar", "state": "doing"},
            {"title": "Validar", "state": "todo"},
        ],
        ["Contrato preservado"],
    )
    app.show_plan()


def test_decisions_survive_interruption_and_resume_without_entering_tool_protocol(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel(), mode="ask")
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test() as pilot:
            app.active_question = "Analise a API"
            app.show_user_question(QUESTION, lambda _answer: None)
            app.answer_user_question("Preservar /api/v2")
            saved = SessionStore(tmp_path)
            assert saved.load() == []
            assert saved.pending_decisions[0]["answer"] == "Preservar /api/v2"
            app.finish("Concluído")
            await pilot.pause()
            await app.restore_session()
            await pilot.pause()
            assert any(
                "Preservar /api/v2" in str(widget.render()) for widget in app.query(".question")
            )
            assert agent.turns == [
                [
                    {"role": "user", "content": "Analise a API"},
                    {"role": "assistant", "content": "Concluído"},
                ]
            ]
            await app.action_clear_chat()
            await app.restore_clear()
            assert app.session_decisions[0][0]["answer"] == "Preservar /api/v2"

    run_ui(scenario())


def test_decisions_metadata_is_redacted_and_invalid_data_is_rejected(tmp_path):
    store = SessionStore(tmp_path, "secret-value")
    turns = [[{"role": "user", "content": "Oi"}, {"role": "assistant", "content": "Olá"}]]
    store.save(turns, "test", decisions=[[{"question": "Chave?", "answer": "secret-value"}]])
    assert "secret-value" not in store.path.read_text()
    store.load()
    assert store.decisions[0][0]["answer"] == "[REDACTED]"
    with pytest.raises(ValueError):
        store.save(turns, "test", pending_decisions=[{"question": "?", "answer": 3}])
    data = json.loads(store.path.read_text())
    data.pop("decisions")
    data.pop("pending_decisions")
    store.path.write_text(json.dumps(data))
    assert store.load() == turns
    assert store.decisions == [[]]


def test_resize_with_question_and_plan_keeps_chat_and_composer_visible(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            seed_plan(app)
            app.show_user_question(QUESTION, lambda _answer: None)
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            assert app.has_class("short-terminal")
            assert app.query_one("#conversation").region.height >= 3
            for selector in ("#prompt", "#question-send", "#question-pause", "#prompt-hint"):
                widget = app.query_one(selector)
                clip = app.screen._compositor.find_widget(widget).clip
                assert widget.region.area > 0
                assert widget.region.intersection(clip) == widget.region
            assert "↑↓" in str(app.query_one("#prompt-hint").render())
            await pilot.press("escape")
            assert "pendente" in str(app.query_one(".question-label").render())

    run_ui(scenario())


def test_details_and_unknown_command_preserve_draft_without_chat_noise(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            prompt = app.query_one(Prompt)
            prompt.value = "Meu próximo pedido"
            initial = len(app.query_one("#conversation").children)
            for command in ("/help", "/status"):
                await app.open_provider_menu(command)
                await pilot.pause()
                assert isinstance(app.screen, DetailsDialog)
                await pilot.press("escape")
                assert prompt.value == "Meu próximo pedido"
                assert len(app.query_one("#conversation").children) == initial
            prompt.value = "/xyz"
            prompt.move_cursor(prompt.document.end)
            await pilot.pause()
            assert prompt.suggesting
            assert "Nenhum comando" in str(app.query_one("#completion").render())
            await pilot.press("enter")
            assert not app.agent.provider.started.is_set()

    run_ui(scenario())


def test_mode_selected_during_execution_only_applies_to_next_request(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test() as pilot:
            app.busy = True
            app.update_session_header()
            await pilot.click("#select-mode")
            await pilot.press("down", "down", "enter")
            assert app.next_mode == (Mode.EXECUTE, False)
            assert app.agent.mode == Mode.ASK
            assert not app.agent.policy.auto_edits
            app.busy = False
            # Local commands and drafting must not apply the queued change.
            await app.local_command("/pwd")
            assert app.agent.mode == Mode.ASK

    run_ui(scenario())


def test_model_states_and_provider_tests_are_explicit(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "unknown"},
                    {"id": "embedding", "type": "embedding"},
                ]
            },
        )

    store = ProviderStore(tmp_path / "config", transport=httpx.MockTransport(handler))
    store.register(
        "openai-compatible", "synthetic", name="local", base_url="https://audit.invalid/v1"
    )
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            app.push_screen(ModelPicker(store))
            await pilot.pause()
            screen = app.screen
            models = screen.query_one(ModelList)
            assert models.option_count == 2
            assert models.get_option("embedding").disabled
            assert "provisório" in str(screen.query_one("#model-info").render())
            screen.query_one("#model-search", Input).value = "nada"
            await pilot.pause()
            assert screen.query_one("#model-list-state").display
            assert screen.query_one("#model-use").disabled
            await pilot.press("escape")
            app.push_screen(ProviderSetup(store))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#provider-kind", Select).value = "ollama"
            await pilot.pause()
            assert not screen.query_one("#provider-key-field").display
            assert screen.query_one("#provider-url-field").display
            assert screen.query_one("#provider-advanced", Collapsible).collapsed
            assert screen.query_one("#provider-test-inference", Button).disabled

    run_ui(scenario())


def test_plan_review_and_context_recovery_share_existing_input(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="execute"))

    async def scenario():
        async with app.run_test() as pilot:
            seed_plan(app)
            await app.local_command("/plan")
            await pilot.pause()
            assert app.query_one(PlanProposal)
            await pilot.click("#plan-adjust")
            await pilot.pause()
            assert app.query_one(Prompt).value.startswith("Ajuste o plano")
            app.active_question = "Analise o projeto"
            app.finish("O pedido não cabe.", successful=False, outcome="context")
            await pilot.pause()
            assert app.pending_user_question
            assert len(app.query(Prompt)) == 1
            assert not app.agent.provider.started.is_set()
            await pilot.press("escape")
            assert not app.agent.provider.started.is_set()

    run_ui(scenario())


def test_new_conversation_preserves_previous_session_for_explicit_resume(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            app.active_question = "Preserve meu histórico"
            app.finish("Histórico preservado")
            await pilot.pause()
            previous = app.session.path
            await app.reset_conversation("")
            await pilot.pause()
            assert app.session.path != previous
            assert "Preserve meu histórico" in previous.read_text()
            assert not app.session_turns
            assert not app.agent.policy.auto_edits

    run_ui(scenario())
