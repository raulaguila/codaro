import httpx
import pytest
from test_tui import UIModel, run_ui
from textual.widgets import Button, Checkbox, Input, Select
from typer.testing import CliRunner

from codaro.agent import Agent
from codaro.cli import app
from codaro.configuration import resolve_configuration, startup_overrides
from codaro.llm import Settings
from codaro.llm.profiles import ProviderStore
from codaro.preferences import Preferences, PreferencesStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.preferences import PreferencesDialog
from codaro.ui.providers import ModelPicker, ProviderSetup


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in [
        "CODARO_BASE_URL",
        "CODARO_MODEL",
        "CODARO_API_KEY",
        "CODARO_PROVIDER",
        "CODARO_TLS_INSECURE",
        "CODARO_TIMEOUT",
        "CODARO_CONTEXT_WINDOW",
        "CODARO_MAX_OUTPUT_TOKENS",
        "CODARO_TOKEN_ENCODING",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def catalog(request):
    assert request.url.path.removeprefix("/v1") in {"/models", "/models/small"}
    return httpx.Response(
        200,
        json={
            "data": [{"id": "small", "context_window": 32768, "supported_parameters": ["tools"]}]
        },
    )


def registered(directory):
    store = ProviderStore(directory, transport=httpx.MockTransport(catalog))
    store.register(
        "openai-compatible", "private-key", name="local", base_url="https://audit.invalid"
    )
    store.select("local", "small")
    return store


@pytest.mark.parametrize(
    "args,mode,resume",
    [
        ([], "execute", False),
        (["."], "execute", False),
        (["--mode", "plan"], "plan", False),
        (["--mode", "ask", "."], "ask", False),
        ([".", "--mode", "plan"], "plan", False),
        (["--resume", "."], "execute", True),
        (["chat", "--resume"], "execute", True),
        (["chat", "--read-only"], "ask", False),
    ],
)
def test_simple_and_legacy_startup_share_the_same_configuration(
    isolated, monkeypatch, args, mode, resume
):
    captured = []
    monkeypatch.setattr(CodaroApp, "run", lambda self: captured.append(self))
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert captured[0].agent.repository.root == isolated
    assert captured[0].agent.mode == mode
    assert captured[0].resume_requested == resume
    assert captured[0].setup_required
    assert startup_overrides.get() is None


def test_root_flags_and_command_overrides_do_not_leak_to_later_invocations(isolated, monkeypatch):
    captured = []
    monkeypatch.setattr(CodaroApp, "run", lambda self: captured.append(self))
    runner = CliRunner()
    PreferencesStore().save(
        Preferences(
            mode="plan", resume=True, max_steps=17, max_seconds=750, max_corrections=4, timeout=22
        )
    )
    result = runner.invoke(app, ["--mode", "ask", ".", "--mode", "execute", "--no-resume"])
    assert result.exit_code == 0, result.output
    assert captured[-1].agent.mode == "execute"
    assert not captured[-1].resume_requested
    assert captured[-1].agent.max_steps == 17
    assert captured[-1].agent.max_seconds == 750
    assert captured[-1].agent.provider.settings.timeout == 22
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert captured[-1].agent.mode == "plan" and captured[-1].resume_requested
    assert startup_overrides.get() is None


def test_saved_profile_wins_over_stale_environment_and_env_is_explicit(isolated, monkeypatch):
    store = registered(isolated / "profiles")
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)
    monkeypatch.setenv("CODARO_MODEL", "old-model")
    monkeypatch.setenv("CODARO_API_KEY", "old-secret")
    monkeypatch.setenv("CODARO_BASE_URL", "https://old.invalid/v1")
    config = resolve_configuration()
    assert config.model.model == "small" and config.model.api_key == "private-key"
    assert config.model.context_window == 32768
    assert config.sources["provider"] == "perfil ativo: local"
    assert set(config.ignored_environment) == {"CODARO_MODEL", "CODARO_API_KEY", "CODARO_BASE_URL"}
    assert not config.needs_setup
    environment = resolve_configuration(provider="env")
    assert environment.model.model == "old-model" and environment.model.api_key == "old-secret"
    assert not environment.ignored_environment
    result = CliRunner().invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "private-key" not in result.output and "old-secret" not in result.output
    assert "CODARO_MODEL" in result.output


def test_legacy_env_without_profiles_still_works_and_does_not_require_setup(isolated, monkeypatch):
    monkeypatch.setenv("CODARO_MODEL", "legacy-model")
    config = resolve_configuration()
    assert config.model.model == "legacy-model" and not config.needs_setup
    monkeypatch.delenv("CODARO_MODEL")
    monkeypatch.setenv("CODARO_API_KEY", "stale-key")
    assert resolve_configuration().needs_setup


def test_profile_startup_does_not_call_the_network(isolated, monkeypatch):
    store = registered(isolated / "profiles")
    store.mutate(lambda data: data["profiles"]["local"].update(kind="ollama"))

    def offline(request):
        raise AssertionError("Startup must use the saved catalog")

    store.transport = httpx.MockTransport(offline)
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)
    assert resolve_configuration().model.model == "small"
    assert Settings.from_env().model == "small"


