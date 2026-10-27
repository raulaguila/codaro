"""Optional installed language servers; diagnostic evidence includes a file hash."""

import hashlib
import time

from codaro.languages import language_for
from codaro.llm import RequestCancelled
from codaro.rpc import StdioRPC, check
from codaro.tool_registry import Tool, definition

DIAGNOSTICS_TOOL = definition(
    "get_diagnostics",
    "Consulta diagnósticos LSP do arquivo atual. Não substitui execução de testes.",
    {"path": {"type": "string", "maxLength": 2000}},
    ["path"],
)
DEFAULT_SERVERS = {
    "python": ["pyright-langserver", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
    "javascript": ["typescript-language-server", "--stdio"],
    "tsx": ["typescript-language-server", "--stdio"],
    "go": ["gopls"],
    "java": ["jdtls"],
    "c": ["clangd"],
    "cpp": ["clangd"],
    "csharp": ["csharp-ls"],
    "rust": ["rust-analyzer"],
    "sql": ["sql-language-server", "up", "--method", "stdio"],
    "ruby": ["ruby-lsp"],
    "php": ["intelephense", "--stdio"],
    "kotlin": ["kotlin-language-server"],
    "swift": ["sourcekit-lsp"],
    "lua": ["lua-language-server"],
    "dart": ["dart", "language-server", "--protocol=lsp"],
    "elixir": ["elixir-ls"],
    "zig": ["zls"],
}


def diagnostics(repository, path, config, cancelled=None, *, pool=None):
    if not config.get("enabled"):
        raise ValueError("Diagnósticos LSP desativados.")
    target = repository.resolve_file(path)
    language = language_for(target)
    if language not in DEFAULT_SERVERS and language not in config.get("servers", {}):
        return {"available": False, "reason": "Linguagem sem servidor configurado."}
    argv = config.get("servers", {}).get(language) or DEFAULT_SERVERS.get(language)
    raw = repository.read_bytes(target)
    if len(raw) > 200_000:
        return {"available": False, "reason": "Arquivo excede 200 KB para diagnóstico interativo."}
    version = hashlib.sha256(raw).hexdigest()
    client = None
    try:
        client = (
            pool.acquire(argv, repository.root)
            if pool
            else StdioRPC(argv, repository.root, framing="headers")
        )
        result = initialize(
            client,
            "initialize",
            {
                "processId": None,
                "rootUri": repository.root.as_uri(),
                "capabilities": {
                    "textDocument": {"publishDiagnostics": {"relatedInformation": False}}
                },
                "workspaceFolders": [
                    {"uri": repository.root.as_uri(), "name": repository.root.name}
                ],
            },
            cancelled=cancelled,
            timeout=10,
        )
        if not isinstance(result, dict):
            raise ValueError("Resposta de inicialização LSP inválida.")
        client.notifications.clear()
        document_version = open_document(client, target, language, raw, force=True)
        deadline = time.monotonic() + 3
        items = None
        while time.monotonic() < deadline:
            check(cancelled, deadline)
            client.receive()
            for notification in client.notifications:
                params = notification.get("params", {})
                if (
                    notification.get("method") == "textDocument/publishDiagnostics"
                    and isinstance(params, dict)
                    and params.get("uri") == target.as_uri()
                    and params.get("version", document_version) == document_version
                ):
                    items = params.get("diagnostics")
            client.notifications.clear()
            if items is not None:
                break
        if not isinstance(items, list):
            return {
                "available": True,
                "status": "pending",
                "path": path,
                "file_hash": version,
                "diagnostics": [],
                "validation_passed": False,
            }
        selected = []
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            span = item.get("range")
            if not isinstance(span, dict) or not all(
                isinstance(span.get(end), dict)
                and all(
                    type(span[end].get(key)) is int and 0 <= span[end][key] <= 1_000_000
                    for key in ("line", "character")
                )
                for end in ("start", "end")
            ):
                continue
            selected.append(
                {
                    "message": str(item.get("message", ""))[:1000],
                    "severity": item.get("severity")
                    if type(item.get("severity")) is int and 1 <= item["severity"] <= 4
                    else None,
                    "range": {
                        end: {key: span[end][key] for key in ("line", "character")}
                        for end in ("start", "end")
                    },
                    "source": str(item.get("source", ""))[:100],
                }
            )
        stale = repository.read_bytes(target) != raw
        return {
            "available": True,
            "status": "stale" if stale else "current",
            "path": path,
            "file_hash": version,
            "diagnostics": selected,
            "truncated": len(items) > 100,
            "validation_passed": False,
        }
    except (FileNotFoundError, OSError, ValueError) as exc:
        if pool and client:
            client.close()
        return {"available": False, "reason": str(exc)[:200], "validation_passed": False}
    except RequestCancelled:
        if client:
            client.close()
        raise
    finally:
        if client:
            if pool:
                pool.release(client)
            else:
                client.close()


def register_lsp(agent):
    def run(args):
        return diagnostics(
            agent.repository,
            args["path"],
            agent.features["lsp"],
            agent._cancelled,
            pool=agent.services.lsp,
        )

    agent.registry.register(Tool(DIAGNOSTICS_TOOL, source="lsp", lazy=True, handler=run))


def navigate(repository, path, line, column, operation, config, cancelled=None, *, pool=None):
    """Resolve a position with an explicitly enabled, installed language server.

    Line is one-based; column is a zero-based LSP UTF-16 character offset.
    Returned URIs must pass the same repository policy as direct source reads.
    """
    from pathlib import Path
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    if not config.get("enabled") or operation not in {"definition", "references"}:
        return None
    if type(line) is not int or line < 1 or type(column) is not int or column < 0:
        raise ValueError("Posição inválida: linha começa em 1 e coluna UTF-16 em 0.")
    target = repository.resolve_file(path)
    language = language_for(target)
    argv = config.get("servers", {}).get(language, DEFAULT_SERVERS.get(language))
    if not argv:
        return None
    raw = repository.read_bytes(target)
    lines = raw.decode("utf-8-sig").splitlines()
    if line > len(lines) or column > len(lines[line - 1].encode("utf-16-le")) // 2:
        raise ValueError("Posição fora do arquivo.")
    client = None
    try:
        client = (
            pool.acquire(argv, repository.root)
            if pool
            else StdioRPC(argv, repository.root, framing="headers")
        )
        response = initialize(
            client,
            "initialize",
            {
                "processId": None,
                "rootUri": repository.root.as_uri(),
                "capabilities": {},
                "workspaceFolders": [
                    {"uri": repository.root.as_uri(), "name": repository.root.name}
                ],
            },
            cancelled=cancelled,
            timeout=10,
        )
        if not isinstance(response, dict):
            return None
        capabilities = response.get("capabilities", {})
        if not capabilities.get(operation + "Provider"):
            return None
        open_document(client, target, language, raw)
        params = {
            "textDocument": {"uri": target.as_uri()},
            "position": {"line": line - 1, "character": column},
        }
        if operation == "references":
            params["context"] = {"includeDeclaration": False}
        found = client.request("textDocument/" + operation, params, cancelled=cancelled, timeout=10)
        if repository.read_bytes(target) != raw:
            return None
        locations = [found] if isinstance(found, dict) else found or []
        if not isinstance(locations, list):
            return None
        results = []
        for location in locations[:120]:
            if not isinstance(location, dict):
                continue
            uri = location.get("uri", location.get("targetUri", ""))
            span = location.get("range", location.get("targetSelectionRange", {}))
            parsed = urlparse(uri)
            if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
                continue
            try:
                destination = repository.resolve_file(str(Path(url2pathname(parsed.path))))
                relative = destination.relative_to(repository.root).as_posix()
                first, last = span["start"]["line"], span["end"]["line"]
                if type(first) is not int or type(last) is not int or not 0 <= first <= last:
                    continue
                data = repository.read_bytes(destination)
                if first >= len(data.decode("utf-8-sig").splitlines()):
                    continue
                results.append(
                    {
                        "path": relative,
                        "start_line": first + 1,
                        "end_line": last + 1,
                        "file_hash": hashlib.sha256(data).hexdigest(),
                    }
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return {
            "results": results,
            "resolved": True,
            "source": "language_server_locations",
            "truncated": len(locations) > 120,
        }
    except (OSError, ValueError):
        if pool and client:
            client.close()
        return None
    except RequestCancelled:
        if client:
            client.close()
        raise
    finally:
        if client:
            if pool:
                pool.release(client)
            else:
                client.close()


def initialize(client, method, params, *, cancelled=None, timeout=10):
    cached = getattr(client, "_codaro_initialize", None)
    if cached is None:
        cached = client.request(method, params, cancelled=cancelled, timeout=timeout)
        if isinstance(cached, dict):
            client._codaro_initialize = cached
            client.notify("initialized")
    return cached


def open_document(client, target, language, raw, *, force=False):
    documents = getattr(client, "_codaro_documents", {})
    client._codaro_documents = documents
    uri = target.as_uri()
    previous = documents.get(uri)
    version = previous[0] if previous else 0
    if previous is None:
        version = 1
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language,
                    "version": version,
                    "text": raw.decode("utf-8-sig"),
                }
            },
        )
    elif force or previous[1] != raw:
        version += 1
        client.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": raw.decode("utf-8-sig")}],
            },
        )
    documents[uri] = (version, raw)
    while len(documents) > 128:
        oldest = next(iter(documents))
        del documents[oldest]
        client.notify("textDocument/didClose", {"textDocument": {"uri": oldest}})
    return version
