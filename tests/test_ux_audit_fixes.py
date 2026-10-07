import asyncio
import threading
from dataclasses import replace

import httpx
import pytest
from test_tui import StreamingUIModel, UIModel, run_ui, wait_ready
from textual.command import CommandPalette
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Select, Static, TextArea

from codaro.agent import Agent, AgentEvent
from codaro.provider import ModelError
from codaro.provider_ui import ModelPicker, ProviderSetup
from codaro.providers import ProviderStore
from codaro.repository import Repository
from codaro.tui import CodaroApp, Prompt, ScopeReview
from codaro.ux_screens import NewConversation, ProviderManager


def catalog(request):
    return httpx.Response(
        200,
        json={
            "data": [
                {"id": "chat", "context_window": 8192, "supported_parameters": ["tools"]},
                {"id": "embedding", "supported_parameters": []},
            ]
        },
    )


def store_for(tmp_path):
    store = ProviderStore(tmp_path / "profiles", transport=httpx.MockTransport(catalog))
    store.register(
        "openai-compatible", "secret-old", name="local", base_url="https://audit.invalid/v1"
    )
    store.select("local", "chat")
    return store


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35), (160, 50)])
def test_provider_actions_and_mode_stay_visible_on_small_terminals(tmp_path, size):
    store = store_for(tmp_path)
    model = UIModel()
    model.settings = replace(model.settings, model="modelo-" + "x" * 140)
    app = CodaroApp(Agent(Repository(tmp_path), model, mode="execute"))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert app.query_one("#session").region.height == 2
            app.push_screen(ProviderSetup(store))
            await pilot.pause()
            for identifier in ["provider-save", "provider-test", "provider-back"]:
                widget = app.screen.query_one("#" + identifier)
                clip = app.screen._compositor.find_widget(widget).clip
                assert widget.region.intersection(clip) == widget.region
            await pilot.press("escape")
            app.push_screen(ModelPicker(store))
            await pilot.pause()
            for widget in app.screen.query(Button):
                clip = app.screen._compositor.find_widget(widget).clip
                assert widget.region.intersection(clip) == widget.region
            await pilot.press("escape")

    run_ui(scenario())


def test_clear_preserves_proposal_and_grant_restore_and_new_task_reset(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            agent.edits.observe(agent.repository.read_lines("main.py", 1, 1), b"x = 1\n")
            result = agent.edits.propose("main.py", "x = 1", "x = 2", "Correção")
            proposal = agent.edits.proposals[result["proposal_id"]]
            task = agent.tasks.start("atividade anterior", new=True)
            agent.policy.grant(task["id"], ["src"], [])
            agent.turns = [
                [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}]
            ]
            app.session_turns = list(agent.turns)
            await pilot.press("ctrl+l")
            await pilot.pause()
            assert proposal.state == "pending"
            assert agent.policy.kind == "task"
            assert not agent.turns
            assert "mantidos" in str(app.query_one("#status", Static).render())
            await app.restore_clear()
            await pilot.pause()
            assert agent.turns[0][0]["content"] == "oi"
            assert app.query_one(f"#review-{proposal.id}").is_attached
            await app.action_new_conversation()
            await pilot.pause()
            assert isinstance(app.screen, NewConversation)
            assert app.screen.focused.id == "cancel-conversation"
            app.screen.query_one("#new-objective", Input).value = "nova atividade"
            await pilot.click("#begin-conversation")
            await pilot.pause(0.2)
            assert agent.tasks.current()["objective"] == "nova atividade"
            assert agent.policy.kind == "action"
            assert proposal.state == "rejected"
            assert (tmp_path / "main.py").read_text() == "x = 1\n"
            assert app.clear_backup is None

    run_ui(scenario())


