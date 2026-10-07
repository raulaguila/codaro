"""User-level BYOK profiles and bounded discovery of provider model metadata."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx

from codaro.provider import ModelError, Settings
from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write, timestamp

PRESETS = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "openai-compatible": "",
    "custom": "",
}
MAX_CONFIG_BYTES = 4_000_000


def positive(value):
    return value if type(value) is int and 1 <= value <= 20_000_000 else None


def text(value, limit=200):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError("Metadados de modelo inválidos.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Metadados de modelo inválidos.")
    return value


class ModelCatalog:
    def __init__(self, profile, *, transport=None):
        self.profile, self.transport = profile, transport

    def request(self, method, url, **kwargs):
        headers = {}
        key = self.profile["api_key"]
        if key:
            header = {"gemini": "x-goog-api-key", "anthropic": "x-api-key"}.get(
                self.profile["kind"], "Authorization"
            )
            headers[header] = f"Bearer {key}" if header == "Authorization" else key
        if self.profile["kind"] == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        try:
            with httpx.Client(
                timeout=15,
                verify=not self.profile["tls_insecure"],
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                with client.stream(method, url, headers=headers, **kwargs) as response:
                    if response.status_code >= 300:
                        raise ModelError(
                            f"Catálogo indisponível (HTTP {response.status_code}). "
                            "Confira o provedor, endereço e credencial."
                        )
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_CONFIG_BYTES:
                            raise ModelError("Catálogo excede o limite de 4 MB.")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("invalid catalog")
            return data
        except httpx.RequestError as exc:
            raise ModelError("Não foi possível consultar o catálogo do provedor.") from exc
        except (ValueError, RecursionError) as exc:
            raise ModelError("O provedor retornou um catálogo inválido.") from exc

    def list(self):
        kind, base = self.profile["kind"], self.profile["base_url"]
        if kind == "ollama":
            data = self.request("GET", base.removesuffix("/v1") + "/api/tags")
            rows = data.get("models")
        else:
            url = base.removesuffix("/openai") + "/models" if kind == "gemini" else base + "/models"
            rows, params, seen = [], {}, set()
            for _ in range(10):
                data = self.request("GET", url, params=params)
                page = data.get("models" if kind == "gemini" else "data")
                if not isinstance(page, list) or len(rows) + len(page) > 2000:
                    raise ModelError("Catálogo inválido ou maior que 2.000 modelos.")
                rows.extend(page)
                cursor = (
                    data.get("nextPageToken")
                    if kind == "gemini"
                    else (data.get("last_id") if data.get("has_more") else None)
                )
                if not cursor:
                    break
                if not isinstance(cursor, str) or len(cursor) > 2000 or cursor in seen:
                    raise ModelError("Paginação inválida no catálogo.")
                seen.add(cursor)
                params = {"pageToken" if kind == "gemini" else "after": cursor}
            else:
                raise ModelError("Catálogo excede dez páginas.")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
            raise ModelError("O provedor não informou modelos disponíveis.")
        models = {}
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("invalid model")
                identifier = row.get("id", row.get("name", row.get("model")))
                if kind == "gemini" and isinstance(identifier, str):
                    identifier = identifier.removeprefix("models/")
                identifier = text(identifier)
                context, source = None, "não informado pela API"
                for field in (
                    "context_length",
                    "context_window",
                    "max_context_length",
                    "max_model_len",
                    "max_input_tokens",
                    "inputTokenLimit",
                ):
                    if positive(row.get(field)):
                        context, source = row[field], "API: " + field
                        break
                top = row.get("top_provider")
                top = top if isinstance(top, dict) else {}
                if positive(top.get("context_length")):
                    context = (
                        min(context, top["context_length"]) if context else top["context_length"]
                    )
                    source = "API: top_provider.context_length"
                output = (
                    positive(row.get("outputTokenLimit"))
                    or positive(row.get("max_output_tokens"))
                    or positive(row.get("max_tokens"))
                    or positive(top.get("max_completion_tokens"))
                )
                parameters = row.get("supported_parameters")
                capabilities = row.get("capabilities")
                tools = "tools" in parameters if isinstance(parameters, list) else None
                if (
                    isinstance(capabilities, dict)
                    and type(capabilities.get("function_calling")) is bool
                ):
                    tools = capabilities["function_calling"]
                methods = row.get("supportedGenerationMethods")
                if (
                    kind == "gemini"
                    and isinstance(methods, list)
                    and "generateContent" not in methods
                ):
                    tools = False
                models[identifier] = {
                    "id": identifier,
                    "name": text(
                        row.get("displayName")
                        or row.get("display_name")
                        or row.get("name")
                        or identifier
                    ),
                    "context_window": context,
                    "context_source": source,
                    "max_output_tokens": output,
                    "tools": tools,
                    "owned_by": text(row.get("owned_by") or kind),
                }
            if self.profile["api_key"] and self.profile["api_key"] in json.dumps(
                list(models.values())
            ):
                raise ValueError("credential in metadata")
        except ValueError as exc:
            raise ModelError("O provedor retornou metadados de modelo inválidos.") from exc
        return sorted(models.values(), key=lambda model: model["id"].casefold())

    def details(self, model):
        if self.profile["kind"] != "ollama":
            current = next((item for item in self.list() if item["id"] == model["id"]), None)
            if current is None:
                raise ModelError("O modelo não está mais disponível no catálogo da API.")
            return current
        base = self.profile["base_url"].removesuffix("/v1")
        data = self.request("POST", base + "/api/show", json={"model": model["id"]})
        result = dict(model)
        parameters = data.get("parameters", "")
        match = (
            re.search(r"(?m)^num_ctx\s+(\d+)\s*$", parameters)
            if isinstance(parameters, str)
            else None
        )
        if match and positive(int(match[1])):
            result.update(context_window=int(match[1]), context_source="API: parameters.num_ctx")
        capabilities = data.get("capabilities")
        if isinstance(capabilities, list):
            result["tools"] = "tools" in capabilities
        # Architectural context_length is not the running server's num_ctx.
        return result


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
            (base_url or PRESETS[kind]).rstrip("/"), "unselected", key, tls_insecure=tls_insecure
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
        window = min(override or model["context_window"] or 16_384, 2_000_000)
        output = min(1400, model["max_output_tokens"] or 1400, window - 513)
        return Settings(
            profile["base_url"],
            model["id"],
            profile["api_key"],
            tls_insecure=profile["tls_insecure"],
            context_window=window,
            max_output_tokens=output,
            provider_id=name,
            api_style="anthropic" if profile["kind"] == "anthropic" else "openai",
            model_max_output_tokens=model["max_output_tokens"],
            context_source="configuração do usuário"
            if override
            else model["context_source"]
            if model["context_window"]
            else "fallback: 16.384 tokens",
        )

    def active_settings(self, name=None):
        name, profile = self.profile(name)
        model = next((item for item in profile["models"] if item["id"] == profile["model"]), None)
        if model is None:
            raise ValueError("Selecione um modelo com codaro models use ID --provider PERFIL.")
        return self.settings_for(name, profile, model)

    def remove(self, name):
        def delete(value):
            if name not in value["profiles"]:
                raise ValueError("Perfil não cadastrado.")
            del value["profiles"][name]
            if value["active"] == name:
                value["active"] = None

        self.mutate(delete)
