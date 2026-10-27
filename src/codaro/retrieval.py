"""Shared scopes, query normalization and bounded literal/regex retrieval."""

from __future__ import annotations

import fnmatch
import json
import subprocess
import tempfile
import time
from pathlib import PurePosixPath

from codaro.chunks import terms
from codaro.languages import ALIASES, EXTENSIONS, language_for

STOP_WORDS = set(
    (
        "a as o os de da do das dos e em no na nos nas um uma para por que como onde qual quais "
        "the a an of to in is are where how what this project projeto código code"
    ).split()
)
CONCEPTS = {
    "permissão": ("permission", "authorize", "access"),
    "permissões": ("permission", "authorize", "access"),
    "autenticação": ("authentication", "auth", "login"),
    "usuário": ("user",),
    "usuários": ("user",),
    "senha": ("password",),
    "pagamento": ("payment",),
    "contexto": ("context",),
    "limite": ("limit", "budget"),
    "configuração": ("config", "settings"),
    "conversa": ("conversation", "session"),
    "arquivo": ("file",),
    "busca": ("search",),
    "cancelamento": ("cancel", "cancelled"),
    "erro": ("error", "exception"),
    "permissions": ("permission", "authorize", "access"),
    "authorization": ("authorize", "permission"),
    "authentication": ("authenticate", "auth", "login"),
    "editar": ("edit", "editing"),
    "documento": ("document",),
}


def query_terms(query):
    original = [word for word in terms(query) if word not in STOP_WORDS]
    original = original or terms(query)
    expanded = [term for word in original for term in CONCEPTS.get(word, ())]
    return list(dict.fromkeys([*original, *expanded]))[:60]


def scope_options(path=None, glob=None, language=None, extension=None):
    options = {}
    for key, value in (
        ("path", path),
        ("glob", glob),
        ("language", language),
        ("extension", extension),
    ):
        if value is None:
            continue
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 500
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Filtro inválido.")
        options[key] = value
    if path is not None:
        if path.startswith(("/", "\\")) or ".." in PurePosixPath(path.replace("\\", "/")).parts:
            raise ValueError("Escopo deve ser relativo ao projeto.")
        options["path"] = path.replace("\\", "/").rstrip("/") or "."
    if language is not None:
        canonical = ALIASES.get(language.casefold(), language.casefold())
        if canonical not in EXTENSIONS and canonical not in {"text", "dockerfile", "make"}:
            raise ValueError("Linguagem desconhecida; use extensão ou glob.")
        options["language"] = canonical
    if extension is not None:
        options["extension"] = "." + extension.lower().lstrip(".")
    return options


def matches_scope(name, options):
    scope = options.get("path", ".")
    if scope != "." and name != scope and not name.startswith(scope + "/"):
        return False
    pattern = options.get("glob")
    if pattern and not (fnmatch.fnmatchcase(name, pattern) or PurePosixPath(name).match(pattern)):
        return False
    if options.get("language"):
        actual, requested = language_for(name), options["language"]
        if actual != requested and not (
            PurePosixPath(name).suffix.lower() == ".h" and requested in {"c", "cpp"}
        ):
            return False
    return (
        not options.get("extension") or PurePosixPath(name).suffix.lower() == options["extension"]
    )


def scoped_files(repository, **filters):
    options = scope_options(**filters)
    paths = [
        path
        for path in repository.files()
        if matches_scope(path.relative_to(repository.root).as_posix(), options)
    ]
    if options.get("language") in {"c", "cpp"}:
        selected = []
        for path in paths:
            try:
                if (
                    path.suffix.lower() != ".h"
                    or language_for(path, repository.read_text(str(path))) == options["language"]
                ):
                    selected.append(path)
            except (OSError, ValueError):
                continue
        return selected
    return paths


def grep_code(
    repository,
    query,
    *,
    regex=False,
    case_sensitive=True,
    limit=20,
    offset=0,
    cancelled=None,
    **filters,
):
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("Busca literal/regex deve ter de 1 a 1000 caracteres.")
    if type(regex) is not bool or type(case_sensitive) is not bool:
        raise ValueError("regex e case_sensitive devem ser booleanos.")
    if (
        type(limit) is not int
        or not 1 <= limit <= 60
        or type(offset) is not int
        or not 0 <= offset <= 10000
    ):
        raise ValueError("Página inválida: limit 1–60 e offset 0–10000.")
    paths = scoped_files(repository, **filters)
    found, scanned, more = [], 0, False
    deadline = time.monotonic() + 20
    # A file at a time bounds subprocess output even for regex matching every line.
    for path in paths:
        if time.monotonic() >= deadline:
            raise ValueError("Busca excedeu 20 segundos; refine diretório, extensão ou padrão.")
        if cancelled is not None and cancelled.is_set():
            from codaro.llm import RequestCancelled

            raise RequestCancelled("Busca cancelada.")
        name = path.relative_to(repository.root).as_posix()
        args = ["rg", "--json", "--line-number", "--max-count", str(offset + limit + 1)]
        if not regex:
            args.append("--fixed-strings")
        if not case_sensitive:
            args.append("--ignore-case")
        # Read through Repository so ignores, no-follow policy and size limits remain authoritative.
        try:
            raw = repository.read_bytes(path)
        except (OSError, ValueError):
            continue
        with tempfile.TemporaryFile() as output:
            try:
                result = subprocess.run(
                    [*args, "-e", query, "--", "-"],
                    input=raw,
                    stdout=output,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("Busca excedeu o prazo; refine o padrão ou o escopo.") from exc
            if result.returncode not in (0, 1):
                raise ValueError("Padrão de busca inválido ou não suportado pelo ripgrep.")
            output.seek(0)
            for line in output:
                record = json.loads(line)
                if record["type"] != "match":
                    continue
                if scanned >= offset:
                    data = record["data"]
                    found.append(
                        {
                            "path": name,
                            "start_line": data["line_number"],
                            "end_line": data["line_number"],
                            "preview": data["lines"].get("text", "")[:300].rstrip(),
                            "source": "text_match_not_resolved_reference",
                        }
                    )
                scanned += 1
                if len(found) > limit:
                    more = True
                    break
        if more:
            break
    return {
        "results": found[:limit],
        "offset": offset,
        "next_offset": offset + limit if more else None,
        "truncated": more,
        "match_kind": "regex" if regex else "literal",
        "source": "current_text_matches",
    }
