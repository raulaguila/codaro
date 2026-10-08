"""Bounded discovery of provider models and their metadata."""

from __future__ import annotations

import json
import re

import httpx

from codaro.llm.errors import (
    ModelError,
)

MAX_CATALOG_BYTES = 4_000_000


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
                        if len(raw) > MAX_CATALOG_BYTES:
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
        if not match:
            info = data.get("model_info", {})
            if isinstance(info, dict):
                architecture = info.get("general.architecture")
                primary = positive(info.get(f"{architecture}.context_length"))
                limits = (
                    [primary]
                    if primary
                    else [
                        positive(value)
                        for key, value in info.items()
                        if key.endswith(".context_length")
                    ]
                )
                # Vision encoders may also report a tiny context_length.
                limits = [value for value in limits if value and value >= 4096]
                if limits:
                    result.update(
                        context_window=min(limits),
                        context_source="API: model_info.context_length → options.num_ctx",
                    )
        # Native chat explicitly requests this window; the /v1 API cannot set num_ctx.
        return result
