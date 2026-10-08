"""Known provider URLs and native endpoint normalization."""

PRESETS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "openai-compatible": "",
    "custom": "",
}


def provider_base_url(kind, base_url):
    base = (base_url or PRESETS.get(kind, "")).strip().rstrip("/")
    if kind == "ollama" and not base.endswith("/v1"):
        base = base.removesuffix("/api") + "/v1"
    return base
