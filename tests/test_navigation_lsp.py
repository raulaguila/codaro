import threading

import pytest

from codaro.features import FeatureStore
from codaro.lsp import navigate
from codaro.repository import Repository


class LanguageServer:
    locations = []
    calls = []
    closed = 0

    def __init__(self, argv, root, framing):
        self.calls.append(("start", argv, root))

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "initialize":
            return {"capabilities": {"definitionProvider": True, "referencesProvider": True}}
        return self.locations

    def notify(self, *args):
        pass

    def close(self):
        type(self).closed += 1


def test_lsp_locations_are_resolved_and_filtered_by_repository_policy(tmp_path, monkeypatch):
    source = tmp_path / "main.go"
    source.write_text("package main\nfunc main() {}\n")
    target = tmp_path / "service.go"
    target.write_text("package main\nfunc Run() {}\n")
    secret = tmp_path / ".env"
    secret.write_text("PRIVATE=1")

    def location(path):
        return {
            "uri": path.as_uri(),
            "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 4}},
        }

    LanguageServer.calls, LanguageServer.closed = [], 0
    LanguageServer.locations = [
        location(target),
        location(secret),
        {"uri": "https://outside.test/code", "range": {}},
    ]
    monkeypatch.setattr("codaro.lsp.StdioRPC", LanguageServer)
    result = navigate(Repository(tmp_path), "main.go", 2, 5, "definition", {"enabled": True})
    assert result["resolved"] and len(result["results"]) == 1
    assert result["results"][0]["path"] == "service.go"
    assert LanguageServer.calls[0][1] == ["gopls"]
    assert LanguageServer.closed == 1
    assert any(item[0] == "textDocument/definition" for item in LanguageServer.calls)


def test_disabled_or_missing_language_server_does_not_block_fallback(tmp_path, monkeypatch):
    (tmp_path / "a.rs").write_text("fn main() {}")
    repository = Repository(tmp_path)
    assert navigate(repository, "a.rs", 1, 0, "definition", {"enabled": False}) is None

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("not installed")

    monkeypatch.setattr("codaro.lsp.StdioRPC", unavailable)
    assert navigate(repository, "a.rs", 1, 0, "definition", {"enabled": True}) is None
    with pytest.raises(ValueError):
        navigate(repository, "a.rs", 1, 999, "definition", {"enabled": True})


def test_lsp_utf16_position_and_cancel_are_preserved(tmp_path, monkeypatch):
    (tmp_path / "a.ts").write_text('const x = "😀";\n')
    from codaro.llm import RequestCancelled

    class CancelServer(LanguageServer):
        def request(self, method, params, **kwargs):
            assert kwargs["cancelled"].is_set()
            raise RequestCancelled("cancelled")

    monkeypatch.setattr("codaro.lsp.StdioRPC", CancelServer)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RequestCancelled):
        navigate(Repository(tmp_path), "a.ts", 1, 14, "references", {"enabled": True}, cancelled)


def test_feature_store_accepts_language_servers_for_the_requested_languages(tmp_path):
    store = FeatureStore(tmp_path)
    data = store.load()
    data["lsp"] = {
        "enabled": True,
        "servers": {
            "go": ["gopls"],
            "java": ["jdtls"],
            "csharp": ["csharp-ls"],
            "sql": ["sql-language-server"],
        },
    }
    store.save(data)
    assert store.load()["lsp"]["servers"]["go"] == ["gopls"]
