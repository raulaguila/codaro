from __future__ import annotations

import codecs
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from codaro.trace import current_flow

MAX_RESPONSE_BYTES = 256_000
MAX_MESSAGE_CHARS = 16_000
MAX_STREAM_BYTES = 2_000_000


def build_payload(
    model: str, messages: list[dict], tools: list[dict] | None, *, streaming=False, max_tokens=1400
):
    """One wire format for requests, context accounting and debug dumps."""
    payload = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens}
    if tools:
        payload.update(tools=tools, tool_choice="auto")
    if streaming:
        payload["stream"] = True
    return payload


def capture_wire(kind: str, value):
    flow = current_flow.get()
    if flow is not None and flow.turn is not None and flow.turn["http_attempts"]:
        attempt = flow.turn["http_attempts"][-1]
        if kind == "sse":
            attempt.setdefault("sse_events", []).append(value)
        else:
            attempt[kind] = value


class ModelError(RuntimeError):
    """Provider failure with a user-facing message that excludes remote error bodies."""


class ContextLimitError(ModelError):
    """Recognized context rejection; retry only the model, never executed tools."""


def is_context_error(body: str) -> bool:
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return False
    error = value.get("error") if isinstance(value, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        if code in ("context_length_exceeded", "context_window_exceeded"):
            return True
        message = error.get("message", "")
    else:
        message = error if isinstance(error, str) else ""
    if not isinstance(message, str):
        return False
    message = message.lower()
    return any(
        phrase in message
        for phrase in (
            "maximum context length",
            "context length exceeded",
            "context window exceeded",
            "exceeds the context",
            "exceed the context",
            "exceeds context",
            "input length exceeds",
            "too many tokens",
            "context_length_exceeded",
        )
    )


class RequestCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    base_url: str
    model: str
    api_key: str = ""
    timeout: float = 90.0
    tls_insecure: bool = False
    context_window: int = 16_384
    max_output_tokens: int = 1400
    token_encoding: str | None = None

    def __post_init__(self):
        if (
            type(self.context_window) is not int
            or not 4096 <= self.context_window <= 2_000_000
            or type(self.max_output_tokens) is not int
            or not 1 <= self.max_output_tokens <= 32_768
            or self.max_output_tokens + 512 >= self.context_window
        ):
            raise ValueError("Janela de contexto/limite de saída inválidos; reserve 512 tokens.")
        if self.token_encoding not in (None, "cl100k_base", "o200k_base"):
            raise ValueError("CODARO_TOKEN_ENCODING deve ser cl100k_base ou o200k_base.")
        if type(self.tls_insecure) is not bool:
            raise ValueError("TLS insecure deve ser booleano.")
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
    def from_env(
        cls, *, tls_insecure: bool | None = None, context_window: int | None = None
    ) -> Settings:
        if tls_insecure is None:
            raw = os.getenv("CODARO_TLS_INSECURE", "false").strip().lower()
            values = {
                "1": True,
                "true": True,
                "yes": True,
                "on": True,
                "0": False,
                "false": False,
                "no": False,
                "off": False,
            }
            if raw not in values:
                raise ValueError("CODARO_TLS_INSECURE deve ser true/false ou 1/0.")
            tls_insecure = values[raw]
        try:
            timeout = float(os.getenv("CODARO_TIMEOUT", "90"))
        except ValueError as exc:
            raise ValueError("CODARO_TIMEOUT deve ser um número.") from exc
        try:
            window = (
                context_window
                if context_window is not None
                else int(os.getenv("CODARO_CONTEXT_WINDOW", "16384"))
            )
            output = int(os.getenv("CODARO_MAX_OUTPUT_TOKENS", "1400"))
        except ValueError as exc:
            raise ValueError(
                "CODARO_CONTEXT_WINDOW e CODARO_MAX_OUTPUT_TOKENS: use inteiros."
            ) from exc
        return cls(
            base_url=os.getenv("CODARO_BASE_URL", "http://localhost:11434/v1").strip().rstrip("/"),
            model=os.getenv("CODARO_MODEL", "qwen2.5:7b").strip(),
            api_key=os.getenv("CODARO_API_KEY", "").strip(),
            timeout=timeout,
            tls_insecure=tls_insecure,
            context_window=window,
            max_output_tokens=output,
            token_encoding=os.getenv("CODARO_TOKEN_ENCODING", "").strip() or None,
        )


def validate_message(message: object) -> dict:
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise ModelError("Resposta incompatível: mensagem de assistente inválida.")
    if message.get("function_call") is not None:
        raise ModelError(
            "O servidor retornou function_call legado; configure o protocolo tool_calls."
        )
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
        return self._request(messages, tools)

    def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta: Callable[[str], None] | None = None,
        cancelled: threading.Event | None = None,
    ) -> dict:
        return self._request(
            messages, tools, on_delta=on_delta or (lambda _: None), cancelled=cancelled
        )

    def check_tool_calling(self):
        probe = {
            "type": "function",
            "function": {
                "name": "codaro_probe",
                "description": "Confirma o protocolo de ferramentas.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        }
        messages = [
            {
                "role": "user",
                "content": (
                    "Chame codaro_probe sem argumentos via tool_calls. Depois de receber "
                    "o resultado, responda somente com o valor de probe_result."
                ),
            }
        ]
        message = self._request(messages, [probe])
        calls = message.get("tool_calls") or []
        try:
            valid = (
                len(calls) == 1
                and calls[0]["function"]["name"] == "codaro_probe"
                and json.loads(calls[0]["function"]["arguments"]) == {}
            )
        except (ValueError, KeyError, TypeError, RecursionError):
            valid = False
        if not valid:
            raise ModelError(
                "O modelo/servidor não retornou tool_calls válidos no diagnóstico. "
                "Escolha um modelo com ferramentas e confira o template do servidor."
            )
        marker = "codaro_probe_ok_" + uuid.uuid4().hex
        messages.extend(
            [
                message,
                {
                    "role": "tool",
                    "name": "codaro_probe",
                    "tool_call_id": calls[0]["id"],
                    "content": json.dumps({"probe_result": marker}),
                },
            ]
        )
        answer = self.stream(messages)
        if answer.get("tool_calls") or marker not in (answer.get("content") or ""):
            raise ModelError(
                "O modelo chamou a ferramenta, mas não concluiu o ciclo com o resultado. "
                "Confira o suporte a role: tool e o template do servidor."
            )

    def _request(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        on_delta: Callable[[str], None] | None = None,
        cancelled: threading.Event | None = None,
    ) -> dict:
        payload = build_payload(
            self.settings.model,
            messages,
            tools,
            streaming=on_delta is not None,
            max_tokens=self.settings.max_output_tokens,
        )
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.settings.timeout, connect=10),
                transport=self.transport,
                verify=not self.settings.tls_insecure,
            ) as client:
                for attempt in range(3):
                    check_cancelled(cancelled)
                    flow = current_flow.get()
                    if flow is not None and flow.turn is not None:
                        flow.turn["http_attempts"].append({"attempt": attempt + 1})
                    with client.stream(
                        "POST",
                        f"{self.settings.base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as response:
                        capture_wire("status_code", response.status_code)
                        check_cancelled(cancelled)
                        if response.is_error:
                            # Keep a bounded body for local diagnosis; never expose it in the UI.
                            raw_error = bytearray()
                            for part in response.iter_bytes():
                                check_cancelled(cancelled)
                                raw_error.extend(part[: max(0, 64_000 - len(raw_error))])
                                if len(raw_error) >= 64_000:
                                    break
                            capture_wire("error_body", raw_error.decode("utf-8", errors="replace"))
                            if response.status_code in {400, 413, 422} and is_context_error(
                                raw_error.decode("utf-8", errors="replace")
                            ):
                                raise ContextLimitError(
                                    "O servidor rejeitou o contexto. Confira CODARO_CONTEXT_WINDOW "
                                    "e a janela realmente configurada no modelo."
                                )
                        if response.status_code in {429, 502, 503, 504} and attempt < 2:
                            if cancelled is None:
                                time.sleep(0.25 * 2**attempt)
                            elif cancelled.wait(0.25 * 2**attempt):
                                check_cancelled(cancelled)
                            continue
                        response.raise_for_status()
                        if on_delta is not None and "text/event-stream" in response.headers.get(
                            "content-type", ""
                        ):
                            return self._read_stream(response, on_delta, cancelled)
                        message = self._read_json(response, cancelled)
                        if on_delta is not None and message.get("content"):
                            on_delta(message["content"])
                            check_cancelled(cancelled)
                        return message
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

    @staticmethod
    def _read_json(response: httpx.Response, cancelled: threading.Event | None) -> dict:
        raw = bytearray()
        try:
            for part in response.iter_bytes():
                check_cancelled(cancelled)
                raw.extend(part)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ModelError("Resposta da API excede o limite de 256 KB.")
        finally:
            capture_wire(
                "response_body", raw[:MAX_RESPONSE_BYTES].decode("utf-8", errors="replace")
            )
        data = json.loads(raw)
        choices = data["choices"]
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("invalid choices")
        capture_wire("finish_reason", choices[0].get("finish_reason"))
        capture_wire("usage", data.get("usage"))
        check_finish_reason(choices[0].get("finish_reason"))
        message = validate_message(choices[0]["message"])
        if choices[0].get("finish_reason") == "tool_calls" and not message.get("tool_calls"):
            raise ModelError(
                "O servidor finalizou com tool_calls sem enviar chamadas de ferramentas."
            )
        return message

    @staticmethod
    def _read_stream(
        response: httpx.Response, on_delta: Callable[[str], None], cancelled: threading.Event | None
    ) -> dict:
        content = ""
        calls: dict[int, dict] = {}
        pending = ""
        last_emit = 0.0
        finished = False
        event_count = 0
        for data in sse_events(response, cancelled):
            capture_wire("sse", data)
            check_cancelled(cancelled)
            event_count += 1
            if event_count > 10_000:
                raise ModelError("Stream contém eventos demais.")
            if data == "[DONE]":
                finished = True
                break
            event = json.loads(data)
            if not content and not calls and is_context_error(data):
                raise ContextLimitError(
                    "O servidor rejeitou o contexto. Confira CODARO_CONTEXT_WINDOW."
                )
            if not isinstance(event, dict) or "error" in event:
                raise ModelError("O servidor interrompeu o stream com uma resposta inválida.")
            if "usage" in event:
                capture_wire("usage", event["usage"])
            choices = event.get("choices")
            if choices == []:
                continue  # Some servers send a final usage-only event.
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("invalid stream choices")
            choice = choices[0]
            if choice.get("index", 0) != 0:
                raise ModelError("Stream retornou uma escolha inesperada.")
            delta = choice.get("delta")
            if not isinstance(delta, dict) or delta.get("role", "assistant") != "assistant":
                raise ValueError("invalid delta")
            if finished and (delta.get("content") or delta.get("tool_calls")):
                raise ModelError("O stream enviou conteúdo após concluir a resposta.")
            fragment = delta.get("content")
            if fragment is not None:
                if not isinstance(fragment, str):
                    raise ValueError("invalid content delta")
                content += fragment
                pending += fragment
                if len(content) > MAX_MESSAGE_CHARS:
                    raise ModelError("Resposta textual maior que o limite permitido.")
                if pending and (time.monotonic() - last_emit >= 0.04 or len(pending) >= 256):
                    on_delta(pending)
                    pending = ""
                    last_emit = time.monotonic()
            fragments = delta.get("tool_calls")
            if fragments is not None:
                if not isinstance(fragments, list) or len(fragments) > 8:
                    raise ValueError("invalid tool deltas")
                for item in fragments:
                    if not isinstance(item, dict):
                        raise ValueError("invalid tool delta")
                    index = item.get("index")
                    if type(index) is not int or not 0 <= index < 8:
                        raise ValueError("invalid tool index")
                    call = calls.setdefault(
                        index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if item.get("type", "function") != "function":
                        raise ValueError("invalid tool type")
                    if "id" in item:
                        call["id"] = merge_fragment(call["id"], item["id"], 200)
                    function = item.get("function", {})
                    if not isinstance(function, dict):
                        raise ValueError("invalid function delta")
                    if "name" in function:
                        call["function"]["name"] = merge_fragment(
                            call["function"]["name"], function["name"], 80
                        )
                    if "arguments" in function:
                        arguments = function["arguments"]
                        if not isinstance(arguments, str):
                            raise ValueError("invalid arguments delta")
                        call["function"]["arguments"] += arguments
                        if len(call["function"]["arguments"]) > 8000:
                            raise ModelError("Argumentos de ferramenta excedem o limite permitido.")
            reason = choice.get("finish_reason")
            if reason is not None:
                capture_wire("finish_reason", reason)
                check_finish_reason(reason)
                if reason == "tool_calls" and not calls:
                    raise ModelError(
                        "O servidor finalizou com tool_calls sem enviar chamadas de ferramentas."
                    )
                finished = True
        if not finished:
            raise ModelError("Conexão interrompida antes de concluir a resposta.")
        check_cancelled(cancelled)
        message = {"role": "assistant", "content": content or None}
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        validated = validate_message(message)
        if pending:
            on_delta(pending)
            check_cancelled(cancelled)
        return validated


def check_cancelled(cancelled: threading.Event | None):
    if cancelled is not None and cancelled.is_set():
        raise RequestCancelled("Investigação cancelada.")


def check_finish_reason(reason: str | None):
    if reason == "length":
        raise ModelError("O modelo atingiu o limite de saída. Faça uma pergunta menor.")
    if reason == "content_filter":
        raise ModelError("O provedor interrompeu a geração da resposta.")
    if reason == "function_call":
        raise ModelError(
            "O servidor retornou function_call legado; configure o protocolo tool_calls."
        )
    if reason not in {None, "stop", "tool_calls"}:
        raise ModelError("Motivo de conclusão incompatível com a API.")


def merge_fragment(current: str, fragment: object, limit: int) -> str:
    if not isinstance(fragment, str):
        raise ValueError("invalid string fragment")
    result = (
        current
        if fragment == current
        else (fragment if fragment.startswith(current) else current + fragment)
    )
    if len(result) > limit:
        raise ModelError("Fragmento de ferramenta excede o limite permitido.")
    return result


def sse_events(response: httpx.Response, cancelled: threading.Event | None) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer = ""
    fields: list[str] = []
    total = 0
    for raw in response.iter_bytes():
        check_cancelled(cancelled)
        total += len(raw)
        if total > MAX_STREAM_BYTES:
            raise ModelError("Stream excede o limite de 2 MB.")
        buffer += decoder.decode(raw)
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.removesuffix("\r")
            if not line:
                if fields:
                    yield "\n".join(fields)
                    fields = []
            elif line.startswith("data:"):
                fields.append(line[5:].removeprefix(" "))
    buffer += decoder.decode(b"", final=True)
    if buffer.startswith("data:"):
        fields.append(buffer[5:].removeprefix(" ").removesuffix("\r"))
    if fields:
        yield "\n".join(fields)