def test_tool_and_generation_arrivals_preserve_reader_position(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            for i in range(25):
                app.mount_message(Static(f"mensagem {i}\n" + "linha\n" * 4))
            await pilot.pause(0.2)
            conversation = app.query_one("#conversation", VerticalScroll)
            conversation.scroll_home(animate=False)
            await pilot.pause()
            app.activity(AgentEvent("tool_end", "Buscar código", "resultado", state="success"))
            app.append_delta("Texto novo")
            await pilot.pause(0.2)
            assert conversation.scroll_y == 0
            assert app.query_one("#new-messages").display
            await pilot.click("#new-messages")
            await pilot.pause()
            assert conversation.is_vertical_scroll_end

    run_ui(scenario())


@pytest.mark.parametrize("theme", ["textual-dark", "textual-light"])
def test_theme_surfaces_and_palette_are_consistent(tmp_path, theme):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="execute"))

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            app.theme = theme
            await pilot.pause()
            assert (
                app.screen.styles.background.hex.lower()
                == app.get_css_variables()["background"].lower()
            )
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            assert app.screen.query_one(Input).placeholder == "Buscar comandos…"
            titles = {c.title for c in app.get_system_commands(app.screen_stack[0])}
            assert {
                "Perguntar",
                "Planejar",
                "Executar",
                "Nova conversa e tarefa",
                "Contexto",
                "Gerenciar provedores",
            } <= titles
            assert not {"Quit", "Theme", "Keys"} & titles

    run_ui(scenario())


def test_cancel_provider_test_ignores_late_result_and_preserves_focus(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow(request):
        started.set()
        release.wait(5)
        return catalog(request)

    store = ProviderStore(tmp_path / "profiles", transport=httpx.MockTransport(slow))
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ProviderSetup(store))
            await pilot.pause()
            screen = app.screen
            screen.query_one("#provider-key", Input).value = "fake"
            await pilot.click("#provider-test")
            try:
                assert await asyncio.to_thread(started.wait, 2)
                await pilot.press("escape")
                assert app.screen is screen and not screen.saving
                assert screen.focused.id == "provider-test"
                screen.query_one("#provider-url", Input).value = "https://changed.invalid/v1"
            finally:
                release.set()
            await pilot.pause(0.2)
            assert "cancelado" in str(screen.query_one("#provider-error", Static).render())
            assert screen.query_one("#provider-url", Input).value == "https://changed.invalid/v1"
            assert not store.path.exists()
            await pilot.press("escape")

    run_ui(scenario())


def test_provider_edit_rename_and_remove_use_ui_without_exposing_saved_key(tmp_path):
    store = store_for(tmp_path)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ProviderSetup(store, profile="local"))
            await pilot.pause()
            screen = app.screen
            assert screen.query_one("#provider-key", Input).value == ""
            assert screen.query_one("#provider-key", Input).password
            screen.query_one("#provider-name", Input).value = "renamed"
            screen.query_one("#provider-url", Input).value = "https://new.invalid/v1"
            await pilot.click("#provider-save")
            await pilot.pause(0.2)
            assert store.load()["active"] == "renamed"
            assert store.profile("renamed")[1]["api_key"] == "secret-old"
            app.push_screen(ProviderManager(store))
            await pilot.pause()
            assert all("secret-old" not in str(w.render()) for w in app.screen.query(Static))
            button = app.screen.query_one("#profile-remove-renamed", Button)
            button.press()
            await pilot.pause(0.4)
            assert "renamed" in store.load()["profiles"]
            button.press()
            await pilot.pause()
            assert store.load()["active"] is None
            assert not store.load()["profiles"]

    run_ui(scenario())


def test_visual_scope_uses_exact_argv_and_never_implicitly_grants(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    task = agent.tasks.start("Validar", new=True)
    app = CodaroApp(agent)
    decisions = []

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ScopeReview(task), decisions.append)
            await pilot.pause()
            screen = app.screen
            screen.query_one("#scope-paths", TextArea).text = "src\ntests"
            screen.query_one(
                "#scope-commands", TextArea
            ).text = 'python -m pytest "tests/test example.py"'
            await pilot.pause()
            assert agent.policy.kind == "action"
            await pilot.click("#grant-scope")
            await pilot.pause()
            assert decisions == [
                {
                    "paths": ["src", "tests"],
                    "commands": [["python", "-m", "pytest", "tests/test example.py"]],
                }
            ]
            assert agent.policy.kind == "action"

    run_ui(scenario())


def test_incompatible_models_hidden_and_unknown_capability_explicit(tmp_path):
    store = store_for(tmp_path)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ModelPicker(store))
            await pilot.pause()
            screen = app.screen
            with pytest.raises(Exception, match="Illegal select value"):
                screen.query_one("#model-id", Select).value = "embedding"
            assert "ocultos" in str(screen.query_one("#model-error", Static).render())
            assert "confirmadas" in str(screen.query_one("#model-info", Static).render())
            await pilot.press("escape")

    run_ui(scenario())


