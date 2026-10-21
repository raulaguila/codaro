import json

import httpx

from codaro.llm import OpenAICompatible, Settings
from codaro.redaction import redact_credentials
from codaro.sessions import SessionStore
from codaro.trace import PromptFlow

TOKENS = [
    "sk-proj-" + "aB12" * 12,
    "sk-ant-api03-" + "Ab12" * 12,
    "ghp_" + "ab12" * 10,
    "ghs_" + "ab12" * 10,
    "gho_" + "ab12" * 10,
    "github_pat_" + "ab12_" * 12,
    "AKIA" + "ABCDEF0123456789",
    "AIza" + "x" * 35,
]


def test_detected_credentials_are_masked_in_nested_data_keys_and_json_text():
    secrets = [*TOKENS, "very_private_password", "x" * 40]
    value = {
        "content": "\n".join(TOKENS) + '\nPASSWORD="very_private_password"',
        "nested": [{"Authorization": "Bearer " + "x" * 40}],
        TOKENS[0]: "key is a secret too",
    }
    masked = redact_credentials(value)
    assert all(secret not in json.dumps(masked) for secret in secrets)
    assert redact_credentials(masked) == masked
    assert value["content"].startswith(TOKENS[0])  # Does not mutate source inputs.


def test_ordinary_code_hashes_token_settings_and_examples_are_preserved():
    value = {
        "token_encoding": "cl100k_base",
        "max_output_tokens": 8192,
        "content": "sha256 = '" + "a" * 64 + "'\nsk-example\nAPI_KEY=example",
    }
    assert redact_credentials(value) == value


def test_pem_private_key_is_masked_but_public_key_is_preserved():
    private = "-----BEGIN RSA PRIVATE KEY-----\nABCDEF\n-----END RSA PRIVATE KEY-----"
    public = "-----BEGIN PUBLIC KEY-----\nABCDEF\n-----END PUBLIC KEY-----"
    assert redact_credentials(private + public) == "[REDACTED PRIVATE KEY]" + public


def test_masks_persistence_trace_and_outgoing_model_content(tmp_path):
    secret = TOKENS[0]
    settings = Settings("https://example.test/v1", "model", "provider-test-key")
    content = f"Example configuration: API_KEY='{secret}'"
    turn = [{"role": "user", "content": content}, {"role": "assistant", "content": "ok"}]
    store = SessionStore(tmp_path, settings.api_key)
    store.save([turn], "model")
    assert secret not in store.path.read_text()
    flow = PromptFlow(tmp_path, content, settings, allow_edits=False, limits={})
    flow.append_event("test", {"content": content})
    assert secret not in flow.path.read_text()
    assert secret not in flow.archive_path.read_text()

    def respond(request):
        assert request.headers["Authorization"] == "Bearer provider-test-key"
        assert secret not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = OpenAICompatible(settings, httpx.MockTransport(respond))
    assert provider.complete(turn[:1])["content"] == "ok"
