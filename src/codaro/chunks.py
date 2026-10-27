from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from codaro.languages import language_for, symbols_for


@dataclass(frozen=True)
class Chunk:
    symbol: str
    start: int
    end: int
    signature: str
    content: str
    declaration_start: int | None = None
    declaration_end: int | None = None
    kind: str = "text"
    language: str = "text"


def terms(text: str, limit: int | None = 40) -> list[str]:
    # Split acronym boundaries as well as camelCase; retain original identifiers.
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", expanded)
    words = re.findall(r"[^\W_]+", expanded.lower(), flags=re.UNICODE)
    whole = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
    result = list(dict.fromkeys(words + whole))
    return result if limit is None else result[:limit]


def chunks_for(path: Path, text: str) -> list[Chunk]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not text:
        return []
    chunks: list[Chunk] = []
    language = language_for(path, text)
    for symbol in symbols_for(path, text):
        comment_start = symbol.start
        while comment_start > max(1, symbol.start - 6):
            previous = lines[comment_start - 2].strip()
            if not previous.startswith(("#", "//", "/*", "*", "--")):
                break
            comment_start -= 1
        for offset in range(comment_start, symbol.end + 1, 100):
            end = min(offset + 99, symbol.end)
            chunks.append(
                Chunk(
                    symbol.name,
                    offset,
                    end,
                    symbol.signature,
                    "\n".join(lines[offset - 1 : end]),
                    symbol.start,
                    symbol.end,
                    symbol.kind,
                    language,
                )
            )
    # Windows preserve module-level statements for Python and other languages.
    for offset in range(0, len(lines), 60):
        chunks.append(
            Chunk(
                "<module>" if path.suffix.lower() == ".py" else "<file>",
                offset + 1,
                min(offset + 60, len(lines)),
                lines[offset][:300],
                "\n".join(lines[offset : offset + 60]),
                language=language,
            )
        )
    return chunks
