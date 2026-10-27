"""Resume, local configuration and trusted discovery through the real Textual UI."""

import copy
import threading

import pytest
from test_tui import UIModel, run_ui
from textual.widgets import Button, Checkbox, Collapsible, Input, Select, SelectionList, Static

from codaro.agent import Agent
from codaro.features import DEFAULTS, FeatureStore
from codaro.interaction import completions
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.details import ResumeDialog
from codaro.ui.features import FeaturesDialog
from codaro.ui.integrations import IntegrationRegistration


def visible(app):
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


def seeded_agent(root):
    agent = Agent(Repository(root), UIModel(), mode="execute")
    agent.conversation.store.save(
        [
            [
                {"role": "user", "content": "Explique a API"},
                {"role": "assistant", "content": "Contrato atual"},
            ]
        ],
        "test-model",
        decisions=[[{"question": "Qual contrato?", "answer": "API v2"}]],
    )
    agent.sessions.rename(agent.session_id, "Contrato de autenticação")
    return agent


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35), (160, 45)])
def test_resume_offer_identifies_conversation_and_restores_without_actions(tmp_path, size):
    agent = seeded_agent(tmp_path)
    app = CodaroApp(agent, offer_resume=True)

    async def scenario():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ResumeDialog)
            text = visible(app)
            for label in (
                "Contrato de autenticação",
                "Última atividade",
                "Explique a API",
                "Começar nova",
                "Retomar conversa",
            ):
                assert label in text
            await pilot.click("#resume-use")
            for _ in range(100):
                await pilot.pause(0.01)
                if "Pergunta respondida" in visible(app):
                    break
            assert "Codaro: Qual contrato?" in visible(app)
            assert "Você: API v2" in visible(app)
            assert "Conversa retomada" in visible(app)
            assert not app.query(".notice")
            assert not agent.provider.started.is_set()

    run_ui(scenario())


def test_new_conversation_from_offer_preserves_old_history_and_revokes_grants(tmp_path):
    agent = seeded_agent(tmp_path)
    previous = copy.deepcopy(agent.conversation.store.load())
    old_store, old_tasks = agent.conversation.store, agent.tasks
    old_task = agent.tasks.start("Tarefa anterior")
    agent.policy.auto_edits = True
    app = CodaroApp(agent, offer_resume=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#resume-back")
            for _ in range(100):
                await pilot.pause(0.01)
                if agent.session_id != "default":
                    break
            assert agent.session_id != "default"
            assert old_store.load() == previous
            assert old_store.decisions[0][0]["answer"] == "API v2"
            assert (
                next(task for task in old_tasks.load()["tasks"] if task["id"] == old_task["id"])[
                    "state"
                ]
                == "cancelled"
            )
            assert not agent.policy.auto_edits and not agent.provider.started.is_set()

    run_ui(scenario())


def test_resume_escape_preserves_active_session_and_history(tmp_path):
    agent = seeded_agent(tmp_path)
    path = agent.conversation.store.path
    saved = path.read_bytes()
    app = CodaroApp(agent, offer_resume=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            assert not isinstance(app.screen, ResumeDialog)
            assert agent.session_id == "default" and path.read_bytes() == saved
            assert not agent.provider.started.is_set()

    run_ui(scenario())


def test_empty_history_does_not_interrupt_startup(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel())
    agent.conversation.store.save([], "test-model")
    app = CodaroApp(agent, offer_resume=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not isinstance(app.screen, ResumeDialog)
            assert not agent.provider.started.is_set()

    run_ui(scenario())


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (160, 45)])
def test_completion_frame_scrolls_filters_and_preserves_draft(tmp_path, size):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            await pilot.press("/")
            menu = app.query_one("#completion")
            assert menu.display and "Comandos" in str(menu.border_title)
            assert menu.region.width <= 76
            await pilot.press(*(["down"] * 8))
            assert "de " in str(menu.border_title)
            selected = app.completion_options()[app.completion_index].value.strip()
            assert selected in visible(app)
            await pilot.press("escape")
            assert not menu.display and app.query_one(Prompt).value == "/"
            app.query_one(Prompt).value = "/provider-m"
            app.query_one(Prompt).move_cursor(app.query_one(Prompt).document.end)
            await pilot.pause()
            assert app.completion_options()[0].value == "/provider-manage "
            app.theme = "textual-light"
            await pilot.pause()
            assert menu.display and not app.agent.provider.started.is_set()

    run_ui(scenario())
    assert completions("/UNDO-T", 7, [])[0].value == "/undo-turn "


def test_features_cancel_and_save_preserve_concurrent_integrations(tmp_path):
    store = FeatureStore(tmp_path)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            await app.open_provider_menu("/features")
            await pilot.pause()
            assert isinstance(app.screen, FeaturesDialog)
            app.screen.query_one("#feature-exploration", Checkbox).value = True
            await pilot.press("escape")
            assert store.load()["exploration"] is False and not store.path.exists()
            await app.open_provider_menu("/features")
            await pilot.pause()
            app.screen.query_one("#feature-exploration", Checkbox).value = True
            store.register_integration(
                "mcp",
                "other",
                {
                    "enabled": False,
                    "trusted": True,
                    "transport": "http",
                    "url": "https://example.test/mcp",
                    "read_only_tools": [],
                    "token_env": "",
                    "tls_insecure": False,
                },
            )
            await pilot.click("#features-save")
            await pilot.pause()
            assert store.load()["exploration"] is True and "other" in store.load()["mcp"]
            assert app.agent.features["exploration"]
            assert "Funcionalidades atualizadas" in visible(app)
            assert not app.query(".notice") and not app.agent.provider.started.is_set()

    run_ui(scenario())


def test_features_invalid_limit_preserves_config_and_redacts_advanced_json(tmp_path):
    store = FeatureStore(tmp_path)
    data = copy.deepcopy(DEFAULTS)
    data["lsp"]["servers"] = {"python": ["server", "sk-proj-" + "x" * 64]}
    store.save(data)
    before = store.path.read_bytes()
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            app.push_screen(FeaturesDialog(tmp_path))
            await pilot.pause()
            assert "sk-proj-" not in str(app.screen.query_one("#features-json", Static).render())
            app.screen.query_one("#feature-max_run_requests", Input).value = "1"
            await pilot.click("#features-save")
            await pilot.pause()
            assert isinstance(app.screen, FeaturesDialog)
            assert store.path.read_bytes() == before
            assert "Orçamento" in str(app.screen.query_one("#features-error", Static).render())

    run_ui(scenario())


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (160, 45)])
@pytest.mark.parametrize(
    "kind,field", [("stdio", "command"), ("http", "url"), ("plugin", "plugin")]
)
def test_integration_form_labels_and_fixed_actions_fit(tmp_path, size, kind, field):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            app.push_screen(IntegrationRegistration(tmp_path))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#integration-kind", Select).value = kind
            await pilot.pause()
            assert screen.query_one("#integration-field-" + field).display
            assert screen.query_one("#integration-advanced", Collapsible).collapsed
            assert not screen.query_one("#integration-trust", Checkbox).value
            for identifier in ("#integration-test", "#integration-save", "#integration-cancel"):
                button = screen.query_one(identifier, Button)
                clip = screen._compositor.find_widget(button).clip
                assert button.region.intersection(clip) == button.region
            assert screen.query_one("#integration-test", Button).disabled
            assert screen.query_one("#integration-save", Button).disabled

    run_ui(scenario())