def test_help_is_short_and_advanced_commands_remain_usable(isolated):
    runner = CliRunner()
    short = runner.invoke(app, ["--help"])
    full = runner.invoke(app, ["--help-all"])
    assert short.exit_code == full.exit_code == 0
    assert "Uso diário" in short.output and "Configuração" in short.output
    assert "evaluate-workflows" not in short.output
    assert "evaluate-workflows" in full.output and "search" in full.output
    result = runner.invoke(app, ["pwd"])
    assert result.exit_code == 0 and str(isolated) in result.output
    assert not PreferencesStore().path.exists()


def test_config_cli_validates_and_resets_preferences_without_removing_credentials(isolated):
    store = registered(ProviderStore().directory)
    credentials = store.path.read_bytes()
    runner = CliRunner()
    assert runner.invoke(app, ["config", "set", "mode", "ask"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "max_steps", "auto"]).exit_code == 0
    previous = PreferencesStore().path.read_bytes()
    bad = runner.invoke(app, ["config", "set", "max_seconds", "99999"])
    assert bad.exit_code == 1
    assert PreferencesStore().path.read_bytes() == previous
    assert runner.invoke(app, ["config", "reset"], input="n\n").exit_code == 0
    assert PreferencesStore().load().mode == "ask"
    assert runner.invoke(app, ["config", "reset", "--yes"]).exit_code == 0
    assert PreferencesStore().load() == Preferences()
    assert store.path.read_bytes() == credentials


@pytest.mark.parametrize("raw", ['{"version":1,"preferences":{"max_steps":true}}', "{broken"])
def test_broken_preferences_can_be_reset_and_do_not_block_local_tools(isolated, raw):
    store = PreferencesStore()
    store.directory.mkdir(parents=True)
    store.path.write_text(raw)
    runner = CliRunner()
    assert runner.invoke(app, ["config", "show"]).exit_code == 1
    assert runner.invoke(app, ["pwd"]).exit_code == 0
    assert runner.invoke(app, ["config", "reset", "--yes"]).exit_code == 0
    assert store.load() == Preferences()


def test_first_start_guides_provider_then_model_without_running_inference(isolated, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return catalog(request)

    store = ProviderStore(isolated / "profiles", transport=httpx.MockTransport(handler))
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)
    app = CodaroApp(Agent(Repository(isolated), UIModel(), mode="ask"), setup_required=True)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ProviderSetup)
            assert app.screen.onboarding
            app.screen.query_one("#provider-key", Input).value = "private-key"
            await pilot.click("#provider-save")
            for _ in range(100):
                await pilot.pause(0.01)
                if isinstance(app.screen, ModelPicker):
                    break
            assert isinstance(app.screen, ModelPicker) and app.screen.onboarding
            app.screen.query_one("#model-id", Select).value = "small"
            await pilot.click("#model-use")
            await pilot.pause()
            assert app.provider_available and not app.setup_required
            assert store.active_settings().model == "small"
            assert len(requests) == 2  # catalog and selected-model metadata, no inference
            assert not resolve_configuration().needs_setup

    run_ui(scenario())


def test_cancelled_setup_cannot_call_the_placeholder_model(isolated, monkeypatch):
    store = ProviderStore(isolated / "profiles", transport=httpx.MockTransport(catalog))
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)
    model = UIModel()
    app = CodaroApp(Agent(Repository(isolated), model, mode="ask"), setup_required=True)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            assert not app.provider_available
            app.query_one(Prompt).value = "Pergunta"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ProviderSetup)
            assert not model.started.is_set()
            assert not app.agent.turns
            assert app.query_one(Prompt).value == "Pergunta"

    run_ui(scenario())


@pytest.mark.parametrize("size", [(60, 20), (120, 35)])
def test_preferences_ui_is_persistent_and_keeps_permissions_and_draft(isolated, size):
    app = CodaroApp(Agent(Repository(isolated), UIModel(), mode="ask"))

    async def scenario():
        async with app.run_test(size=size) as pilot:
            prompt = app.query_one(Prompt)
            prompt.value = "Meu rascunho"
            await app.open_provider_menu("/settings")
            await pilot.pause()
            assert isinstance(app.screen, PreferencesDialog)
            dialog = app.screen
            for button in dialog.query(Button):
                clip = app.screen._compositor.find_widget(button).clip
                assert button.region.intersection(clip) == button.region
            dialog.query_one("#preference-mode", Select).value = "plan"
            dialog.query_one("#preference-resume", Checkbox).value = True
            dialog.query_one("#preference-max_steps", Input).value = "15"
            await pilot.click("#preferences-save")
            await pilot.pause()
            assert PreferencesStore().load().mode == "plan"
            assert PreferencesStore().load().max_steps == 15
            assert prompt.value == "Meu rascunho"
            assert app.agent.mode == "ask" and app.agent.policy.kind == "action"
            config = resolve_configuration()
            assert config.preferences.resume and config.preferences.mode == "plan"

    run_ui(scenario())
