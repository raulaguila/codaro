"""Verify approved hierarchy and interactions in the native terminal layout."""

import json

import httpx
import pytest
from test_agent import FakeModel, call
from test_tui import UIModel, run_ui, wait_ready
from textual.widgets import Collapsible, Input, Static

from codaro.agent import Agent, AgentEvent
from codaro.llm import Settings
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.messages import UserMessage
from codaro.ui.model_list import ModelList
from codaro.ui.modes import ModePicker
from codaro.ui.providers import ModelPicker
from codaro.ui.questions import QuestionDock
from codaro.ui.stages import TurnStages

QUESTION = {
    "question": "Podemos alterar o contrato da autenticação?",
    "explanation": "Essa escolha define se os clientes precisarão de ajustes.",
    "choices": ["Preservar o contrato atual", "Pode alterar o contrato"],
    "recommended": 0,
}


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35), (160, 50)])
def test_header_entry_and_question_follow_approved_composition(tmp_path, size):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            assert app.query_one("#brand").region.y == app.query_one("#select-model").region.y
            assert app.query_one("#brand").region.y == app.query_one("#select-mode").region.y
            prompt = app.query_one(Prompt)
            assert prompt.parent is app.query_one("#entry")
            prompt.value = "Rascunho preservado"
            results = []
            app.show_user_question(QUESTION, results.append)
            await pilot.pause()
            dock = app.query_one(QuestionDock)
            assert dock.parent is app.query_one("#response-card")
            assert prompt.parent.parent is dock.parent
            assert len(app.query(Prompt)) == 1
            for selector in ("#question-send", "#question-pause", "#prompt"):
                widget = app.query_one(selector)
                clip = app.screen._compositor.find_widget(widget).clip
                assert widget.region.intersection(clip) == widget.region
            await pilot.press("escape")
            assert app.pending_user_question and not results
            await pilot.press("down", "enter")
            assert results == ["Pode alterar o contrato"]
            assert prompt.value == "Rascunho preservado"
            assert not app.agent.policy.auto_edits

    run_ui(scenario())


def test_model_question_roundtrip_uses_user_response_and_protocol(tmp_path):
    model = FakeModel(
        [
            call("ask_user_question", QUESTION),
            {"content": "Vou respeitar sua decisão."},
        ]
    )
    model.settings = Settings("http://localhost/v1", "test")
    agent = Agent(Repository(tmp_path), model, mode="ask")
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            await app.submit_question("Preciso escolher a abordagem.")
            for _ in range(100):
                await pilot.pause(0.01)
                if app.pending_user_question:
                    break
            assert app.pending_user_question
            assert len(model.requests) == 1
            await pilot.press("escape")
            await pilot.pause()
            assert len(model.requests) == 1
            prompt = app.query_one(Prompt)
            prompt.value = "/api/v1 deve continuar funcionando"
            await pilot.press("enter")
            await wait_ready(app, pilot)
            messages, tools = model.requests[-1]
            result = json.loads(next(m["content"] for m in messages if m["role"] == "tool"))
            assert result["user_response"] == "/api/v1 deve continuar funcionando"
            assert any(t["function"]["name"] == "ask_user_question" for t in tools)
            assert not agent.policy.auto_edits and not agent.policy.paths

    run_ui(scenario())


def test_one_stage_group_contains_tools_and_reasoning_without_hiding_answer(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            app.activity(AgentEvent("tool_end", "Ler linhas", "src/auth.py:1–80", "success", 12))
            app.append_reasoning("Conferindo a validação recebida do provedor.")
            app.append_delta("A validação começa em src/auth.py.")
            await pilot.pause()
            assert len(app.query(TurnStages)) == 1
            assert app.reasoning_group.parent is app.activity_group.query_one(Collapsible.Contents)
            assert app.generation_preview.parent is app.activity_group.query_one(
                Collapsible.Contents
            )
            assert app.generation_preview.query_one(".live-speaker").display
            assert not app.activity_group.collapsed and not app.reasoning_group.collapsed
            app.activity_group.collapsed = False
            await pilot.pause()
            assert not app.query_one(".live-steps", Static).display
            app.activity_group.collapsed = True
            app.activity(AgentEvent("tool_end", "Buscar código", "test_auth", "success", 2))
            assert app.activity_group.collapsed

    run_ui(scenario())


def test_cancel_required_question_never_invents_response(tmp_path):
    model = FakeModel([call("ask_user_question", QUESTION)])
    model.settings = Settings("http://localhost/v1", "test")
    app = CodaroApp(Agent(Repository(tmp_path), model, mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await app.submit_question("Preciso escolher a abordagem.")
            for _ in range(100):
                await pilot.pause(0.01)
                if app.pending_user_question:
                    break
            assert app.pending_user_question
            await pilot.press("ctrl+x")
            await wait_ready(app, pilot)
            assert not app.agent.turns
            assert app.pending_user_question is None
            assert not app.query_one(QuestionDock).display
            assert len(model.requests) == 1

    run_ui(scenario())


def test_model_search_filters_without_network_and_enter_confirms(tmp_path):
    requests = []

    def catalog(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "chat", "context_window": 8192},
                    {"id": "other", "context_window": 32768},
                ]
            },
        )

    store = ProviderStore(tmp_path / "profiles", transport=httpx.MockTransport(catalog))
    store.register(
        "openai-compatible", "synthetic", name="local", base_url="https://audit.invalid/v1"
    )
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ModelPicker(store))
            await pilot.pause()
            app.screen.query_one("#model-search", Input).value = "other"
            await pilot.pause()
            models = app.screen.query_one(ModelList)
            assert models.option_count == 1 and models.value == "other"
            assert len(requests) == 1
            app.screen.query_one("#model-search", Input).value = "does-not-exist"
            await pilot.pause()
            assert not models.option_count and app.screen.query_one("#model-use").disabled
            app.screen.query_one("#model-search", Input).value = "other"
            await pilot.pause()
            models.focus()
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause(0.01)
                if not isinstance(app.screen, ModelPicker):
                    break
            assert store.active_settings().model == "other"

    run_ui(scenario())


def test_mode_overlay_keeps_transcript_visible_and_closes_without_applying(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            app.mount_message(UserMessage("CONVERSA VISÍVEL AO FUNDO"))
            app.query_one(Prompt).value = "Rascunho preservado"
            await pilot.pause()
            await pilot.click("#select-mode")
            await pilot.pause()
            text = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
            assert "CONVERSA VISÍVEL AO FUNDO" in text
            assert isinstance(app.screen, ModePicker)
            await pilot.press("down", "down")
            await pilot.click(offset=(3, 20))
            await pilot.pause()
            assert not isinstance(app.screen, ModePicker)
            assert app.agent.mode.value == "ask"
            assert app.query_one(Prompt).value == "Rascunho preservado"

    run_ui(scenario())
