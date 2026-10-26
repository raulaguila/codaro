import asyncio
import threading
from dataclasses import replace

import httpx
import pytest
from test_tui import UIModel, run_ui, wait_ready
from textual.containers import VerticalScroll
from textual.widgets import Button, Collapsible, Input, Static

from codaro.agent import Agent, AgentEvent
from codaro.llm import ContextCapacityError, ModelError
from codaro.llm.errors import OutputLimitError
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.model_list import ModelList
from codaro.ui.providers import ModelPicker, ProviderSetup
from codaro.ui.recovery import RecoveryCard


def rendered(app):
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


def test_all_reasoning_steps_disappear_when_group_is_collapsed(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            app.hide_welcome()
            for number in range(3):
                app.append_reasoning(f"SEGREDO_ETAPA_{number}")
                await pilot.pause()
                if number != 2:
                    app.finish_preview("tools")
            group = app.reasoning_group
            assert group.collapsed
            assert "SEGREDO_ETAPA" not in rendered(app)
            assert "etapa encerrada" not in rendered(app)
            app.activity_group.collapsed = False
            group.collapsed = False
            await pilot.pause()
            assert "SEGREDO_ETAPA_2" in rendered(app)
            assert all(
                p.parent is group.query_one(Collapsible.Contents)
                for p in group.query(".reasoning-preview")
            )
            group.collapsed = True
            app.finish_preview("tools")
            app.append_reasoning("ETAPA_POSTERIOR")
            await pilot.pause()
            assert "ETAPA_POSTERIOR" not in rendered(app)
            assert "etapa encerrada" not in rendered(app)

    run_ui(scenario())


def test_retry_preserves_cursor_and_new_edits_and_resolves_old_error(tmp_path):
    model = UIModel(failure=True)
    app = CodaroApp(Agent(Repository(tmp_path), model, mode="ask"))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            prompt = app.query_one(Prompt)
            prompt.value = "Pergunta original"
            await pilot.press("enter")
            await wait_ready(app, pilot)
            card = app.pending_recovery
            prompt.value = "Meu próximo rascunho"
            prompt.move_cursor((0, 5))
            selection = prompt.selection
            model.started.clear()
            model.blocked = True
            try:
                await pilot.click("#retry-question")
                assert await asyncio.to_thread(model.started.wait, 2)
                assert prompt.value == "Meu próximo rascunho"
                assert prompt.selection == selection
                prompt.insert(" alterado durante a geração")
                newest = prompt.value
            finally:
                model.release.set()
            await wait_ready(app, pilot)
            await pilot.pause()
            assert app.agent.turns[-1][0]["content"] == "Pergunta original"
            assert prompt.value == newest
            assert card.collapsed and card.has_class("resolved")
            assert "resolvida" in card.title
            assert not card.query(".recovery-actions Button")
            assert "API indisponível" in str(card.query_one(".notice", Static).render())
            assert app.focused is prompt
            # The progress timer must not overwrite the final ready state.
            await pilot.pause(1.1)
            assert str(app.query_one("#status", Static).render()) == "Pronto"

    run_ui(scenario())


def catalog(request):
    return httpx.Response(
        200,
        json={
            "data": [
                {"id": name, "context_window": 8192, "supported_parameters": ["tools"]}
                for name in ["chat", "other"]
            ]
        },
    )


@pytest.mark.parametrize("operation", ["register", "update", "refresh", "select"])
def test_cancel_remote_lookup_cannot_write_a_late_result(tmp_path, monkeypatch, operation):
    store = ProviderStore(tmp_path / "profiles", transport=httpx.MockTransport(catalog))
    store.register("openai-compatible", "fake", name="local", base_url="https://audit.invalid/v1")
    store.select("local", "chat")
    original = store.path.read_bytes()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow(request):
        started.set()
        release.wait(5)
        return catalog(request)

    store.transport = httpx.MockTransport(slow)
    method = getattr(store, "prepare_" + operation)

    def observe_completion(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(store, "prepare_" + operation, observe_completion)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            setup = operation in {"register", "update"}
            screen = (
                ProviderSetup(store, profile="local" if operation == "update" else None)
                if setup
                else ModelPicker(store)
            )
            app.push_screen(screen)
            await pilot.pause()
            if setup:
                screen.query_one("#provider-name", Input).value = "new-profile"
                screen.query_one("#provider-key", Input).value = "new-fake-key"
                button, back = "provider-save", "provider-back"
            else:
                screen.query_one("#model-id", ModelList).value = "other"
                button, back = (
                    ("model-refresh" if operation == "refresh" else "model-use"),
                    "model-back",
                )
            try:
                await pilot.click("#" + button)
                assert await asyncio.to_thread(started.wait, 2)
                await pilot.pause()
                assert not screen.query_one("#" + back, Button).disabled
                assert "Consultando" in rendered(app)
                await pilot.press("escape")
                assert app.screen is screen
                assert not (screen.saving if setup else screen.fetching)
                assert "cancelada" in rendered(app)
                assert store.path.read_bytes() == original
            finally:
                release.set()
            assert await asyncio.to_thread(finished.wait, 2)
            await pilot.pause()
            assert store.path.read_bytes() == original
            assert "cancelada" in rendered(app)
            await pilot.press("escape")
            assert app.focused is app.query_one(Prompt)

    run_ui(scenario())


def test_prepared_change_preserves_concurrent_profile_edit(tmp_path):
    store = ProviderStore(tmp_path, transport=httpx.MockTransport(catalog))
    store.register("openai-compatible", "fake", name="local", base_url="https://audit.invalid/v1")
    pending = store.prepare_update(
        "openai-compatible",
        "replacement",
        previous_name="local",
        base_url="https://audit.invalid/v1",
    )
    assert store.profile("local")[1]["api_key"] == "fake"
    store.mutate(lambda value: value["profiles"]["local"].update(tls_insecure=True))
    with pytest.raises(ValueError, match="alterado"):
        pending.commit()
    assert store.profile("local")[1]["api_key"] == "fake"


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 35), (160, 50)])
def test_compact_layout_keeps_setup_chat_and_completion_hint_visible(tmp_path, size):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            mode = app.query_one("#select-mode")
            clip = app.screen._compositor.find_widget(mode).clip
            assert mode.region.intersection(clip) == mode.region
            assert not app.query("#suggestions, #configure-start")
            prompt = app.query_one(Prompt)
            await pilot.press("/")
            assert app.query_one("#conversation").region.height >= 4
            assert "Tab completa" in rendered(app) and "Esc fecha" in rendered(app)
            for _ in range(7):
                await pilot.press("down")
            selected = app.completion_options()[app.completion_index].value.strip()
            assert "› " + selected in rendered(app)
            before = prompt.selection
            await pilot.press("escape")
            assert not app.query_one("#completion").display
            assert prompt.value == "/" and prompt.selection == before
            prompt.value = "linha\n" * 7
            prompt.move_cursor(prompt.document.end)
            await pilot.pause()
            assert app.query_one("#conversation").region.height >= 4
            await pilot.resize_terminal(60, 20)
            await pilot.pause()
            assert app.query_one("#conversation").region.height >= 4

    run_ui(scenario())


