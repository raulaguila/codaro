from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from codaro.llm.config import Settings
from codaro.llm.errors import (
    ContextLimitError,
    ModelError,
    OllamaMemoryError,
    RequestCancelled,
    is_context_error,
    reported_context_window,
)
from codaro.llm.protocol import (
    MAX_MESSAGE_CHARS,
    MAX_RESPONSE_BYTES,
    MAX_TOOL_ARGUMENT_BYTES,
    build_payload,
    capture_wire,
    check_finish_reason,
    validate_message,
)
from codaro.llm.streaming import check_cancelled, merge_fragment, sse_events
from codaro.runtime import redact_request, remaining_seconds
from codaro.trace import current_flow


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
        raw_message = choices[0].get("message", {})
        if not isinstance(raw_message, dict):
            raise ModelError("Resposta sem mensagem válida do modelo.")
        check_finish_reason(
            choices[0].get("finish_reason"),
            partial_text=raw_message.get("content", ""),
            has_tool_calls=bool(raw_message.get("tool_calls") or raw_message.get("function_call")),
        )
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
                if reason == "length" and pending:
                    on_delta(pending)
                    pending = ""
                    check_cancelled(cancelled)
                check_finish_reason(reason, partial_text=content, has_tool_calls=bool(calls))
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
