"""One resolver for CLI startup, model settings and persisted preferences."""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass, replace

from codaro.llm.config import Settings
from codaro.preferences import Preferences, PreferencesStore

startup_overrides: ContextVar[dict | None] = ContextVar("codaro_startup_overrides", default=None)


@dataclass(frozen=True)
class Configuration:
    model: Settings
    preferences: Preferences
    sources: dict[str, str]
    needs_setup: bool
    ignored_environment: tuple[str, ...] = ()
    setup_profile: str | None = None


def resolve_configuration(**overrides):
    from codaro.llm.profiles import ProviderStore

    options = {
        **(startup_overrides.get() or {}),
        **{k: v for k, v in overrides.items() if v is not None},
    }
    preferences = PreferencesStore().load()
    source = "preferências salvas" if PreferencesStore().path.exists() else "padrão"
    sources = {
        name: source
        for name in ("mode", "resume", "max_steps", "max_seconds", "max_corrections", "timeout")
    }
    updates = {}
    for name in ("mode", "resume", "max_steps", "max_seconds", "max_corrections"):
        if name in options:
            updates[name] = options[name]
            sources[name] = "opção de inicialização"
    preferences = replace(preferences, **updates)
    store = ProviderStore()
    selected = options.get("provider") or os.getenv("CODARO_PROVIDER", "").strip()
    data = store.load() if selected != "env" else {"active": None, "profiles": {}}
    configured = None
    setup_profile = None
    provider_source = "padrão (configuração pendente)"
    if selected != "env" and (selected or data["active"]):
        name, profile = store.profile(selected or None)
        setup_profile = name
        model = next((item for item in profile["models"] if item["id"] == profile["model"]), None)
        if model is not None:
            # Startup uses the persisted API catalog, without blocking on a network call.
            configured = store.settings_for(name, profile, model)
        provider_source = "perfil explícito: " + name if selected else "perfil ativo: " + name
    environment_connection = any(os.getenv(name) for name in ("CODARO_BASE_URL", "CODARO_MODEL"))
    use_environment = selected == "env" or (
        not selected and not data["active"] and environment_connection
    )
    if use_environment:
        configured = Settings(
            os.getenv("CODARO_BASE_URL", "http://localhost:11434/v1").strip().rstrip("/"),
            os.getenv("CODARO_MODEL", "qwen2.5:7b").strip(),
            os.getenv("CODARO_API_KEY", "").strip(),
        )
        provider_source = (
            "ambiente explícito" if selected == "env" else "ambiente (compatibilidade)"
        )
    sources["provider"] = provider_source
    needs_setup = configured is None
    base = configured or Settings("http://localhost:11434/v1", "qwen2.5:7b")

    def number(env, fallback, converter):
        try:
            return converter(os.getenv(env, str(fallback)))
        except ValueError as exc:
            if converter is int:
                raise ValueError("Use inteiros para " + env + ".") from exc
            raise ValueError(env + " deve ser um número válido.") from exc

    window = options.get("context_window")
    if window is None:
        window = number("CODARO_CONTEXT_WINDOW", base.context_window, int)
    sources["context_window"] = (
        "opção de inicialização"
        if "context_window" in options
        else "CODARO_CONTEXT_WINDOW"
        if os.getenv("CODARO_CONTEXT_WINDOW")
        else base.context_source
    )
    raw_output = os.getenv("CODARO_MAX_OUTPUT_TOKENS", "auto").strip().lower()
    try:
        output = None if raw_output in {"", "auto"} else int(raw_output)
    except ValueError as exc:
        raise ValueError("CODARO_MAX_OUTPUT_TOKENS deve ser inteiro ou auto.") from exc
    if output is not None and base.model_max_output_tokens:
        output = min(output, base.model_max_output_tokens, 32768)
    sources["max_output_tokens"] = (
        "CODARO_MAX_OUTPUT_TOKENS" if raw_output != "auto" else "automático"
    )
    tls = options.get("tls_insecure")
    if tls is None:
        raw_tls = os.getenv("CODARO_TLS_INSECURE", str(base.tls_insecure)).strip().lower()
        values = {
            "true": True,
            "1": True,
            "yes": True,
            "on": True,
            "false": False,
            "0": False,
            "no": False,
            "off": False,
        }
        if raw_tls not in values:
            raise ValueError("CODARO_TLS_INSECURE deve ser true/false ou 1/0.")
        tls = values[raw_tls]
    sources["tls_insecure"] = (
        "opção de inicialização"
        if "tls_insecure" in options
        else "CODARO_TLS_INSECURE"
        if os.getenv("CODARO_TLS_INSECURE")
        else provider_source
    )
    timeout = number("CODARO_TIMEOUT", preferences.timeout, float)
    if os.getenv("CODARO_TIMEOUT"):
        sources["timeout"] = "CODARO_TIMEOUT"
    ignored = tuple(
        name
        for name in ("CODARO_BASE_URL", "CODARO_MODEL", "CODARO_API_KEY")
        if os.getenv(name) and not use_environment
    )
    model = replace(
        base,
        tls_insecure=tls,
        context_window=window,
        max_output_tokens=output,
        timeout=timeout,
        token_encoding=os.getenv("CODARO_TOKEN_ENCODING", "").strip() or None,
        context_source="configuração do usuário"
        if "context_window" in options or os.getenv("CODARO_CONTEXT_WINDOW")
        else base.context_source,
    )
    return Configuration(model, preferences, sources, needs_setup, ignored, setup_profile)