@pytest.mark.parametrize(
    "error,actions,neutral",
    [
        (ModelError("API retornou HTTP 401."), {"recover-provider"}, False),
        (
            ModelError("API retornou HTTP 503."),
            {"retry-question", "recover-provider", "recover-model"},
            False,
        ),
        (OutputLimitError("Limite de saída atingido."), {"retry-question", "recover-model"}, False),
        (
            ContextCapacityError("Podemos reduzir a pergunta."),
            {"retry-question", "recover-model"},
            False,
        ),
    ],
)
def test_recovery_actions_match_the_failure(tmp_path, error, actions, neutral):
    class FailingModel(UIModel):
        def complete(self, messages, tools=None):
            raise error

    app = CodaroApp(Agent(Repository(tmp_path), FailingModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            await pilot.pause()
            card = app.query_one(RecoveryCard)
            assert {b.id for b in card.query(Button)} == actions
            assert card.has_class("neutral") == neutral

    run_ui(scenario())


def test_cancel_is_neutral_and_context_details_stay_available(tmp_path):
    model = UIModel(blocked=True)
    app = CodaroApp(Agent(Repository(tmp_path), model, mode="ask"))

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("o", "i", "enter")
            try:
                assert await asyncio.to_thread(model.started.wait, 2)
                app.activity(
                    AgentEvent("model_start", "Consulta", context_tokens=4000, context_limit=8000)
                )
                meter = app.query_one("#context-meter", Static)
                assert "50%" in str(meter.render())
                assert "4,000/8,000" in meter.tooltip
                await pilot.press("ctrl+x")
            finally:
                model.release.set()
            await wait_ready(app, pilot)
            await pilot.pause()
            card = app.query_one(RecoveryCard)
            assert card.has_class("neutral")
            assert {b.id for b in card.query(Button)} == {"retry-question", "recover-changes"}
            assert not app.agent.turns

    run_ui(scenario())


def test_incoming_message_keeps_completion_and_unread_button_visible(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(60, 20)) as pilot:
            app.hide_welcome()
            for number in range(20):
                app.mount_message(Static(f"Mensagem {number}\n" + "linha\n" * 3))
            await pilot.pause()
            conversation = app.query_one("#conversation", VerticalScroll)
            conversation.scroll_home(animate=False)
            await pilot.pause()
            await pilot.press("/")
            app.mount_message(Static("Nova mensagem"))
            await pilot.pause()
            assert app.query_one("#new-messages").display
            assert conversation.region.height >= 4
            assert conversation.scroll_y == 0
            assert "Esc fecha" in rendered(app)

    run_ui(scenario())


def test_authentication_recovery_edits_saved_providers_and_preserves_draft(tmp_path, monkeypatch):
    from codaro.ui.dialogs import ProviderManager

    store = ProviderStore(tmp_path / "profiles", transport=httpx.MockTransport(catalog))
    store.register("openai-compatible", "fake", name="local", base_url="https://audit.invalid/v1")
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)

    class AuthModel(UIModel):
        settings = replace(UIModel.settings, provider_id="local")

        def complete(self, messages, tools=None):
            raise ModelError("API retornou HTTP 401.")

    app = CodaroApp(Agent(Repository(tmp_path), AuthModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            await pilot.pause()
            app.query_one(Prompt).value = "Meu rascunho"
            await pilot.click("#recover-provider")
            await pilot.pause()
            assert isinstance(app.screen, ProviderManager)
            await pilot.press("escape")
            assert app.query_one(Prompt).value == "Meu rascunho"

    run_ui(scenario())
