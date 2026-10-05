import json

import httpx
import pytest

from codaro.provider import MAX_RESPONSE_BYTES, ModelError, OpenAICompatible, Settings


def provider(handler):
    return OpenAICompatible(
        Settings("https://example.test/v1", "test-model", "test-key"), httpx.MockTransport(handler)
    )


def result(message, **kwargs):
    return {"choices": [{"message": message, **kwargs}]}


def test_provider_request_and_response():
    def handle(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["tools"] == [{"type": "function"}]
        return httpx.Response(200, json=result({"role": "assistant", "content": "OK"}))

    message = provider(handle).complete(
        [{"role": "user", "content": "Olá"}], [{"type": "function"}]
    )
    assert message["content"] == "OK"


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "text",
        {},
        {"choices": []},
        {"choices": "wrong"},
        {"choices": [None]},
        result({}),
        result({"content": ["text"]}),
        result({"content": "OK", "tool_calls": {}}),
        result({"content": "OK", "tool_calls": "invalid"}),
        result({"content": None, "tool_calls": [{}]}),
        result({"role": "system", "content": "untrusted"}),
    ],
)
def test_malformed_responses_fail_cleanly(body):
    with pytest.raises(ModelError):
        provider(lambda request: httpx.Response(200, json=body)).complete([])


def test_duplicate_call_ids_are_rejected():
    call = {
        "id": "duplicate",
        "type": "function",
        "function": {"name": "list_files", "arguments": "{}"},
    }
    response = result({"content": None, "tool_calls": [call, call]})
    with pytest.raises(ModelError, match="duplicada"):
        provider(lambda request: httpx.Response(200, json=response)).complete([])


def test_invalid_json_is_reported():
    with pytest.raises(ModelError):
        provider(lambda request: httpx.Response(200, content=b"not json")).complete([])


def test_response_size_is_bounded():
    with pytest.raises(ModelError, match="256 KB"):
        provider(
            lambda request: httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        ).complete([])


def test_truncated_generation_is_not_silently_accepted():
    response = result({"content": "incomplete"}, finish_reason="length")
    with pytest.raises(ModelError, match="limite de saída"):
        provider(lambda request: httpx.Response(200, json=response)).complete([])


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_http_errors_do_not_echo_sensitive_bodies(status):
    with pytest.raises(ModelError) as failure:
        provider(lambda request: httpx.Response(status, text="sensitive-server-body")).complete([])
    assert str(status) in str(failure.value)
    assert "sensitive-server-body" not in str(failure.value)


def test_transient_status_is_retried(monkeypatch):
    attempts = []
    monkeypatch.setattr("codaro.provider.time.sleep", lambda _: None)

    def handle(request):
        attempts.append(request)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=result({"content": "OK"}))

    assert provider(handle).complete([])["content"] == "OK"
    assert len(attempts) == 3


def test_retries_are_bounded(monkeypatch):
    attempts = []
    monkeypatch.setattr("codaro.provider.time.sleep", lambda _: None)

    def handle(request):
        attempts.append(request)
        return httpx.Response(429)

    with pytest.raises(ModelError, match="429"):
        provider(handle).complete([])
    assert len(attempts) == 3


def test_timeout_is_reported():
    def handle(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(ModelError, match="esgotado"):
        provider(handle).complete([])


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/test",
        "",
        "https://user:password@example.test",
        "https://example.test?key=private",
        "https://example.test#fragment",
        "https://example.test:invalid",
        "http://example.test:0",
        "http://exa mple.test",
    ],
)
def test_settings_reject_invalid_urls(url):
    with pytest.raises(ValueError):
        Settings(url, "model")


@pytest.mark.parametrize("timeout", [0, -1, 301, float("nan")])
def test_settings_validate_timeout(timeout):
    with pytest.raises(ValueError):
        Settings("http://localhost:11434/v1", "model", timeout=timeout)


def test_environment_settings(monkeypatch):
    monkeypatch.setenv("CODARO_BASE_URL", "http://localhost:11434/v1/")
    monkeypatch.setenv("CODARO_MODEL", "local-model")
    monkeypatch.setenv("CODARO_TIMEOUT", "12")
    assert Settings.from_env().timeout == 12
    assert Settings.from_env().base_url.endswith("/v1")
    monkeypatch.setenv("CODARO_TIMEOUT", "not a number")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_deeply_nested_remote_json_fails_cleanly():
    payload = b'{"choices":' + b"[" * 1500 + b"]" * 1500 + b"}"
    with pytest.raises(ModelError):
        provider(lambda request: httpx.Response(200, content=payload)).complete([])
