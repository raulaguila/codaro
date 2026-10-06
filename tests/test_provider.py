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


@pytest.mark.parametrize("insecure", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_tls_setting_reaches_http_client_for_both_response_modes(monkeypatch, insecure, streaming):
    captured = []
    original_client = httpx.Client

    def client(**kwargs):
        captured.append(kwargs["verify"])
        return original_client(**kwargs)

    monkeypatch.setattr("codaro.provider.httpx.Client", client)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=result({"content": "OK"}))
    )
    model = OpenAICompatible(
        Settings("https://example.test/v1", "test", tls_insecure=insecure), transport
    )
    answer = model.stream([]) if streaming else model.complete([])
    assert answer["content"] == "OK"
    assert captured == [not insecure]


def test_tls_is_secure_by_default_and_env_override_is_explicit(monkeypatch):
    monkeypatch.delenv("CODARO_TLS_INSECURE", raising=False)
    assert not Settings.from_env().tls_insecure
    monkeypatch.setenv("CODARO_TLS_INSECURE", "true")
    assert Settings.from_env().tls_insecure
    assert not Settings.from_env(tls_insecure=False).tls_insecure
    monkeypatch.setenv("CODARO_TLS_INSECURE", "false")
    assert Settings.from_env(tls_insecure=True).tls_insecure


@pytest.mark.parametrize("raw", ["false", "0", "off", "no", "TRUE", "1", "on", "yes"])
def test_tls_boolean_env_is_parsed(monkeypatch, raw):
    monkeypatch.setenv("CODARO_TLS_INSECURE", raw)
    assert Settings.from_env().tls_insecure == (raw.lower() in {"true", "1", "on", "yes"})


def test_invalid_tls_values_are_rejected(monkeypatch):
    monkeypatch.setenv("CODARO_TLS_INSECURE", "perhaps")
    with pytest.raises(ValueError, match="CODARO_TLS_INSECURE"):
        Settings.from_env()
    with pytest.raises(ValueError, match="booleano"):
        Settings("https://example.test/v1", "model", tls_insecure="false")


def test_tool_calling_probe_checks_structured_protocol():
    def handle(request):
        payload = json.loads(request.content)
        if payload.get("stream"):
            assert "tools" not in payload
            message = payload["messages"][-1]
            assert message["role"] == "tool"
            assert message["tool_call_id"] == "probe"
            marker = json.loads(message["content"])["probe_result"]
            return httpx.Response(200, json=result({"content": marker}))
        assert payload["tool_choice"] == "auto"
        assert payload["tools"][0]["function"]["name"] == "codaro_probe"
        return httpx.Response(
            200,
            json=result(
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "probe",
                            "type": "function",
                            "function": {"name": "codaro_probe", "arguments": "{}"},
                        }
                    ],
                }
            ),
        )

    provider(handle).check_tool_calling()


def test_tool_calling_probe_rejects_json_written_as_text():
    model = provider(
        lambda request: httpx.Response(
            200, json=result({"content": '{"name":"codaro_probe","parameters":{}}'})
        )
    )
    with pytest.raises(ModelError, match="tool_calls válidos"):
        model.check_tool_calling()


def test_tool_probe_rejects_model_ignoring_tool_result():
    def handle(request):
        payload = json.loads(request.content)
        if payload.get("stream"):
            return httpx.Response(200, json=result({"content": "Vou chamar a ferramenta."}))
        return httpx.Response(
            200,
            json=result(
                {
                    "tool_calls": [
                        {
                            "id": "probe",
                            "type": "function",
                            "function": {"name": "codaro_probe", "arguments": "{}"},
                        }
                    ]
                }
            ),
        )

    with pytest.raises(ModelError, match="não concluiu o ciclo"):
        provider(handle).check_tool_calling()


@pytest.mark.parametrize(
    "message,reason",
    [
        (None, "tool_calls"),
        ({"content": "Vou chamar uma ferramenta."}, "tool_calls"),
        ({"content": "Olá", "function_call": {"name": "list_files", "arguments": "{}"}}, "stop"),
        ({"content": "Olá"}, "function_call"),
    ],
)
def test_missing_or_legacy_tool_protocol_is_not_accepted_as_an_answer(message, reason):
    with pytest.raises(ModelError):
        provider(
            lambda _: httpx.Response(200, json=result(message, finish_reason=reason))
        ).complete([])
