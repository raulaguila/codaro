import json

import httpx
import pytest
from test_tui import UIModel, run_ui
from textual.widgets import Input, Select, Static

from codaro.agent import Agent
from codaro.provider import ModelError, create_provider
from codaro.providers import ProviderStore
from codaro.repository import Repository
from codaro.tui import CodaroApp


def ollama_handler(request):
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
    if request.url.path == "/api/show":
        return httpx.Response(200, json={"parameters": "", "capabilities": ["tools"]})
    assert request.url.path == "/v1/chat/completions"
    payload = json.loads(request.content)
    if payload["messages"][-1]["role"] == "tool":
        marker = json.loads(payload["messages"][-1]["content"])["probe_result"]
        return httpx.Response(200, json={"choices": [{"message": {"content": marker}}]})
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "probe",
                                "type": "function",
                                "function": {"name": "codaro_probe", "arguments": "{}"},
                            }
                        ],
                        "content": None,
                    }
                }
            ]
        },
    )


@pytest.mark.parametrize("suffix", ["", "/", "/api", "/v1", "/v1/"])
def test_ollama_root_urls_support_catalog_and_inference(tmp_path, suffix):
    store = ProviderStore(tmp_path / "config", transport=httpx.MockTransport(ollama_handler))
    store.register("ollama", "", base_url="http://localhost:11434" + suffix)
    settings = store.select("ollama", "llama3.1:8b")
    assert settings.base_url == "http://localhost:11434/v1"
    assert settings.context_window == 4096 and "fallback" in settings.context_source
    create_provider(settings, transport=store.transport).check_tool_calling()


def test_existing_ollama_profile_without_v1_is_normalized_on_load(tmp_path):
    store = ProviderStore(tmp_path / "config", transport=httpx.MockTransport(ollama_handler))
    store.register("ollama", "")
    store.select("ollama", "llama3.1:8b")
    store.mutate(
        lambda value: value["profiles"]["ollama"].update(base_url="http://localhost:11434")
    )
    assert store.active_settings().base_url == "http://localhost:11434/v1"


def test_connection_checks_catalog_and_tool_round_trip_without_saving(tmp_path):
    store = ProviderStore(tmp_path / "config", transport=httpx.MockTransport(ollama_handler))
    assert store.test_connection("ollama", "", base_url="http://localhost:11434")
    store.test_connection("ollama", "", base_url="http://localhost:11434", model_id="llama3.1:8b")
    assert not store.path.exists()


def test_successful_catalog_does_not_hide_inference_404(tmp_path):
    def handler(request):
        if request.url.path.endswith("chat/completions"):
            return httpx.Response(404, json={"error": "fake-key-do-not-display"})
        return ollama_handler(request)

    store = ProviderStore(tmp_path / "config", transport=httpx.MockTransport(handler))
    assert store.test_connection("ollama", "fake-key-do-not-display")
    with pytest.raises(ModelError, match="404") as error:
        store.test_connection("ollama", "fake-key-do-not-display", model_id="llama3.1:8b")
    assert "fake-key-do-not-display" not in str(error.value)
    assert not store.path.exists()


def test_provider_form_tests_connection_before_registration(tmp_path, monkeypatch):
    store = ProviderStore(tmp_path / "config", transport=httpx.MockTransport(ollama_handler))
    monkeypatch.setattr("codaro.providers.ProviderStore", lambda: store)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 50)) as pilot:
            await app.local_command("/providers")
            await pilot.pause()
            app.screen.query_one("#provider-kind", Select).value = "ollama"
            await pilot.pause()
            app.screen.query_one("#provider-url", Input).value = "http://localhost:11434"
            await pilot.click("#provider-test")
            for _ in range(100):
                await pilot.pause(0.02)
                if "Catálogo acessível" in str(
                    app.screen.query_one("#provider-error", Static).render()
                ):
                    break
            assert not store.path.exists()
            app.screen.query_one("#provider-test-model", Select).value = "llama3.1:8b"
            await pilot.pause()
            await pilot.click("#provider-test")
            for _ in range(100):
                await pilot.pause(0.02)
                if "verificadas" in str(app.screen.query_one("#provider-error", Static).render()):
                    break
            assert "verificadas" in str(app.screen.query_one("#provider-error", Static).render())
            assert not store.path.exists()

    run_ui(scenario())
