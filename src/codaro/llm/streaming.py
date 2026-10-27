from __future__ import annotations

import threading
from collections.abc import Iterator

import httpx

from codaro.llm.errors import (
    ModelError,
    RequestCancelled,
)
from codaro.llm.protocol import (
    MAX_STREAM_BYTES,
    MAX_STREAM_EVENT_BYTES,
)
from codaro.runtime import remaining_seconds


def check_cancelled(cancelled: threading.Event | None):
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise ModelError("Prazo da tarefa atingido; progresso salvo para retomada.")
    if cancelled is not None and cancelled.is_set():
        raise RequestCancelled("Investigação cancelada.")


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


def sse_events(
    response: httpx.Response, cancelled: threading.Event | None, *, max_bytes=MAX_STREAM_BYTES
) -> Iterator[str]:
    buffer = bytearray()
    fields: list[str] = []
    total = event_bytes = scan_from = 0
    for raw in response.iter_bytes():
        check_cancelled(cancelled)
        total += len(raw)
        if total > max_bytes:
            raise ModelError(
                f"Stream excede o limite de {max_bytes // (1024 * 1024)} MiB; "
                "ajuste CODARO_MAX_STREAM_MB."
            )
        buffer.extend(raw)
        cursor = 0
        while True:
            end = buffer.find(b"\n", scan_from)
            if end < 0:
                scan_from = len(buffer) - cursor
                if cursor:
                    del buffer[:cursor]
                break
            length = end - cursor
            if length > MAX_STREAM_EVENT_BYTES:
                raise ModelError("Evento do stream excede o limite de 1 MiB.")
            line = buffer[cursor:end].decode("utf-8").removesuffix("\r")
            cursor = scan_from = end + 1
            if not line:
                if fields:
                    yield "\n".join(fields)
                    fields = []
                event_bytes = 0
            elif line.startswith("data:"):
                event_bytes += length + 1
                if event_bytes > MAX_STREAM_EVENT_BYTES:
                    raise ModelError("Evento do stream excede o limite de 1 MiB.")
                fields.append(line[5:].removeprefix(" "))
        if len(buffer) > MAX_STREAM_EVENT_BYTES:
            raise ModelError("Evento do stream excede o limite de 1 MiB.")
    if buffer.startswith(b"data:"):
        if event_bytes + len(buffer) > MAX_STREAM_EVENT_BYTES:
            raise ModelError("Evento do stream excede o limite de 1 MiB.")
        fields.append(buffer.decode("utf-8")[5:].removeprefix(" ").removesuffix("\r"))
    elif buffer:
        buffer.decode("utf-8")  # Reject a truncated UTF-8 sequence, even in a comment.
    if fields:
        yield "\n".join(fields)
