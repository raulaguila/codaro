"""Anthropic Messages adapter using the agent's normalized tool protocol."""

from __future__ import annotations

import json
import threading

import httpx

from codaro.provider import (
    MAX_MESSAGE_CHARS,
    MAX_RESPONSE_BYTES,
    ContextLimitError,
    ModelError,
    OpenAICompatible,
    build_payload,
    capture_wire,
    check_cancelled,
    is_context_error,
    sse_events,
    validate_message,
)


class Anthropic(OpenAICompatible):
    @staticmethod
    def wire_payload(payload):
        system, messages = [], []
        for message in payload["messages"]:
            role, blocks = message["role"], []
            if role == "system":
                system.append(message["content"])
                continue
            if role == "tool":
                role = "user"
                blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                )
            else:
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls", []):
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": call["function"]["name"],
                            "input": json.loads(call["function"]["arguments"]),
                        }
                    )
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})
        result = {
            "model": payload["model"],
            "messages": messages,
            "max_tokens": payload["max_tokens"],
            "temperature": payload["temperature"],
        }
        if system:
            result["system"] = "\n".join(system)
        if payload.get("stream"):
            result["stream"] = True
        if payload.get("tools"):
            result["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"]["description"],
                    "input_schema": tool["function"]["parameters"],
                }
                for tool in payload["tools"]
            ]
            result["tool_choice"] = {"type": "auto"}
        return result

    @staticmethod
    def message(data):
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise ModelError("Resposta Anthropic inválida.")
        reason = data.get("stop_reason")
        capture_wire("finish_reason", reason)
        if reason not in {"end_turn", "tool_use", "stop_sequence"}:
            raise ModelError("A Anthropic não concluiu a resposta; confira o limite de saída.")
        content, calls = [], []
        for block in data["content"]:
            if not isinstance(block, dict):
                raise ModelError("Bloco Anthropic inválido.")
            if block.get("type") == "text":
                if not isinstance(block.get("text"), str):
                    raise ModelError("Texto Anthropic inválido.")
                content.append(block["text"])
            elif block.get("type") == "tool_use":
                if not isinstance(block.get("input"), dict):
                    raise ModelError("Argumentos Anthropic inválidos.")
                calls.append(
                    {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block["input"], ensure_ascii=False),
                        },
                    }
                )
        if reason == "tool_use" and not calls:
            raise ModelError("A Anthropic concluiu com tool_use sem informar ferramentas.")
        return validate_message(
            {"role": "assistant", "content": "".join(content) or None, "tool_calls": calls}
        )

    def _request(self, messages, tools, on_delta=None, cancelled=None, on_reasoning=None):
        payload = self.wire_payload(
            build_payload(
                self.settings.model,
                messages,
                tools,
                streaming=on_delta is not None,
                max_tokens=self.settings.max_output_tokens,
            )
        )
        headers = {"x-api-key": self.settings.api_key, "anthropic-version": "2023-06-01"}
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.settings.timeout, connect=10),
                verify=not self.settings.tls_insecure,
                transport=self.transport,
            ) as client:
                for attempt in range(3):
                    check_cancelled(cancelled)
                    from codaro.trace import current_flow

                    flow = current_flow.get()
                    if flow is not None and flow.turn is not None:
                        flow.turn["http_attempts"].append({"attempt": attempt + 1})
                    with client.stream(
                        "POST",
                        self.settings.base_url.rstrip("/") + "/messages",
                        headers=headers,
                        json=payload,
                    ) as response:
                        capture_wire("status_code", response.status_code)
                        if response.is_error:
                            raw = bytearray()
                            for part in response.iter_bytes():
                                check_cancelled(cancelled)
                                raw.extend(part[: max(0, 64000 - len(raw))])
                                if len(raw) >= 64000:
                                    break
                            body = raw.decode("utf-8", errors="replace")
                            capture_wire("error_body", body)
                            if response.status_code in {400, 413, 422} and is_context_error(body):
                                raise ContextLimitError(
                                    "A Anthropic rejeitou o contexto da tarefa."
                                )
                            if response.status_code in {429, 502, 503, 504, 529} and attempt < 2:
                                event = cancelled or threading.Event()
                                event.wait(0.25 * 2**attempt)
                                check_cancelled(cancelled)
                                continue
                            raise ModelError(
                                f"A Anthropic recusou a solicitação (HTTP {response.status_code})."
                            )
                        if on_delta is not None and "text/event-stream" in response.headers.get(
                            "content-type", ""
                        ):
                            return self.read_stream(response, on_delta, cancelled, on_reasoning)
                        raw = bytearray()
                        for part in response.iter_bytes():
                            check_cancelled(cancelled)
                            raw.extend(part)
                            if len(raw) > MAX_RESPONSE_BYTES:
                                raise ModelError("Resposta Anthropic excede 256 KB.")
                        capture_wire("response_body", raw.decode("utf-8", errors="replace"))
                        data = json.loads(raw)
                        self.usage(data.get("usage", {}))
                        message = self.message(data)
                        if on_reasoning:
                            for block in data["content"]:
                                if block.get("type") == "thinking" and isinstance(
                                    block.get("thinking"), str
                                ):
                                    on_reasoning(block["thinking"][:MAX_MESSAGE_CHARS])
                                    check_cancelled(cancelled)
                        if on_delta and message.get("content"):
                            on_delta(message["content"])
                        check_cancelled(cancelled)
                        return message
        except httpx.RequestError as exc:
            raise ModelError("Não foi possível conectar à Anthropic.") from exc
        except (KeyError, ValueError, TypeError, RecursionError) as exc:
            raise ModelError("Resposta incompatível com a API Messages da Anthropic.") from exc

    @staticmethod
    def usage(value):
        if not isinstance(value, dict):
            return
        total = sum(
            value.get(key, 0)
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        capture_wire(
            "usage", {"prompt_tokens": total, "completion_tokens": value.get("output_tokens", 0)}
        )

    def read_stream(self, response, on_delta, cancelled, on_reasoning):
        blocks, usage, closed = {}, {}, set()
        reason, finished, size, thinking_size = None, False, 0, 0
        for number, raw in enumerate(sse_events(response, cancelled)):
            if number >= 10000:
                raise ModelError("Stream Anthropic contém eventos demais.")
            capture_wire("sse", raw)
            event = json.loads(raw)
            if not isinstance(event, dict) or event.get("type") == "error":
                raise ModelError("A Anthropic interrompeu o stream.")
            kind = event.get("type")
            if finished:
                raise ModelError("Stream Anthropic continuou após o encerramento.")
            if kind == "message_start":
                usage.update(event["message"].get("usage", {}))
                self.usage(usage)
            elif kind == "content_block_start":
                index = event["index"]
                if type(index) is not int or not 0 <= index < 64 or index in blocks:
                    raise ModelError("Índice de bloco Anthropic inválido.")
                blocks[index] = dict(event["content_block"])
                if blocks[index].get("type") == "tool_use":
                    blocks[index]["partial_json"] = ""
            elif kind == "content_block_delta":
                if event["index"] in closed:
                    raise ModelError("Stream alterou um bloco Anthropic já concluído.")
                block, delta = blocks[event["index"]], event["delta"]
                if delta.get("type") == "text_delta":
                    fragment = delta["text"]
                    if not isinstance(fragment, str) or block.get("type") != "text":
                        raise ModelError("Fragmento Anthropic inválido.")
                    size += len(fragment)
                    if size > MAX_MESSAGE_CHARS:
                        raise ModelError("Resposta textual maior que o limite permitido.")
                    block["text"] = block.get("text", "") + fragment
                    on_delta(fragment)
                elif delta.get("type") == "input_json_delta":
                    fragment = delta["partial_json"]
                    if not isinstance(fragment, str) or block.get("type") != "tool_use":
                        raise ModelError("Fragmento de ferramenta Anthropic inválido.")
                    block["partial_json"] += fragment
                    if len(block["partial_json"]) > 8000:
                        raise ModelError("Argumentos de ferramenta excedem o limite permitido.")
                elif delta.get("type") == "thinking_delta" and on_reasoning:
                    fragment = delta.get("thinking")
                    if not isinstance(fragment, str):
                        raise ModelError("Raciocínio Anthropic inválido.")
                    visible = fragment[: max(0, MAX_MESSAGE_CHARS - thinking_size)]
                    thinking_size += len(visible)
                    if visible:
                        on_reasoning(visible)
            elif kind == "content_block_stop":
                block = blocks[event["index"]]
                if event["index"] in closed:
                    raise ModelError("Stream concluiu um bloco Anthropic duas vezes.")
                closed.add(event["index"])
                if block.get("type") == "tool_use" and block.get("partial_json"):
                    block["input"] = json.loads(block.pop("partial_json"))
            elif kind == "message_delta":
                reason = event["delta"].get("stop_reason", reason)
                usage.update(event.get("usage", {}))
                self.usage(usage)
            elif kind == "message_stop":
                finished = True
            check_cancelled(cancelled)
        if not finished or closed != set(blocks):
            raise ModelError("Conexão Anthropic interrompida antes de concluir a resposta.")
        return self.message(
            {"content": [blocks[index] for index in sorted(blocks)], "stop_reason": reason}
        )
