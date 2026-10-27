"""Capability inventory distinguishes parser availability from exercised behavior."""

import shutil

from codaro.languages import ALIASES, EXTENSIONS, parser_available
from codaro.lsp import DEFAULT_SERVERS

FIXTURE_LANGUAGES = frozenset(
    (
        "python go java javascript typescript tsx c cpp csharp rust sql ruby php "
        "kotlin swift scala "
        "bash lua dart elixir erlang haskell ocaml r julia perl powershell clojure fsharp fortran "
        "zig solidity proto graphql vue svelte"
    ).split()
)


def language_support(language=None):
    if language is not None:
        language = ALIASES.get(language.casefold(), language.casefold())
        if language not in EXTENSIONS:
            raise ValueError("Linguagem desconhecida.")
    results = []
    for name, suffixes in EXTENSIONS.items():
        if language and name != language:
            continue
        argv = DEFAULT_SERVERS.get(name)
        results.append(
            {
                "language": name,
                "extensions": list(suffixes),
                "parser_available": parser_available(name),
                "structural_verification": "fixture"
                if name in FIXTURE_LANGUAGES
                else "parser_only",
                "lsp_default": argv,
                "lsp_installed": bool(argv and shutil.which(argv[0])),
                "build_validation": "discover_checks_required",
            }
        )
    return {
        "results": results,
        "notice": "Parsing e fixtures não comprovam todos os dialetos. "
        "LSP/build dependem das ferramentas do projeto.",
    }
