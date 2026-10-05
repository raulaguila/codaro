from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE_BYTES = 256_000
MAX_MESSAGE_CHARS = 16_000


class ModelError(RuntimeError):
    """Provider failure with a user-facing message that excludes remote error bodies."""


@dataclass(frozen=True)
class Settings:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 90.0

    def __post_init__(self):
        try:
            parsed = urlsplit(self.base_url)
            valid_port = parsed.port is None or 0 < parsed.port < 65536
        except ValueError as exc:
            raise ValueError("CODARO_BASE_URL inválida.") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not valid_port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ord(c) <= 32 or ord(c) == 127 for c in self.base_url)
        ):
            raise ValueError("Use uma URL HTTP(S), sem credenciais, query ou fragmento.")
        if (
            not self.model.strip()
            or len(self.model) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in self.model)
        ):
            raise ValueError("CODARO_MODEL deve conter um nome de modelo válido.")
        if not 1 <= self.timeout <= 300:
            raise ValueError("CODARO_TIMEOUT deve ficar entre 1 e 300 segundos.")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.api_key):
            raise ValueError("Credencial contém caracteres inválidos.")

    @classmethod
    def from_env(cls) -> Settings:
        try:
            timeout = float(os.getenv("CODARO_TIMEOUT", "90"))
        except ValueError as exc:
            raise ValueError("CODARO_TIMEOUT deve ser um número.") from exc
        return cls(
            base_url=os.getenv("CODARO_BASE_URL", "http://localhost:11434/v1").strip().rstrip("/"),
            model=os.getenv("CODARO_MODEL", "qwen2.5:7b").strip(),
            api_key=os.getenv("CODARO_API_KEY", "").strip(),
            timeout=timeout,
        )


def validate_message(message: object) -> dict:
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise ModelError("Resposta incompatível: mensagem de assistente inválida.")
    content = message.get("content")
    if content is not None and (not isinstance(content, str) or len(content) > MAX_MESSAGE_CHARS):
        raise ModelError("Resposta textual inválida ou maior que o limite permitido.")
    calls = message.get("tool_calls")
    if calls is None:
        calls = []
    if not isinstance(calls, list) or len(calls) > 8:
        raise ModelError("Resposta contém uma lista inválida de ferramentas (máximo: 8).")
    ids = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ModelError("Chamada de ferramenta inválida.")
        identifier = call.get("id")
        function = call.get("function")
        if (
            not isinstance(identifier, str)
            or not identifier
            or len(identifier) > 200
            or identifier in ids
            or not isinstance(function, dict)
        ):
            raise ModelError("Identificação de ferramenta inválida ou duplicada.")
        name, arguments = function.get("name"), function.get("arguments")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 80
            or not isinstance(arguments, str)
            or len(arguments) > 8000
        ):
            raise ModelError("Nome ou argumentos da ferramenta inválidos.")
        ids.add(identifier)
    if not calls and not (content and content.strip()):
        raise ModelError("O modelo retornou uma resposta vazia.")
    result = {"role": "assistant", "content": content}
    if calls:
        result["tool_calls"] = calls
    return result


class OpenAICompatible:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1400,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.settings.timeout, connect=10), transport=self.transport
            ) as client:
                for attempt in range(3):
                    with client.stream(
                        "POST",
                        f"{self.settings.base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as response:
                        if response.status_code in {429, 502, 503, 504} and attempt < 2:
                            time.sleep(0.25 * 2**attempt)
                            continue
                        response.raise_for_status()
                        raw = bytearray()
                        for part in response.iter_bytes():
                            raw.extend(part)
                            if len(raw) > MAX_RESPONSE_BYTES:
                                raise ModelError("Resposta da API excede o limite de 256 KB.")
                        data = json.loads(raw)
                        choices = data["choices"]
                        if (
                            not isinstance(choices, list)
                            or not choices
                            or not isinstance(choices[0], dict)
                        ):
                            raise ValueError("invalid choices")
                        choice = choices[0]
                        if choice.get("finish_reason") == "length":
                            raise ModelError(
                                "O modelo atingiu o limite de saída. Faça uma pergunta menor."
                            )
                        return validate_message(choice["message"])
        except httpx.HTTPStatusError as exc:
            raise ModelError(
                f"API retornou HTTP {exc.response.status_code}. "
                "Confira o modelo, a URL e a credencial."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ModelError(
                "Tempo de resposta do modelo esgotado. Confira CODARO_TIMEOUT."
            ) from exc
        except httpx.RequestError as exc:
            raise ModelError(
                "Não foi possível conectar ao modelo. Confira CODARO_BASE_URL "
                "e se o servidor está ativo."
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError, RecursionError) as exc:
            raise ModelError("Resposta incompatível com a API de chat completions.") from exc
        raise ModelError("Não foi possível obter uma resposta do modelo.")
