from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python
from tree_sitter import Language, Parser

PYTHON_LANGUAGE = Language(tree_sitter_python.language())


@dataclass(frozen=True)
class Chunk:
    symbol: str
    start: int
    end: int
    signature: str
    content: str
    declaration_start: int | None = None
    declaration_end: int | None = None


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
    if path.suffix.lower() == ".py":
        tree = Parser(PYTHON_LANGUAGE).parse(text.encode("utf-8"))
        # Iterative traversal avoids recursion errors for deeply nested source.
        pending = [(tree.root_node, ())]
        while pending:
            node, parents = pending.pop()
            nested = parents
            if node.type in {"function_definition", "class_definition"}:
                name_node = node.child_by_field_name("name")
                name = name_node.text.decode("utf-8") if name_node else "<anonymous>"
                nested = (*parents, name)
                outer = node.parent if node.parent.type == "decorated_definition" else node
                start = outer.start_point.row + 1
                end = outer.end_point.row + (1 if outer.end_point.column else 0)
                end = max(start, end)
                body = node.child_by_field_name("body")
                if body:
                    header = node.text[: body.start_byte - node.start_byte].decode("utf-8")
                    signature = " ".join(header.split())
                else:
                    signature = lines[node.start_point.row].strip()
                # Classes retain their complete implementation, just like functions.
                for offset in range(start, end + 1, 100):
                    chunk_end = min(offset + 99, end)
                    chunks.append(
                        Chunk(
                            ".".join(nested),
                            offset,
                            chunk_end,
                            signature[:300],
                            "\n".join(lines[offset - 1 : chunk_end]),
                            start,
                            end,
                        )
                    )
            pending.extend((child, nested) for child in reversed(node.named_children))
    # Windows preserve module-level statements for Python and other languages.
    for offset in range(0, len(lines), 60):
        chunks.append(
            Chunk(
                "<module>" if path.suffix.lower() == ".py" else "<file>",
                offset + 1,
                min(offset + 60, len(lines)),
                lines[offset][:300],
                "\n".join(lines[offset : offset + 60]),
            )
        )
    return chunks
