import json
import os

import httpx
import pytest
from typer.testing import CliRunner

from codaro.agent import Agent
from codaro.cli import app
from codaro.provider import ModelError, Settings, create_provider
from codaro.providers import ProviderStore
from codaro.repository import Repository


def store_for(tmp_path, handler):
    return ProviderStore(tmp_path / "config", transport=httpx.MockTransport(handler))


def catalog_response(request):
    assert request.headers["authorization"] == "Bearer test-secret"
    return httpx.Response(
        200,
        json={
            "data": [
                {"id": "small", "context_window": 8192, "max_output_tokens": 512},
                {"id": "large", "context_window": 131072},
            ]
        },
    )


def test_registration_selection_and_env_precedence(tmp_path, monkeypatch):
    store = store_for(tmp_path, catalog_response)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)
    for variable in (
        "CODARO_BASE_URL",
        "CODARO_MODEL",
        "CODARO_API_KEY",
        "CODARO_PROVIDER",
        "CODARO_CONTEXT_WINDOW",
    ):
        monkeypatch.delenv(variable, raising=False)
    models = store.register("groq", "test-secret")
    assert [item["id"] for item in models] == ["large", "small"]
    settings = store.select("groq", "small")
    assert settings.context_window == 8192 and settings.max_output_tokens is None
    assert settings.output_reserve == 512
    assert "API" in settings.context_source and "test-secret" not in repr(settings)
    assert Settings.from_env() == settings
    monkeypatch.setenv("CODARO_MAX_OUTPUT_TOKENS", "2048")
    assert Settings.from_env().max_output_tokens == 512
    monkeypatch.delenv("CODARO_MAX_OUTPUT_TOKENS")
    assert Settings.from_env(context_window=4096).context_window == 4096
    monkeypatch.setenv("CODARO_MODEL", "legacy-model")
    assert Settings.from_env().model == "legacy-model"
    monkeypatch.setenv("CODARO_PROVIDER", "groq")
    assert Settings.from_env().model == "small"
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o777 == 0o600


def test_unknown_context_is_explicit_fallback_not_invented_metadata(tmp_path):
    store = store_for(tmp_path, lambda _: httpx.Response(200, json={"data": [{"id": "gpt-test"}]}))
    models = store.register("openai", "test-secret")
    assert models[0]["context_window"] is None
    settings = store.select("openai", "gpt-test")
    assert settings.context_window == 16384 and "fallback" in settings.context_source
    with pytest.raises(ValueError, match="não está no catálogo"):
        store.select("openai", "invented")


def test_gemini_native_catalog_metadata_and_compatible_transport(tmp_path):
    def handler(request):
        assert str(request.url).startswith(
            "https://generativelanguage.googleapis.com/v1beta/models"
        )
        assert request.headers["x-goog-api-key"] == "gemini-key"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-test",
                        "displayName": "Gemini Test",
                        "inputTokenLimit": 1048576,
                        "outputTokenLimit": 65536,
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ]
            },
        )

    store = store_for(tmp_path, handler)
    store.register("gemini", "gemini-key")
    settings = store.select("gemini", "gemini-test")
    assert settings.context_window == 1048576
    assert settings.base_url.endswith("/v1beta/openai") and settings.api_style == "openai"


def test_ollama_reads_configured_num_ctx_not_architecture_limit(tmp_path):
    def handler(request):
        assert "authorization" not in request.headers
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local:7b"}]})
        assert request.url.path == "/api/show"
        assert json.loads(request.content) == {"model": "local:7b"}
        return httpx.Response(
            200,
            json={
                "parameters": "num_ctx 8192\ntemperature 0.1",
                "model_info": {"llama.context_length": 131072},
                "capabilities": ["tools"],
            },
        )

    store = store_for(tmp_path, handler)
    store.register("ollama", "")
    settings = store.select("ollama", "local:7b")
    assert settings.context_window == 8192 and settings.context_source.endswith("num_ctx")


