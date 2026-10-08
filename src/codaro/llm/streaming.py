from __future__ import annotations

import codecs
import threading
from collections.abc import Iterator

import httpx

from codaro.llm.errors import (
    ModelError,
    RequestCancelled,
)
from codaro.llm.protocol import (
    MAX_STREAM_BYTES,
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
