import json

import httpx

from codaro.agent import Agent
from codaro.context import TokenCounter
from codaro.provider import OpenAICompatible, Settings, build_payload
from codaro.repository import Repository


def test_counter_needs_stable_samples_before_reducing_estimate():
    counter = TokenCounter()
    payload = build_payload("test", [{"role": "user", "content": "x" * 1000}], None)
    actual = int(counter.base_count(payload) * 0.5)
    for _ in range(7):
        counter.observe(payload, actual)
        assert counter.scale == 1.0
    counter.observe(payload, actual)
    assert counter.scale == 0.6
    counter.observe(payload, counter.base_count(payload) * 2)
    assert counter.scale >= 2.3
    assert not counter.observe(payload, True)
    assert not counter.observe(payload, 2_000_001)
    assert not counter.observe(payload, None)


def test_calibration_uses_server_usage_and_is_scoped_to_endpoint_model(tmp_path):
    events = []

    def handle(request):
        json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 5500, "completion_tokens": 2},
            },
        )

    settings = Settings("https://test.invalid/v1", "one")
    agent = Agent(Repository(tmp_path), OpenAICompatible(settings, httpx.MockTransport(handle)))
    agent.ask("Investigue.", on_detail=events.append)
    assert agent.counter.scale > 1.0
    assert any(event.reported_tokens == 5500 for event in events)
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["turns"][0]["budget"]["reported_prompt_tokens"] == 5500
    restored = Agent(Repository(tmp_path), OpenAICompatible(settings, httpx.MockTransport(handle)))
    restored.ask("Continue.")
    assert len(restored.counter.samples) == 2
    other = Agent(
        Repository(tmp_path),
        OpenAICompatible(Settings("https://test.invalid/v1", "two"), httpx.MockTransport(handle)),
    )
    other.ask("Investigue.")
    assert len(other.counter.samples) == 1
