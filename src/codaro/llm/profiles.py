"""Persistence, selection and lifecycle of user-level BYOK profiles."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from codaro.llm.catalog import ModelCatalog, positive, text
from codaro.llm.config import Settings
from codaro.llm.endpoints import PRESETS, provider_base_url
from codaro.llm.errors import (
    ModelError,
)
from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write, timestamp

MAX_CONFIG_BYTES = 4_000_000


class ProviderStore:
    def __init__(self, directory=None, *, transport=None):
        parent = Path(os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")).expanduser()
        if directory is None and not parent.is_absolute():
            raise ValueError("XDG_CONFIG_HOME deve ser um caminho absoluto.")
        self.directory = Path(directory) if directory is not None else parent / "codaro"
        self.path = self.directory / "provider-credentials.json"
        self.transport = transport

    def load(self):
        try:
            value = private_json(self.path, MAX_CONFIG_BYTES, require_private=True)
        except FileNotFoundError:
            return {"version": 1, "active": None, "profiles": {}}
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("Configuração de provedores inválida.")
        profiles = value.get("profiles")
        if not isinstance(profiles, dict) or len(profiles) > 32:
            raise ValueError("Configuração de provedores inválida.")
        for name, profile in profiles.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) or not isinstance(profile, dict):
                raise ValueError("Perfil de provedor inválido.")
            if profile.get("kind") not in PRESETS or not isinstance(profile.get("api_key"), str):
                raise ValueError("Perfil de provedor inválido.")
            Settings(
                profile.get("base_url", ""),
                profile.get("model") or "unselected",
                profile["api_key"],
                tls_insecure=profile.get("tls_insecure", False),
            )
            models = profile.get("models")
            if not isinstance(models, list) or len(models) > 2000:
                raise ValueError("Catálogo salvo inválido.")
            for model in models:
                if not isinstance(model, dict):
                    raise ValueError("Modelo salvo inválido.")
                text(model.get("id"))
                text(model.get("name"))
                text(model.get("context_source"))
                for field in ("context_window", "max_output_tokens"):
                    if model.get(field) is not None and positive(model[field]) is None:
                        raise ValueError("Limites de modelo inválidos.")
                if model.get("tools") is not None and type(model["tools"]) is not bool:
                    raise ValueError("Capacidades de modelo inválidas.")
            overrides = profile.get("context_overrides", {})
            if not isinstance(overrides, dict) or len(overrides) > 2000:
                raise ValueError("Configuração de contexto inválida.")
            for identifier, limit in overrides.items():
                text(identifier)
                if type(limit) is not int or not 4096 <= limit <= 2_000_000:
                    raise ValueError("Configuração de contexto inválida.")
        if value.get("active") is not None and value["active"] not in profiles:
            raise ValueError("Provedor ativo inválido.")
        return value

    def mutate(self, operation):
        if self.directory.is_symlink():
            raise ValueError("Configuração de provedores não pode usar links.")
        self.directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with private_lock(self.directory / "providers.lock"):
            value = self.load()
            operation(value)
            raw = json.dumps(value, ensure_ascii=False).encode()
            if len(raw) > MAX_CONFIG_BYTES:
                raise ValueError("Configuração de provedores excede 4 MB.")
            atomic_write(self.path, raw)

    def profile(self, name=None):
        value = self.load()
        name = name or value["active"]
        if name not in value["profiles"]:
            raise ValueError("Cadastre um provedor com codaro providers add.")
        return name, value["profiles"][name]

    def register(self, kind, key, *, name=None, base_url=None, tls_insecure=False):
        if kind not in PRESETS:
            raise ValueError("Provedor desconhecido; use um preset ou custom.")
        name = name or kind
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
            raise ValueError("Nome do perfil inválido.")
        if name in self.load()["profiles"]:
            raise ValueError("Perfil já cadastrado; remova-o antes de substituir a credencial.")
        settings = Settings(
            provider_base_url(kind, base_url), "unselected", key, tls_insecure=tls_insecure
        )
        if kind not in {"ollama", "custom", "openai-compatible"} and not key:
            raise ValueError("Informe a API key do provedor.")
        profile = {
            "kind": kind,
            "base_url": settings.base_url,
            "api_key": key,
            "tls_insecure": tls_insecure,
            "model": "",
        }
        profile["models"] = ModelCatalog(profile, transport=self.transport).list()
        profile["updated_at"] = timestamp()

        def add(value):
            if name in value["profiles"] or len(value["profiles"]) >= 32:
                raise ValueError("Perfil já existe ou limite de 32 perfis atingido.")
            value["profiles"][name] = profile

        self.mutate(add)
        return profile["models"]

    def update(self, kind, key, *, previous_name, name=None, base_url=None, tls_insecure=False):
        previous_name, old = self.profile(previous_name)
        name = name or previous_name
        if kind not in PRESETS or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
            raise ValueError("Provedor ou nome de perfil inválido.")
        settings = Settings(
            provider_base_url(kind, base_url), "unselected", key, tls_insecure=tls_insecure
        )
        if kind not in {"ollama", "custom", "openai-compatible"} and not key:
            raise ValueError("Informe a API key do provedor.")
        candidate = {
            **old,
            "kind": kind,
            "base_url": settings.base_url,
            "api_key": key,
            "tls_insecure": tls_insecure,
        }
        candidate["models"] = ModelCatalog(candidate, transport=self.transport).list()
        if not any(item["id"] == old["model"] for item in candidate["models"]):
            candidate["model"] = ""
        if kind != old["kind"] or settings.base_url != old["base_url"]:
            candidate["context_overrides"] = {}
        candidate["updated_at"] = timestamp()

        def save(value):
            if value["profiles"].get(previous_name) != old:
                raise ValueError("Perfil alterado durante a consulta; tente novamente.")
            if name != previous_name and name in value["profiles"]:
                raise ValueError("Já existe um perfil com esse nome.")
            del value["profiles"][previous_name]
            value["profiles"][name] = candidate
            if value["active"] == previous_name:
                value["active"] = name

        self.mutate(save)
        return candidate["models"]

    def test_connection(
        self,
        kind,
        key,
        *,
        base_url=None,
        tls_insecure=False,
        model_id=None,
        cancelled=None,
        on_stage=None,
    ):
        """Check the form without saving; selected models get a complete tool round trip."""
        if kind not in PRESETS:
            raise ValueError("Provedor desconhecido.")
        settings = Settings(
            provider_base_url(kind, base_url),
            model_id or "unselected",
            key,
            tls_insecure=tls_insecure,
        )
        if kind not in {"ollama", "custom", "openai-compatible"} and not key:
            raise ValueError("Informe a API key do provedor.")
        profile = {
            "kind": kind,
            "base_url": settings.base_url,
            "api_key": key,
            "tls_insecure": tls_insecure,
        }

        def check_cancel():
            if cancelled is not None and cancelled.is_set():
                raise ModelError("Teste cancelado.")

        check_cancel()
        if on_stage:
            on_stage("catálogo")
        catalog = ModelCatalog(profile, transport=self.transport)
        models = catalog.list()
        check_cancel()
        if model_id:
            model = next((item for item in models if item["id"] == model_id), None)
            if model is None:
                raise ValueError("Modelo não está no catálogo consultado.")
            model = catalog.details(model)
            check_cancel()
            if model["tools"] is False:
                raise ValueError("A API informa que este modelo não suporta ferramentas/chat.")
            from codaro.llm.factory import create_provider

            create_provider(
                self.settings_for("teste", profile, model), transport=self.transport
            ).check_tool_calling(cancelled=cancelled, on_stage=on_stage)
        return models

    def models(self, name=None, *, refresh=False):
        name, profile = self.profile(name)
        if not refresh:
            return profile["models"]
        models = ModelCatalog(profile, transport=self.transport).list()

        def save(value):
            current = value["profiles"].get(name)
            if current != profile:
                raise ValueError("O perfil mudou durante a consulta; tente novamente.")
            current.update(models=models, updated_at=timestamp())

        self.mutate(save)
        return models

    def select(self, name, model_id, *, context_window=None):
        name, profile = self.profile(name)
        model = next((model for model in profile["models"] if model["id"] == model_id), None)
        if model is None:
            raise ValueError("Modelo não está no catálogo; atualize a lista do provedor.")
        model = ModelCatalog(profile, transport=self.transport).details(model)
        if model["tools"] is False:
            raise ValueError("A API informa que este modelo não suporta ferramentas/chat.")
        overrides = dict(profile.get("context_overrides", {}))
        if context_window is not None:
            overrides[model_id] = context_window
        candidate = {**profile, "model": model_id, "context_overrides": overrides}
        self.settings_for(name, candidate, model)

        def save(value):
            current = value["profiles"].get(name)
            if current != profile:
                raise ValueError("O perfil mudou durante a seleção; tente novamente.")
            current["models"] = [
                model if item["id"] == model_id else item for item in current["models"]
            ]
            current["model"] = model_id
            current["context_overrides"] = overrides
            value["active"] = name

        self.mutate(save)
        return self.settings_for(name, candidate, model)

    def settings_for(self, name, profile, model):
        override = profile.get("context_overrides", {}).get(model["id"])
        fallback = 4096 if profile["kind"] == "ollama" else 16_384
        window = min(override or model["context_window"] or fallback, 2_000_000)
        return Settings(
            provider_base_url(profile["kind"], profile["base_url"]),
            model["id"],
            profile["api_key"],
            tls_insecure=profile["tls_insecure"],
            context_window=window,
            max_output_tokens=None,
            provider_id=name,
            include_stream_usage=profile["kind"] == "openai",
            api_style=profile["kind"] if profile["kind"] in {"anthropic", "ollama"} else "openai",
            model_max_output_tokens=model["max_output_tokens"],
            context_source="configuração do usuário"
            if override
            else model["context_source"]
            if model["context_window"]
            else f"fallback: {fallback:,} tokens".replace(",", "."),
        )

    def active_settings(self, name=None):
        name, profile = self.profile(name)
        model = next((item for item in profile["models"] if item["id"] == profile["model"]), None)
        if model is None:
            raise ValueError("Selecione um modelo com codaro models use ID --provider PERFIL.")
        if profile["kind"] == "ollama":
            # Refresh persisted legacy fallbacks and changed Modelfile parameters.
            try:
                return self.select(name, model["id"])
            except ModelError:
                # Offline startup keeps the last valid profile available for recovery.
                pass
        return self.settings_for(name, profile, model)

    def remove(self, name):
        def delete(value):
            if name not in value["profiles"]:
                raise ValueError("Perfil não cadastrado.")
            del value["profiles"][name]
            if value["active"] == name:
                value["active"] = None

        self.mutate(delete)
