import httpx
from test_tui import UIModel, run_ui
from textual.command import CommandPalette
from textual.widgets import Checkbox, Input, Select, Static

from codaro.agent import Agent
from codaro.provider import create_provider
from codaro.provider_ui import ModelPicker, ProviderSetup
from codaro.providers import ProviderStore
from codaro.repository import Repository
from codaro.tui import CodaroApp, Prompt


def test_chat_registration_and_model_selection_updates_live_budgets(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer ui-test-key"
        return httpx.Response(
            200,
            json={"data": [{"id": "small-test", "context_window": 8192, "max_output_tokens": 512}]},
        )

    store = ProviderStore(tmp_path / "config", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("codaro.providers.ProviderStore", lambda: store)
    (tmp_path / "project").mkdir()
    app = CodaroApp(Agent(Repository(tmp_path / "project"), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 50)) as pilot:
            app.query_one(Prompt).value = "Meu rascunho"
            await pilot.press("ctrl+p")
            assert isinstance(app.screen, CommandPalette)
            await pilot.press(*"Cadastrar provedor")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ProviderSetup)
            assert app.query_one(Prompt).value == "Meu rascunho"
            key = app.screen.query_one("#provider-key", Input)
            assert key.password
            app.screen.query_one("#provider-kind", Select).value = "openai-compatible"
            await pilot.pause()
            app.screen.query_one("#provider-url", Input).value = "https://proxy.test/v1"
            app.screen.query_one("#provider-key", Input).value = "ui-test-key"
            app.screen.query_one("#provider-tls", Checkbox).value = True
            await pilot.click("#provider-save")
            for _ in range(100):
                await pilot.pause(0.02)
                if isinstance(app.screen, ModelPicker):
                    break
            assert isinstance(app.screen, ModelPicker)
            app.screen.query_one("#model-id", Select).value = "small-test"
            await pilot.pause()
            await pilot.click("#model-use")
            for _ in range(100):
                await pilot.pause(0.02)
                if not isinstance(app.screen, ModelPicker):
                    break
            assert not isinstance(app.screen, ModelPicker)
            assert app.agent.provider.settings.model == "small-test"
            assert app.agent.provider.settings.tls_insecure
            assert app.agent.context_window == 8192 and app.agent.input_limit == 7168
            assert store.active_settings().model == "small-test"
            assert app.session.redact("ui-test-key") == "[REDACTED]"
            assert all("ui-test-key" not in str(widget.render()) for widget in app.query(Static))
            assert len(requests) == 2  # discovery plus fresh metadata on selection
            # A session-level TLS verify override survives a model change on this endpoint.
            from dataclasses import replace

            app.agent.set_provider(
                create_provider(replace(app.agent.provider.settings, tls_insecure=False))
            )
            app.activate_provider(store.select("openai-compatible", "small-test"))
            assert not app.agent.provider.settings.tls_insecure
            await pilot.press("ctrl+p")
            await pilot.press(*"Selecionar provedor e modelo")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ModelPicker)
            assert app.query_one(Prompt).value == "Meu rascunho"
            await pilot.press("escape")

    run_ui(scenario())


def test_registration_error_keeps_key_masked_and_does_not_write(tmp_path, monkeypatch):
    store = ProviderStore(
        tmp_path / "config",
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "ui-test-key"})),
    )
    monkeypatch.setattr("codaro.providers.ProviderStore", lambda: store)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 50)) as pilot:
            await app.local_command("/providers")
            await pilot.pause()
            app.screen.query_one("#provider-key", Input).value = "ui-test-key"
            await pilot.click("#provider-save")
            for _ in range(100):
                await pilot.pause(0.02)
                if not app.screen.saving:
                    break
            assert isinstance(app.screen, ProviderSetup)
            assert "401" in str(app.screen.query_one("#provider-error", Static).render())
            assert not store.path.exists()
            assert all(
                "ui-test-key" not in str(widget.render()) for widget in app.screen.query(Static)
            )
            await pilot.press("escape")
            assert not isinstance(app.screen, ProviderSetup)

    run_ui(scenario())
