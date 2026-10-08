from __future__ import annotations

import codecs
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from codaro.runtime import redact_request, remaining_seconds
from codaro.trace import current_flow

MAX_RESPONSE_BYTES = 256_000
MAX_MESSAGE_CHARS = 16_000
MAX_TOOL_ARGUMENT_BYTES = 64_000
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
        flow.capture(kind, value)


class ModelError(RuntimeError):
    """Provider failure with a user-facing message that excludes remote error bodies."""


class OutputLimitError(ModelError):
    """Incomplete output must be regenerated, never executed as tool arguments."""


class OllamaMemoryError(ModelError):
    """Requested native window exceeds available server memory; retry a smaller window."""


class ContextCapacityError(ModelError):
    """The irreducible turn cannot fit after automatic recovery."""


class ContextLimitError(ModelError):
    """Recognized context rejection; retry only the model, never executed tools."""

    def __init__(self, message, *, context_window=None):
        super().__init__(message)
        self.context_window = context_window


def reported_context_window(body):
    """Extract only explicit token limits, never the requested token count."""
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        return None
    error = value.get("error") if isinstance(value, dict) else None
    if isinstance(error, dict):
        for field in ("max_context_length", "context_window", "context_length", "max_input_tokens"):
            limit = error.get(field)
            if type(limit) is int and 1024 <= limit <= 2_000_000:
                return limit
        error = error.get("message", "")
    if not isinstance(error, str):
        return None
    match = re.search(
        r"(?:maximum context length(?: is)?|context (?:window|length)(?: is| of)?|"
        r"maximum(?: number of)? (?:input )?tokens(?: is)?)\s*[:=]?\s*(\d[\d,]{0,12})\b",
        error,
        re.I,
    )
    if match:
        limit = int(match[1].replace(",", ""))
        if 1024 <= limit <= 2_000_000:
            return limit
    return None


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
            "prompt is too long",
            "too many input tokens",
            "context_length_exceeded",
        )
    )


class RequestCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    timeout: float = 90.0
    tls_insecure: bool = False
    context_window: int = 16_384
    max_output_tokens: int = 1400
    token_encoding: str | None = None
    provider_id: str = ""
    context_source: str = "configuração padrão/ambiente"
    api_style: str = "openai"
    model_max_output_tokens: int | None = None
    include_stream_usage: bool = False

    def __post_init__(self):
        if self.api_style not in {"openai", "anthropic", "ollama"}:
            raise ValueError("API do provedor inválida.")
        if len(self.api_key) > 16384:
            raise ValueError("Credencial excede o limite permitido.")
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
        if type(self.include_stream_usage) is not bool:
            raise ValueError("Stream usage deve ser booleano.")
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
        from codaro.providers import ProviderStore

        store, selected = ProviderStore(), os.getenv("CODARO_PROVIDER", "").strip()
        configured = None
        if selected or not any(
            os.getenv(name) for name in ("CODARO_BASE_URL", "CODARO_MODEL", "CODARO_API_KEY")
        ):
            if selected or store.load()["active"]:
                configured = store.active_settings(selected or None)
        if tls_insecure is None:
            raw = (
                os.getenv(
                    "CODARO_TLS_INSECURE", str(configured.tls_insecure) if configured else "false"
                )
                .strip()
                .lower()
            )
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
                else int(
                    os.getenv(
                        "CODARO_CONTEXT_WINDOW",
                        str(configured.context_window) if configured else "16384",
                    )
                )
            )
            output = int(
                os.getenv(
                    "CODARO_MAX_OUTPUT_TOKENS",
                    str(configured.max_output_tokens) if configured else "1400",
                )
            )
        except ValueError as exc:
            raise ValueError(
                "CODARO_CONTEXT_WINDOW e CODARO_MAX_OUTPUT_TOKENS: use inteiros."
            ) from exc
        if configured and configured.model_max_output_tokens:
            output = min(output, configured.model_max_output_tokens, 32768)
        return cls(
            base_url=configured.base_url
            if configured
            else os.getenv("CODARO_BASE_URL", "http://localhost:11434/v1").strip().rstrip("/"),
            model=configured.model
            if configured
            else os.getenv("CODARO_MODEL", "qwen2.5:7b").strip(),
            api_key=configured.api_key if configured else os.getenv("CODARO_API_KEY", "").strip(),
            timeout=timeout,
            tls_insecure=tls_insecure,
            context_window=window,
            max_output_tokens=output,
            token_encoding=os.getenv("CODARO_TOKEN_ENCODING", "").strip() or None,
            provider_id=configured.provider_id if configured else "",
            api_style=configured.api_style if configured else "openai",
            model_max_output_tokens=configured.model_max_output_tokens if configured else None,
            include_stream_usage=configured.include_stream_usage if configured else False,
            context_source=(
                "configuração do usuário"
                if context_window is not None or os.getenv("CODARO_CONTEXT_WINDOW")
                else configured.context_source
                if configured
                else "configuração padrão/ambiente"
            ),
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
            or len(arguments.encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES
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
    streaming_content_type = "text/event-stream"

    @property
    def chat_url(self):
        return f"{self.settings.base_url.rstrip('/')}/chat/completions"

    def wire_payload(self, payload):
        payload = dict(payload)
        if (
            payload.get("stream")
            and self.settings.api_style == "openai"
            and (
                self.settings.include_stream_usage
                or urlsplit(self.settings.base_url).hostname == "api.openai.com"
            )
            and getattr(self, "_stream_usage_supported", True)
        ):
            payload["stream_options"] = {"include_usage": True}
        return payload

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
        *,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict:
        return self._request(
            messages,
            tools,
            on_delta=on_delta or (lambda _: None),
            cancelled=cancelled,
            on_reasoning=on_reasoning,
        )

    def check_tool_calling(self, *, cancelled=None, on_stage=None):
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
        if on_stage:
            on_stage("chamada de ferramenta")
        message = self._request(messages, [probe], cancelled=cancelled)
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
        if cancelled is not None and cancelled.is_set():
            raise RequestCancelled("Teste cancelado.")
        if on_stage:
            on_stage("resposta final")
        answer = self._request(messages, None, on_delta=lambda delta: None, cancelled=cancelled)
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
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict:
        payload = build_payload(
            self.settings.model,
            messages,
            tools,
            streaming=on_delta is not None,
            max_tokens=self.settings.max_output_tokens,
        )
        payload = redact_request(self.wire_payload(payload))
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    min(self.settings.timeout, remaining_seconds() or self.settings.timeout),
                    connect=min(10, remaining_seconds() or 10),
                ),
                transport=self.transport,
                verify=not self.settings.tls_insecure,
            ) as client:
                negotiated_usage = False
                for attempt in range(4):
                    check_cancelled(cancelled)
                    flow = current_flow.get()
                    if flow is not None and flow.turn is not None:
                        flow.turn["http_attempts"].append({"attempt": attempt + 1})
                    capture_wire("http_request", payload)
                    with client.stream(
                        "POST",
                        self.chat_url,
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
                            if (
                                response.status_code in {400, 422}
                                and "stream_options" in payload
                                and (
                                    "stream_options" in raw_error.decode("utf-8", errors="replace")
                                    and any(
                                        word in raw_error.decode("utf-8", errors="replace").lower()
                                        for word in (
                                            "unsupported",
                                            "unknown",
                                            "unrecognized",
                                            "not permitted",
                                        )
                                    )
                                )
                            ):
                                payload.pop("stream_options")
                                self._stream_usage_supported = False
                                negotiated_usage = True
                                continue
                            if self.settings.api_style == "ollama" and any(
                                phrase in raw_error.decode("utf-8", errors="replace").lower()
                                for phrase in (
                                    "requires more system memory",
                                    "out of memory",
                                    "unable to allocate",
                                    "failed to allocate",
                                )
                            ):
                                raise OllamaMemoryError(
                                    "A janela solicitada excede a memória do Ollama."
                                )
                            context_status = response.status_code in {400, 413, 422} or (
                                self.settings.api_style == "ollama" and response.status_code == 500
                            )
                            if context_status and is_context_error(
                                raw_error.decode("utf-8", errors="replace")
                            ):
                                raise ContextLimitError(
                                    "O servidor rejeitou o contexto. Confira CODARO_CONTEXT_WINDOW "
                                    "e a janela realmente configurada no modelo.",
                                    context_window=reported_context_window(
                                        raw_error.decode("utf-8", errors="replace")
                                    ),
                                )
                        if response.status_code in {429, 502, 503, 504} and attempt < 2 + int(
                            negotiated_usage
                        ):
                            if cancelled is None:
                                time.sleep(0.25 * 2**attempt)
                            elif cancelled.wait(0.25 * 2**attempt):
                                check_cancelled(cancelled)
                            continue
                        response.raise_for_status()
                        if (
                            on_delta is not None
                            and self.streaming_content_type
                            in response.headers.get("content-type", "")
                        ):
                            return self._read_stream(response, on_delta, cancelled, on_reasoning)
                        message = self._read_json(response, cancelled, on_reasoning)
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
    def _read_json(
        response: httpx.Response,
        cancelled: threading.Event | None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict:
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
        check_cancelled(cancelled)
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
        if on_reasoning is not None:
            raw_message = choices[0]["message"]
            reasoning = raw_message.get("reasoning_content") or raw_message.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                on_reasoning(reasoning[:MAX_MESSAGE_CHARS])
                check_cancelled(cancelled)
        return message

    @staticmethod
    def _read_stream(
        response: httpx.Response,
        on_delta: Callable[[str], None],
        cancelled: threading.Event | None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict:
        content = ""
        calls: dict[int, dict] = {}
        pending = ""
        last_emit = 0.0
        finished = False
        event_count = 0
        reasoning_chars = 0
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
            if is_context_error(data):
                raise ContextLimitError(
                    "O servidor rejeitou o contexto. Confira CODARO_CONTEXT_WINDOW.",
                    context_window=reported_context_window(data),
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
            reasoning = delta.get("reasoning_content")
            if reasoning is None:
                reasoning = delta.get("reasoning")
            if finished and (delta.get("content") or delta.get("tool_calls") or reasoning):
                raise ModelError("O stream enviou conteúdo após concluir a resposta.")
            if reasoning is not None:
                if not isinstance(reasoning, str):
                    raise ValueError("invalid reasoning delta")
                # Reasoning is a separate, optional preview, never part of content/history.
                visible = reasoning[: max(0, MAX_MESSAGE_CHARS - reasoning_chars)]
                reasoning_chars += len(visible)
                if visible and on_reasoning is not None:
                    on_reasoning(visible)
                    check_cancelled(cancelled)
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
                        if (
                            len(call["function"]["arguments"].encode("utf-8"))
                            > MAX_TOOL_ARGUMENT_BYTES
                        ):
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
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise ModelError("Prazo da tarefa atingido; progresso salvo para retomada.")
    if cancelled is not None and cancelled.is_set():
        raise RequestCancelled("Investigação cancelada.")


def create_provider(settings, *, transport=None):
    if settings.api_style == "ollama":
        from codaro.ollama import Ollama

        return Ollama(settings, transport)
    if settings.api_style == "anthropic":
        from codaro.anthropic import Anthropic

        return Anthropic(settings, transport)
    return OpenAICompatible(settings, transport)


def check_finish_reason(reason: str | None):
    if reason == "length":
        raise OutputLimitError("O modelo atingiu o limite de saída.")
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
