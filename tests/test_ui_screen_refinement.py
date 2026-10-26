"""Regressions from screenshots with real-sized catalogs and conversational turns."""

import json

import httpx
import pytest
from test_agent import FakeModel, call
from test_tui import UIModel, run_ui
from textual.widgets import Button, Input, RadioButton, Select, Static

from codaro.agent import Agent, AgentEvent
from codaro.agent.presentation import TOOL_TITLES, tool_outcome, tool_target
from codaro.agent.questions import register_questions
from codaro.intent import is_information_request
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.messages import DecisionMessage, UserMessage
from codaro.ui.model_list import ModelList
from codaro.ui.modes import ModePicker
from codaro.ui.outcomes import TaskReceipt
from codaro.ui.providers import ModelPicker, ProviderSetup


def visible(app):
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (160, 45)])
def test_large_catalog_has_single_line_rows_and_current_metadata(tmp_path, size):
    models = [
        {"id": f"model-{i:02}", **({"context_window": 131072} if i == 21 else {})}
        for i in range(30)
    ]
    store = ProviderStore(
        tmp_path / "config",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": models})),
    )
    store.register("openai-compatible", "", name="Local", base_url="https://example.test/v1")
    store.select("Local", "model-21")
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            app.push_screen(ModelPicker(store))
            await pilot.pause()
            options = app.screen.query_one(ModelList)
            assert options.virtual_size.height == 30
            assert options.value == "model-21" and "model-21" in visible(app)
            options.value = "model-22"
            app.screen.post_message(ModelList.Changed("model-21"))
            await pilot.pause()
            metadata = str(app.screen.query_one("#model-metadata", Static).render())
            assert "Modelo: model-22" in metadata
            assert "provisório" in str(app.screen.query_one("#model-info", Static).render())
            await pilot.resize_terminal(70, 25)
            await pilot.pause()
            assert options.value == "model-22" and options.virtual_size.height == 30

    run_ui(scenario())


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (160, 45)])
def test_mode_policy_is_separate_visible_only_for_execute_and_compact(tmp_path, size):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            app.open_mode_picker()
            await pilot.pause()
            assert isinstance(app.screen, ModePicker)
            before = app.screen.query_one("#mode-picker").region.height
            assert not app.screen.query_one("#edit-policy").display
            await pilot.press("down", "down")
            await pilot.pause()
            assert app.screen.query_one("#edit-policy").display
            assert app.screen.query_one("#mode-picker").region.height > before
            for selector in ("#edit-ask", "#edit-auto", "#mode-help", "#mode-apply"):
                widget = app.screen.query_one(selector)
                clip = app.screen._compositor.find_widget(widget).clip
                assert widget.region.intersection(clip) == widget.region
            await pilot.click("#edit-auto")
            assert app.screen.query_one("#edit-auto", RadioButton).value
            await pilot.press("escape")
            assert app.agent.mode.value == "ask" and not app.agent.policy.auto_edits

    run_ui(scenario())


def test_provider_labels_focus_and_validation_do_not_send_incomplete_form(tmp_path):
    requests = []
    store = ProviderStore(
        tmp_path / "profiles",
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={"data": []})
        ),
    )
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ProviderSetup(store))
            await pilot.pause()
            assert "OpenAI" in visible(app)
            assert "Cole sua API key" in visible(app)
            assert app.screen.query_one("#provider-test", Button).disabled
            assert app.screen.query_one("#provider-save", Button).disabled
            app.screen.query_one("#provider-key", Input).value = "synthetic-key"
            await pilot.pause()
            assert not app.screen.query_one("#provider-test", Button).disabled
            app.screen.query_one("#provider-kind", Select).value = "ollama"
            await pilot.pause()
            app.screen.query_one("#provider-url", Input).value = "invalid-url"
            await pilot.pause()
            assert app.screen.query_one("#provider-test", Button).disabled
            assert not requests

    run_ui(scenario())


