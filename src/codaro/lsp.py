"""Optional installed language servers; diagnostic evidence includes a file hash."""

import hashlib
import time

from codaro.rpc import StdioRPC, check
from codaro.tool_registry import Tool, definition

DIAGNOSTICS_TOOL = definition(
    "get_diagnostics",
    "Consulta diagnósticos LSP do arquivo atual. Não substitui execução de testes.",
    {"path": {"type": "string", "maxLength": 2000}},
    ["path"],
)
LANGUAGES = {".py": "python", ".ts": "typescript", ".tsx": "typescript"}
DEFAULT_SERVERS = {
    "python": ["pyright-langserver", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
}


def diagnostics(repository, path, config, cancelled=None):
    if not config.get("enabled"):
        raise ValueError("Diagnósticos LSP desativados.")
    target = repository.resolve_file(path)
    language = LANGUAGES.get(target.suffix)
    if not language:
        return {"available": False, "reason": "Linguagem sem servidor configurado."}
    argv = config.get("servers", {}).get(language, DEFAULT_SERVERS[language])
    raw = repository.read_bytes(target)
    if len(raw) > 200_000:
        return {"available": False, "reason": "Arquivo excede 200 KB para diagnóstico interativo."}
    version = hashlib.sha256(raw).hexdigest()
    client = None
    try:
        client = StdioRPC(argv, repository.root, framing="headers")
        result = client.request(
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
        client.notify("initialized")
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": target.as_uri(),
                    "languageId": language,
                    "version": 1,
                    "text": raw.decode("utf-8-sig"),
                }
            },
        )
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
                    and params.get("version", 1) == 1
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
        return {"available": False, "reason": str(exc)[:200], "validation_passed": False}
    finally:
        if client:
            client.close()


def register_lsp(agent):
    def run(args):
        return diagnostics(agent.repository, args["path"], agent.features["lsp"], agent._cancelled)

    agent.registry.register(Tool(DIAGNOSTICS_TOOL, source="lsp", lazy=True, handler=run))