@pytest.mark.parametrize("action", ["cancel", "edit"])
def test_changed_or_cancelled_discovery_never_applies_late_results(tmp_path, monkeypatch, action):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def probe(*args):
        calls.append(args)
        entered.set()
        release.wait(5)
        return [{"name": "read_code", "label": "read_code", "description": "Consulta o código"}]

    monkeypatch.setattr(IntegrationRegistration, "probe", probe)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            app.push_screen(IntegrationRegistration(tmp_path))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#integration-name", Input).value = "local"
            screen.query_one("#integration-command", Input).value = "synthetic-server"
            await pilot.pause()
            assert not calls
            screen.query_one("#integration-trust", Checkbox).value = True
            await pilot.pause()
            await pilot.click("#integration-test")
            for _ in range(100):
                await pilot.pause(0.01)
                if entered.is_set():
                    break
            assert entered.is_set()
            if action == "cancel":
                await pilot.press("escape")
            else:
                screen.query_one("#integration-command", Input).value = "changed-server"
                await pilot.pause()
            assert not screen.testing and app.screen is screen
            release.set()
            await pilot.pause(0.2)
            assert screen.tested_signature is None and not screen.tools
            assert screen.query_one("#integration-save", Button).disabled
            assert not FeatureStore(tmp_path).path.exists()

    try:
        run_ui(scenario())
    finally:
        release.set()


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (160, 45)])
def test_discovery_requires_explicit_read_selection_and_retest_after_edit(
    tmp_path, monkeypatch, size
):
    def probe(*args):
        return [
            {"name": name, "label": name, "description": "Descrição"}
            for name in ("read_code", "write_code")
        ]

    monkeypatch.setattr(IntegrationRegistration, "probe", probe)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            app.push_screen(IntegrationRegistration(tmp_path))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#integration-name", Input).value = "local"
            screen.query_one("#integration-command", Input).value = "synthetic-server"
            screen.query_one("#integration-trust", Checkbox).value = True
            await pilot.pause()
            await pilot.click("#integration-test")
            for _ in range(100):
                await pilot.pause(0.01)
                if screen.tested_signature:
                    break
            choices = screen.query_one(SelectionList)
            await pilot.pause()
            assert "read_code" in visible(app) and "write_code" in visible(app)
            assert choices.selected == []
            choices.select("read_code")
            assert screen.config()[2]["read_only_tools"] == ["read_code"]
            screen.query_one("#integration-command", Input).value = "changed-server"
            await pilot.pause()
            assert screen.tested_signature is None and choices.selected == []
            assert screen.query_one("#integration-save", Button).disabled
            assert not FeatureStore(tmp_path).path.exists()

    run_ui(scenario())


def test_plugin_changed_on_disk_requires_retest_before_save(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin.py"
    plugin.write_text("value = 1\n")
    monkeypatch.setattr(IntegrationRegistration, "probe", lambda *args: [])
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test() as pilot:
            app.push_screen(IntegrationRegistration(tmp_path))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#integration-kind", Select).value = "plugin"
            screen.query_one("#integration-name", Input).value = "local"
            screen.query_one("#integration-plugin", Input).value = str(plugin)
            screen.query_one("#integration-trust", Checkbox).value = True
            await pilot.pause()
            await pilot.click("#integration-test")
            for _ in range(100):
                await pilot.pause(0.01)
                if screen.tested_signature:
                    break
            assert screen.tested_signature is not None
            plugin.write_text("value = 2\n")
            await pilot.click("#integration-save")
            await pilot.pause()
            assert screen.tested_signature is None
            assert screen.query_one("#integration-save", Button).disabled
            assert not FeatureStore(tmp_path).path.exists()

    run_ui(scenario())