def test_echoed_question_is_retried_without_inventing_a_user_answer(tmp_path):
    bad = {"question": "Me faça outra pergunta", "choices": ["Como posso ajudar?"]}
    good = {"question": "Qual parte do projeto quer discutir?", "choices": ["Busca", "CLI"]}
    model = FakeModel(
        [
            call("ask_user_question", bad, "bad"),
            call("ask_user_question", good, "good"),
            {"content": "Vamos analisar a busca."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, mode="ask")
    questions = []
    register_questions(
        agent,
        lambda question, _: questions.append(question) or {"user_response": "Busca"},
    )
    assert agent.ask("Me faça outra pergunta") == "Vamos analisar a busca."
    assert questions == [good]
    errors = [json.loads(m["content"]) for m in model.requests[1][0] if m["role"] == "tool"]
    assert any("repetindo" in e.get("error", "") for e in errors)
    assert not agent.policy.auto_edits


@pytest.mark.parametrize(
    "text", ["Me faça outra pergunta", "Faça uma pergunta.", "Ask me a question"]
)
def test_conversational_question_requests_are_consultations(text):
    assert is_information_request(text)
    assert not is_information_request(text + "; edite main.py")


def test_chat_limits_prose_keeps_code_wide_and_records_one_decision(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(180, 45)) as pilot:
            app.mount_message(UserMessage("Explique " * 30))
            app.active_question = "Olá"
            app.finish("Texto explicativo " * 40 + "\n\n```python\n" + "x" * 130 + "\n```")
            app.mount_decision({"question": "Qual contrato preservar?", "answer": "API atual"})
            await pilot.pause()
            assert app.query_one(".assistant MarkdownParagraph").region.width <= 100
            assert app.query_one(".assistant MarkdownFence").region.width > 100
            assert app.query_one(UserMessage).region.width <= 100
            assert len(app.query(DecisionMessage)) == 1
            decision = str(app.query_one(DecisionMessage).render())
            assert "Qual contrato preservar?" in decision and "Você: API atual" in decision

    run_ui(scenario())


def test_conversation_has_no_implementation_receipt_and_idle_rate_is_identified(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    agent.tasks.start("Me faça uma pergunta")
    agent.tasks.state("completed")
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test() as pilot:
            app.active_question = "Me faça uma pergunta"
            app.generation_rate.report(100, generation_seconds=2)
            app.finish("Qual parte do projeto você quer conhecer?")
            await pilot.pause()
            assert not app.query(".task-receipt")
            assert "Última: 50 t/s" in str(app.query_one("#generation-rate", Static).render())
            app.busy = True
            app.update_generation_status()
            assert "Última" not in str(app.query_one("#generation-rate", Static).render())
            assert TOOL_TITLES["ask_user_question"] == "Perguntar ao usuário"
            assert "arquivo" not in tool_target("ask_user_question", {})
            assert tool_outcome({"user_response": "API atual"}) == ("success", "Decisão registrada")
            app.activity(AgentEvent("tool_start", "Perguntar ao usuário", "Decisão pendente"))
            assert "Aguardando sua resposta" in str(app.query_one("#status", Static).render())

    run_ui(scenario())


@pytest.mark.parametrize("exit_code", [None, 0, 1])
def test_receipt_only_marks_actual_current_checks_as_successful(tmp_path, exit_code):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    agent.tasks.start("Atualize o código")
    agent.tasks.changed("main.py", "checkpoint")
    if exit_code is not None:
        agent.tasks.validation({"argv": ["pytest"], "exit_code": exit_code, "timed_out": False})
    agent.tasks.state("completed")
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test() as pilot:
            app.active_question = "Atualize o código"
            app.finish("Concluído")
            await pilot.pause()
            receipt = app.query_one(TaskReceipt)
            assert receipt.has_class("verified") == (exit_code == 0)
            assert receipt.has_class("failed") == (exit_code == 1)

    run_ui(scenario())
