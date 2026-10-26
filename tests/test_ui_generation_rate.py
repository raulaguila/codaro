import httpx
import pytest
from test_tui import UIModel, run_ui
from textual.widgets import Static

from codaro.agent import Agent, AgentEvent
from codaro.llm import Settings
from codaro.llm.ollama import Ollama
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.generation import GenerationRate


def test_estimate_excludes_first_fragment_wait_and_resets_between_calls():
    now = [100.0]
    rate = GenerationRate(clock=lambda: now[0])
    assert rate.value() is None
    rate.append("abcd")
    assert rate.value() is None
    now[0] += 2
    rate.append("x" * 80)
    assert rate.value() == (10, "estimate")
    assert rate.display()[0] == "≈10 t/s"
    now[0] += 50
    assert rate.value() == (10, "estimate")
    rate.reset()
    assert rate.value() is None


@pytest.mark.parametrize(
    "tokens,seconds", [(True, 1), (-1, 1), (10, 0), (10, float("nan")), (None, 2)]
)
def test_invalid_metrics_never_produce_a_rate(tokens, seconds):
    rate = GenerationRate()
    rate.report(tokens, generation_seconds=seconds)
    assert rate.value() is None


def test_provider_decode_speed_is_distinct_from_whole_request_average():
    rate = GenerationRate()
    rate.report(100, generation_seconds=2, request_seconds=20)
    assert rate.display()[0] == "50 t/s"
    assert rate.value()[1] == "provider"
    rate.reset()
    rate.report(100, request_seconds=20)
    assert rate.display()[0] == "5.0 t/s méd."
    assert rate.value()[1] == "request"


def test_local_tokenizer_estimate_counts_incremental_text():
    class Encoder:
        def encode(self, text, **kwargs):
            return text.split()

    now = [1.0]
    rate = GenerationRate(clock=lambda: now[0], encoder=Encoder())
    rate.append("first ")
    now[0] += 2
    rate.append("second third fourth fifth")
    assert rate.value() == (2, "estimate")


def test_ollama_metrics_reach_agent_events_with_native_decode_duration(tmp_path):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "Olá."},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 100,
                "eval_count": 80,
                "eval_duration": 2_000_000_000,
            },
        )
    )
    provider = Ollama(Settings("http://local.test:11434", "test", api_style="ollama"), transport)
    events = []
    Agent(Repository(tmp_path), provider).ask("Olá", on_detail=events.append)
    metric = next(event for event in events if event.kind == "generation_metrics")
    assert metric.output_tokens == 80
    assert metric.generation_seconds == 2
    assert metric.request_seconds > 0


def test_rate_is_visible_during_streaming_and_cleared_on_cancellation(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
    now = [10.0]
    app.generation_rate.clock = lambda: now[0]

    async def scenario():
        async with app.run_test(size=(60, 20)) as pilot:
            app.busy = True
            app.activity(
                AgentEvent("model_start", "Gerando", context_tokens=100, context_limit=4096)
            )
            app.append_delta("abcd")
            now[0] += 2
            app.append_delta("x" * 80)
            await pilot.pause()
            assert "≈10 t/s" in str(app.query_one("#generation-rate", Static).render())
            assert "cancelar" not in str(app.query_one("#status", Static).render())
            app.finish("Interrompido", successful=False, outcome="cancelled")
            assert not app.query_one("#generation-rate").display
            app.activity(AgentEvent("model_start", "Gerando"))
            assert app.generation_rate.value() is None

    run_ui(scenario())
