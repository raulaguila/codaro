"""Structural navigation with explicit distinction between syntax and resolved references."""

import hashlib
import re
from pathlib import Path

from codaro.languages import language_for, parser_available, symbols_for
from codaro.retrieval import grep_code


def file_outline(repository, path, *, offset=0, limit=30):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 60:
        raise ValueError("Página de símbolos inválida.")
    target = repository.resolve_file(path)
    raw = repository.read_bytes(target)
    symbols = symbols_for(Path(path), raw.decode("utf-8-sig"))
    results = [
        {
            "path": path,
            "symbol": item.name,
            "kind": item.kind,
            "signature": item.signature,
            "start_line": item.start,
            "end_line": item.end,
            "language": item.language,
        }
        for item in symbols
    ]
    return {
        "results": results[offset : offset + limit],
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(results) else None,
        "total": len(results),
        "file_hash": hashlib.sha256(raw).hexdigest(),
        "structural": parser_available(language_for(path)),
        "source": "syntax_outline_not_implementation_evidence",
    }


def find_definition(index, symbol, **filters):
    symbol = symbol.replace("::", ".")
    results = index.search(symbol, limit=12, **filters)
    exact = [
        item
        for item in results
        if item["kind"] != "text"
        and (item["symbol"] == symbol or item["symbol"].endswith("." + symbol))
    ]
    return {
        "results": exact,
        "source": "structural_name_candidates",
        "notice": "Nomes iguais podem pertencer a escopos diferentes; leia as definições.",
    }


def find_references(repository, symbol, *, limit=20, offset=0, cancelled=None, **filters):
    if not isinstance(symbol, str) or not re.fullmatch(r"[\w.$:]+", symbol) or len(symbol) > 300:
        raise ValueError("Informe um identificador simples ou qualificado.")
    # Rust-regex Unicode boundaries avoid substring matches without Python backtracking.
    name = symbol.replace("::", ".").rsplit(".", 1)[-1]
    result = grep_code(
        repository,
        r"(^|[^\w$])" + re.escape(name) + r"([^\w$]|$)",
        regex=True,
        limit=limit,
        offset=offset,
        cancelled=cancelled,
        **filters,
    )
    result.update(
        source="textual_identifier_occurrences",
        resolved=False,
        notice="Ocorrências textuais incluem comentários, strings e homônimos. "
        "Não são referências resolvidas pelo compilador.",
    )
    return result
