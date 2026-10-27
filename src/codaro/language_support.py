"""Capability inventory distinguishes parser availability from exercised behavior."""

import shutil

from codaro.languages import (
    ALIASES,
    EXTENSIONS,
    FILENAMES,
    SUPPORTED_LANGUAGES,
    language_members,
    parser_available,
)
from codaro.lsp import DEFAULT_SERVERS

FIXTURE_LANGUAGES = frozenset(
    (
        "python go java javascript typescript tsx c cpp csharp rust sql ruby php "
        "kotlin swift scala "
        "bash lua dart elixir erlang haskell ocaml r julia perl powershell clojure fsharp fortran "
        "zig solidity proto graphql vue svelte"
    ).split()
)


def language_support(language=None, *, offset=0, limit=20, detail="summary"):
    """Return a stable, resumable page; only full pages probe local LSP binaries."""
    if (
        type(offset) is not int
        or not 0 <= offset <= 10000
        or type(limit) is not int
        or not 1 <= limit <= 60
    ):
        raise ValueError("Página inválida: limit 1–60 e offset 0–10000.")
    if detail not in ("summary", "full"):
        raise ValueError("detail deve ser summary ou full.")
    names = sorted(language_members(language)) if language is not None else SUPPORTED_LANGUAGES
    selected = names[offset : offset + limit]
    results = []
    for name in selected:
        available = parser_available(name)
        item = {
            "language": name,
            "extensions": list(EXTENSIONS.get(name, ())),
            "parser_available": available,
            "structural_verification": "fixture"
            if name in FIXTURE_LANGUAGES
            else "parser_only"
            if available
            else "text_only",
        }
        if filenames := FILENAMES.get(name):
            item["filenames"] = list(filenames)
        if detail == "full":
            argv = DEFAULT_SERVERS.get(name)
            item.update(
                aliases=[alias for alias, canonical in ALIASES.items() if canonical == name],
                filter_members=list(language_members(name)),
                lsp_default=argv,
                lsp_installed=bool(argv and shutil.which(argv[0])),
                build_validation="discover_checks_required",
            )
        results.append(item)
    next_offset = offset + len(results)
    more = next_offset < len(names)
    return {
        "results": results,
        "offset": offset,
        "total": len(names),
        "next_offset": next_offset if more else None,
        "truncated": more,
        "detail": detail,
        "notice": "Parsing e fixtures não comprovam todos os dialetos. "
        "Use detail=full para aliases/LSP; build exige discover_checks.",
    }
