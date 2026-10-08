from __future__ import annotations

from codaro.llm.errors import EmptyResponseError, ModelError, OutputLimitError
from codaro.trace import current_flow

MAX_RESPONSE_BYTES = 256_000

MAX_MESSAGE_CHARS = 16_000

MAX_TOOL_ARGUMENT_BYTES = 64_000

MAX_STREAM_BYTES = 2_000_000


def build_payload(
    model: str, messages: list[dict], tools: list[dict] | None, *, streaming=False, max_tokens=None
):
    """One wire format for requests, context accounting and debug dumps."""
    payload = {"model": model, "messages": messages, "temperature": 0.1}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools:
        payload.update(tools=tools, tool_choice="auto")
    if streaming:
        payload["stream"] = True
    return payload


def capture_wire(kind: str, value):
    flow = current_flow.get()
    if flow is not None and flow.turn is not None and flow.turn["http_attempts"]:
        flow.capture(kind, value)


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
        raise EmptyResponseError("O modelo retornou uma resposta vazia.")
    result = {"role": "assistant", "content": content}
    if calls:
        result["tool_calls"] = calls
    return result


def check_finish_reason(reason: str | None, *, partial_text="", has_tool_calls=False):
    if reason == "length":
        raise OutputLimitError(
            "O modelo atingiu o limite de saída.",
            partial_text=partial_text,
            has_tool_calls=has_tool_calls,
        )
    if reason == "content_filter":
        raise ModelError("O provedor interrompeu a geração da resposta.")
    if reason == "function_call":
        raise ModelError(
            "O servidor retornou function_call legado; configure o protocolo tool_calls."
        )
    if reason not in {None, "stop", "tool_calls"}:
        raise ModelError("Motivo de conclusão incompatível com a API.")