def test_catalog_pagination_and_failed_refresh_preserves_profile(tmp_path):
    failed = False

    def handler(request):
        if failed:
            return httpx.Response(401, json={"error": "echo test-secret"})
        if request.url.params.get("after"):
            return httpx.Response(200, json={"data": [{"id": "b"}], "has_more": False})
        return httpx.Response(200, json={"data": [{"id": "a"}], "has_more": True, "last_id": "a"})

    store = store_for(tmp_path, handler)
    assert len(store.register("openai", "test-secret")) == 2
    saved = store.path.read_bytes()
    failed = True
    with pytest.raises(ModelError) as error:
        store.models("openai", refresh=True)
    assert "test-secret" not in str(error.value)
    assert store.path.read_bytes() == saved


@pytest.mark.parametrize("status", [302, 401, 403, 500])
def test_registration_failure_never_saves_credential(tmp_path, status):
    store = store_for(tmp_path, lambda _: httpx.Response(status, json={"error": "test-secret"}))
    with pytest.raises(ModelError):
        store.register("groq", "test-secret")
    assert not store.path.exists()


def test_provider_switch_updates_budgets_and_redacts_both_credentials(tmp_path):
    agent = Agent(
        Repository(tmp_path), create_provider(Settings("https://a.test/v1", "first", "old-key"))
    )
    replacement = Settings(
        "https://b.test/v1", "second", "new-key", context_window=8192, max_output_tokens=512
    )
    agent.set_provider(create_provider(replacement))
    assert agent.input_limit == agent.adaptive_input_limit == 7168
    assert agent.memory.redact("old-key new-key") == "[REDACTED] [REDACTED]"
    assert agent.tasks.redact("new-key") == "[REDACTED]"


def test_credential_storage_rejects_links_and_public_permissions(tmp_path):
    store = store_for(tmp_path, catalog_response)
    store.register("groq", "test-secret")
    if os.name == "posix":
        store.path.chmod(0o644)
        with pytest.raises(ValueError):
            store.load()
        store.path.chmod(0o600)
    original = store.path.with_name("original.json")
    store.path.rename(original)
    store.path.symlink_to(original)
    with pytest.raises(ValueError):
        store.load()


def test_byok_cli_registers_without_echoing_key_and_lists_models(tmp_path, monkeypatch):
    store = store_for(tmp_path, catalog_response)
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)
    runner = CliRunner()
    result = runner.invoke(app, ["providers", "add", "groq"], input="test-secret\nsmall\n")
    assert result.exit_code == 0, result.output
    assert "test-secret" not in result.output
    assert "small" in result.output and store.active_settings().model == "small"
    result = runner.invoke(app, ["providers", "list"])
    assert result.exit_code == 0 and "test-secret" not in result.output
    result = runner.invoke(app, ["models", "use", "large"])
    assert result.exit_code == 0 and store.active_settings().context_window == 131072
    result = runner.invoke(app, ["providers", "remove", "groq"])
    assert result.exit_code == 0 and not store.load()["profiles"]


def test_compatible_tls_insecure_is_explicit_and_preserved(tmp_path, monkeypatch):
    store = store_for(tmp_path, catalog_response)
    monkeypatch.setattr("codaro.llm.profiles.ProviderStore", lambda: store)
    monkeypatch.setenv("TEST_BYOK_KEY", "test-secret")
    result = CliRunner().invoke(
        app,
        [
            "providers",
            "add",
            "openai-compatible",
            "--base-url",
            "https://proxy.test/v1",
            "--tls-insecure",
            "--key-env",
            "TEST_BYOK_KEY",
        ],
        input="small\n",
    )
    assert result.exit_code == 0, result.output
    assert store.active_settings().tls_insecure


def test_selection_refreshes_model_metadata_and_preserves_manual_override(tmp_path):
    window = 8192

    def handler(_):
        return httpx.Response(200, json={"data": [{"id": "model", "context_window": window}]})

    store = store_for(tmp_path, handler)
    store.register("groq", "test-secret")
    window = 32768
    assert store.select("groq", "model").context_window == 32768
    assert store.select("groq", "model", context_window=4096).context_window == 4096
    store.models("groq", refresh=True)
    assert store.active_settings().context_window == 4096


def test_credentials_are_excluded_when_config_is_inside_repository(tmp_path):
    store = store_for(tmp_path, catalog_response)
    store.register("groq", "test-secret")
    assert str(store.path.relative_to(tmp_path)) not in Repository(tmp_path).files()
