"""Native Ollama chat: request the measured window instead of relying on /v1 defaults."""

from __future__ import annotations

import copy
import json
import uuid

from codaro.provider import (
    MAX_MESSAGE_CHARS,
    MAX_RESPONSE_BYTES,
    MAX_STREAM_BYTES,
    ContextLimitError,
    ModelError,
    OllamaMemoryError,
    OpenAICompatible,
    capture_wire,
    check_cancelled,
    check_finish_reason,
    is_context_error,
    reported_context_window,
    validate_message,
)


class Ollama(OpenAICompatible):
    streaming_content_type = "application/x-ndjson"

    @property
    def chat_url(self):
        return self.settings.base_url.rstrip("/").removesuffix("/v1") + "/api/chat"

    def wire_payload(self, payload):
        messages = copy.deepcopy(payload["messages"])
        names = {}
        for message in messages:
            for call in message.get("tool_calls", []):
                names[call["id"]] = call["function"]["name"]
                arguments = call["function"]["arguments"]
                if isinstance(arguments, str):
                    call["function"]["arguments"] = json.loads(arguments)
            if message.get("role") == "tool":
                message["tool_name"] = names.get(
                    message.pop("tool_call_id", ""), message.get("name", "")
                )
        result = {
            "model": payload["model"],
            "messages": messages,
            "stream": payload.get("stream", False),
            "options": {
                "num_ctx": self.settings.context_window,
                "temperature": payload.get("temperature", 0.1),
            },
        }
        if payload.get("max_tokens") is not None:
            result["options"]["num_predict"] = payload["max_tokens"]
        if payload.get("tools"):
            result["tools"] = payload["tools"]
        return result

    @staticmethod
    def _message(data):
        message = dict(data["message"])
        message.setdefault("content", "")
        calls = []
        for call in message.get("tool_calls", []):
            function = call["function"]
            arguments = function["arguments"]
            calls.append(
                {
                    "id": "ollama_" + uuid.uuid4().hex,
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments, ensure_ascii=False),
                    },
                }
            )
        if calls:
            message["tool_calls"] = calls
        return validate_message(message)

    @staticmethod
    def _completion(data, *, partial_text="", has_tool_calls=False):
        if "error" in data:
            body = json.dumps(data)
            if any(
                phrase in body.lower()
                for phrase in (
                    "out of memory",
                    "requires more system memory",
                    "unable to allocate",
                    "failed to allocate",
                )
            ):
                raise OllamaMemoryError("A janela solicitada excede a memória do Ollama.")
            if is_context_error(body):
                raise ContextLimitError(
                    "Contexto rejeitado pelo Ollama.", context_window=reported_context_window(body)
                )
            raise ModelError("O Ollama interrompeu a geração; confira o fluxo local de debug.")
        if data.get("done"):
            capture_wire("finish_reason", data.get("done_reason"))
            capture_wire(
                "usage",
                {
                    "prompt_tokens": data.get("prompt_eval_count"),
                    "completion_tokens": data.get("eval_count"),
                },
            )
            # Preserve the terminal reason and usage even when validation raises.
            check_finish_reason(
                data.get("done_reason"), partial_text=partial_text, has_tool_calls=has_tool_calls
            )

    def _read_json(self, response, cancelled, on_reasoning=None):
        raw = bytearray()
        for part in response.iter_bytes():
            check_cancelled(cancelled)
            raw.extend(part)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ModelError("Resposta da API excede o limite de 256 KB.")
        capture_wire("response_body", raw.decode("utf-8", errors="replace"))
        data = json.loads(raw)
        self._completion(
            data,
            partial_text=data.get("message", {}).get("content", ""),
            has_tool_calls=bool(data.get("message", {}).get("tool_calls")),
        )
        if data.get("done") is not True:
            raise ModelError("O Ollama não confirmou a conclusão da resposta.")
        reasoning = data.get("message", {}).get("thinking")
        if on_reasoning and isinstance(reasoning, str) and reasoning:
            on_reasoning(reasoning[:MAX_MESSAGE_CHARS])
            check_cancelled(cancelled)
        return self._message(data)

    def _read_stream(self, response, on_delta, cancelled, on_reasoning=None):
        pending = bytearray()
        total = 0
        content = ""
        calls = []
        reasoning_chars = 0
        finished = False

        def consume(line):
            nonlocal content, finished, reasoning_chars
            check_cancelled(cancelled)
            data = json.loads(line)
            capture_wire("ndjson", data)
            if "error" in data:
                self._completion(data)
            message = data.get("message", {})
            fragment = message.get("content", "")
            if not isinstance(fragment, str):
                raise ValueError("invalid content")
            content += fragment
            if len(content) > MAX_MESSAGE_CHARS:
                raise ModelError("Resposta do modelo excede o limite permitido.")
            if fragment:
                on_delta(fragment)
                check_cancelled(cancelled)
            reasoning = message.get("thinking", "")
            if on_reasoning and isinstance(reasoning, str) and reasoning:
                remaining = MAX_MESSAGE_CHARS - reasoning_chars
                if remaining > 0:
                    on_reasoning(reasoning[:remaining])
                    reasoning_chars += min(len(reasoning), remaining)
                    check_cancelled(cancelled)
            calls.extend(message.get("tool_calls", []))
            if len(calls) > 8:
                raise ModelError("Lote de ferramentas excede o limite permitido.")
            self._completion(data, partial_text=content, has_tool_calls=bool(calls))
            finished = data.get("done") is True

        for part in response.iter_bytes():
            check_cancelled(cancelled)
            total += len(part)
            if total > MAX_STREAM_BYTES:
                raise ModelError("Stream excede o limite permitido.")
            pending.extend(part)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                if line.strip():
                    consume(line)
                if finished:
                    break
            if finished:
                break
        if pending.strip() and not finished:
            consume(pending)
        if not finished:
            raise ModelError("Stream do Ollama interrompido antes da conclusão.")
        return self._message(
            {"message": {"role": "assistant", "content": content, "tool_calls": calls}}
        )