def test_drafting_during_stream_does_not_submit_and_context_remains_visible(tmp_path):
    model = StreamingUIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model, mode="ask"))

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one(Prompt).value = "Oi"
            await pilot.press("enter")
            try:
                assert await asyncio.to_thread(model.started.wait, 2)
                assert not app.query_one(Prompt).disabled
                app.query_one(Prompt).value = "Meu próximo rascunho"
                await pilot.press("enter")
                assert app.busy
                assert app.query_one(Prompt).value == "Meu próximo rascunho"
                assert "Contexto ≈" in str(app.query_one("#context-meter", Static).render())
            finally:
                model.release.set()
            await wait_ready(app, pilot)
            await pilot.pause()
            assert app.query_one(Prompt).value == "Meu próximo rascunho"
            assert len(app.agent.turns) == 1
            assert not app.activity_group.display

    run_ui(scenario())


def test_provider_failure_has_profile_and_recovery_actions(tmp_path):
    model = UIModel(failure=True)
    app = CodaroApp(Agent(Repository(tmp_path), model, mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one(Prompt).value = "Pergunta preservada"
            await pilot.press("enter")
            await wait_ready(app, pilot)
            await pilot.pause()
            assert "test-model" in str(app.query_one(".notice", Static).render())
            assert app.query_one("#recover-provider")
            await pilot.click("#retry-question")
            await wait_ready(app, pilot)
            assert app.agent.turns[-1][0]["content"] == "Pergunta preservada"

    run_ui(scenario())


def test_profile_update_failure_preserves_saved_credentials(tmp_path):
    store = store_for(tmp_path)
    before = store.load()
    store.transport = httpx.MockTransport(lambda request: httpx.Response(401))
    with pytest.raises(ModelError, match="401"):
        store.update(
            "openai-compatible",
            "secret-new",
            previous_name="local",
            name="changed",
            base_url="https://changed.invalid/v1",
        )
    assert store.load() == before


def test_profile_update_does_not_overwrite_concurrent_change(tmp_path):
    store = store_for(tmp_path)

    def changed(request):
        store.mutate(lambda value: value["profiles"]["local"].update(tls_insecure=True))
        return catalog(request)

    store.transport = httpx.MockTransport(changed)
    with pytest.raises(ValueError, match="alterado"):
        store.update(
            "openai-compatible",
            "secret-new",
            previous_name="local",
            base_url="https://changed.invalid/v1",
        )
    profile = store.profile("local")[1]
    assert profile["api_key"] == "secret-old" and profile["tls_insecure"]


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35), (160, 50)])
def test_approval_actions_stay_visible_with_long_task_and_command(tmp_path, size):
    from codaro.tui import CommandReview

    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    app = CodaroApp(agent)
    task = agent.tasks.start("Objetivo " + "x" * 7000, new=True)

    async def scenario():
        async with app.run_test(size=size) as pilot:
            app.push_screen(ScopeReview(task))
            await pilot.pause()
            for widget in app.screen.query(Button):
                clip = app.screen._compositor.find_widget(widget).clip
                assert widget.region.intersection(clip) == widget.region
            assert app.screen.focused.id == "cancel-scope"
            await pilot.press("escape")
            app.push_screen(
                CommandReview(
                    tmp_path / ("caminho" * 25), ["python", "-c", "print('teste')\n" * 100], 60
                )
            )
            await pilot.pause()
            for widget in app.screen.query(Button):
                clip = app.screen._compositor.find_widget(widget).clip
                assert widget.region.intersection(clip) == widget.region
            assert app.screen.focused.id == "reject-command"
            await pilot.press("escape")

    run_ui(scenario())


def test_edit_active_profile_applies_explicit_tls_change(tmp_path):
    from codaro.provider import create_provider

    store = store_for(tmp_path)
    agent = Agent(Repository(tmp_path), create_provider(store.active_settings()), mode="ask")
    app = CodaroApp(agent)
    store.update(
        "openai-compatible",
        "secret-old",
        previous_name="local",
        name="local",
        base_url="https://audit.invalid/v1",
        tls_insecure=True,
    )

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            app.provider_edited("local", "local", store)
            await pilot.pause()
            assert agent.provider.settings.tls_insecure
            await pilot.press("escape")

    run_ui(scenario())
