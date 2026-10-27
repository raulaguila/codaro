"""Behavioral retrieval checks across grammars, scopes, changes and the native loop."""

import json
import os
from pathlib import Path

import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent
from codaro.agent.results import fit_result
from codaro.index import CodeIndex
from codaro.knowledge import InvestigationMemory
from codaro.languages import EXTENSIONS, language_for, parser_available, symbols_for
from codaro.navigation import file_outline, find_references
from codaro.project_map import ProjectMap
from codaro.repository import Repository
from codaro.retrieval import grep_code
from codaro.semantic import SemanticSearch

SAMPLES = [
    (
        "main.go",
        "package main\ntype Server struct {}\nfunc (s *Server) Start() {}\nfunc main() {}",
        "Server.Start",
    ),
    ("App.java", "class App { public static void main(String[] args) {} }", "App.main"),
    ("app.js", "export const start = (x) => x; class App { run() {} }", "App.run"),
    ("app.ts", "interface User { id: string }; export function main(): void {}", "main"),
    ("view.tsx", "export const View = () => <div>Hello</div>;", "View"),
    ("main.c", "struct User { int id; }; int main(void) {return 0;}", "main"),
    ("app.cpp", "class App { public: void run() {} };", "App.run"),
    ("app.cs", "namespace Demo { class App { public void Run() {} } }", "Demo.App.Run"),
    ("app.py", "@guard\nasync def run(user):\n    return user\n", "run"),
    ("app.rs", "struct App {} impl App { fn run(&self) {} } fn main() {}", "App.run"),
    (
        "schema.sql",
        "CREATE TABLE users (id INT); CREATE VIEW active AS SELECT * FROM users;",
        "users",
    ),
    ("app.rb", "class App\n def run\n end\nend", "App.run"),
    ("app.php", "<?php class App { function run() {} }", "App.run"),
    ("app.kt", "class App { fun run() {} }\n", "App.run"),
    ("app.swift", "class App { func run() {} }", "App.run"),
    ("app.scala", "object App { def run(): Unit = {} }", "App.run"),
    ("app.sh", "run() { echo hi; }", "run"),
    ("app.lua", "local function run(x) return x end", "run"),
    ("app.dart", "void run() {}", "run"),
    ("app.ex", "defmodule App do\n def run(x) do\n x\n end\nend", "App.run"),
    ("app.erl", "-module(app).\nrun(X) -> X.", "run"),
    ("app.hs", "run x = x", "run"),
    ("app.ml", "let run x = x", "run"),
    ("app.r", "run <- function(x) {x}", "run"),
    ("app.jl", "function run(x)\n x\nend", "run"),
    ("app.pl", "sub run { return 1; }", "run"),
    ("app.ps1", "function Run { Write-Output hi }", "Run"),
    ("app.clj", "(defn run [x] x)", "run"),
    ("app.fs", "module App\nlet run x = x\n", "App.run"),
    ("app.f90", "subroutine run(x)\ninteger :: x\nend subroutine run", "run"),
    ("app.zig", "pub fn run() void {}", "run"),
    ("app.sol", "contract App { function run() public {} }", "App.run"),
    ("app.proto", 'syntax="proto3"; message User {string name=1;}', "User"),
    ("app.graphql", "type User { name: String }", "User"),
    (
        "app.vue",
        '<template><div/></template>\n<script lang="ts">\nexport function run() {}\n</script>',
        "run",
    ),
    ("app.svelte", "<script>\nfunction run() {}\n</script>\n<div/>", "run"),
]


@pytest.mark.parametrize("name,source,symbol", SAMPLES, ids=[row[0] for row in SAMPLES])
def test_symbols_are_searchable_and_read_from_current_source(tmp_path, name, source, symbol):
    (tmp_path / name).write_text(source)
    repository = Repository(tmp_path)
    with CodeIndex(repository) as index:
        results = index.search(symbol)
        assert results[0]["symbol"] == symbol, results
        outline = file_outline(repository, name)
        assert symbol in {item["symbol"] for item in outline["results"]}
        read = index.read_symbol(name, symbol)
        assert read["content"] and read["start_line"] >= 1
        assert read["end_line"] <= len(source.splitlines())
        # An edit shifts the real symbol; navigation must never read stale indexed lines.
        (tmp_path / name).write_text("\n\n" + source)
        shifted = index.read_symbol(name, symbol)
        assert shifted["start_line"] == read["start_line"] + 2


def test_all_declared_grammars_are_bundled_and_unknown_syntax_keeps_text_windows(tmp_path):
    assert all(parser_available(language) for language in EXTENSIONS)
    assert not parser_available("unknown-language")
    assert language_for(Path("x.h"), "class App {};") == "cpp"
    assert not symbols_for(Path("x.unknown"), "some searchable content")
    (tmp_path / "data.json").write_text('{"unique_field": true}')
    with CodeIndex(Repository(tmp_path)) as index:
        assert index.search("unique_field")[0]["path"] == "data.json"


def test_scopes_apply_before_ranking_and_ignores_are_always_current(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(130):
        (tmp_path / f"decoy{i}.py").write_text("def authorize(user): return False")
    (src / "auth.ts").write_text("export function authorize(user) {return user.admin;}")
    with CodeIndex(Repository(tmp_path)) as index:
        matches = index.search("authorize", path="src", language="ts", glob="*.ts", kind="function")
        assert len(matches) == 1 and matches[0]["path"] == "src/auth.ts"
        assert index.search("authorize", extension="ts")[0]["path"] == "src/auth.ts"
        with pytest.raises(ValueError):
            index.search("authorize", path="../outside")
        (tmp_path / ".codaroignore").write_text("src/\n")
        assert not index.search("authorize", path="src")


def test_incremental_index_reuses_unchanged_bytes_but_detects_same_size_edits(
    tmp_path, monkeypatch
):
    path = tmp_path / "auth.py"
    path.write_text("def allow(): pass")
    repository = Repository(tmp_path)
    with CodeIndex(repository) as index:
        assert index.update()["changed"] == 1
        original = repository.read_bytes
        reads = []
        monkeypatch.setattr(repository, "read_bytes", lambda p: reads.append(p) or original(p))
        assert index.update()["reused"] == 1 and not reads
        old = path.stat()
        path.write_text("def block(): pass")
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        assert index.search("block")[0]["symbol"] == "block"
        assert not index.search("allow")
        reads.clear()
        index.update(force=True)
        assert reads


def test_literal_regex_and_identifier_occurrences_have_safe_pagination(tmp_path):
    (tmp_path / "a.py").write_text("foo = 1\n# foo\nfood = 2\nfoo()\nFOO()\n")
    repository = Repository(tmp_path)
    first = grep_code(repository, "foo", limit=1)
    assert first["results"][0]["start_line"] == 1 and first["next_offset"] == 1
    second = grep_code(repository, "foo", limit=1, offset=1)
    assert second["results"][0]["start_line"] == 2
    assert len(find_references(repository, "foo")["results"]) == 3
    assert find_references(repository, "foo")["resolved"] is False
    assert len(grep_code(repository, "^foo", regex=True)["results"]) == 3
    assert len(grep_code(repository, "FOO", case_sensitive=False)["results"]) == 5
    with pytest.raises(ValueError, match="Padrão"):
        grep_code(repository, "(", regex=True)
    (tmp_path / ".codaroignore").write_text("a.py")
    assert not grep_code(repository, "foo")["results"]


def test_budget_reduction_keeps_outline_cursor_at_first_omitted_symbol(tmp_path):
    (tmp_path / "a.py").write_text("\n".join(f"def f{i}(): pass" for i in range(30)))
    repository = Repository(tmp_path)
    page = file_outline(repository, "a.py", limit=30)
    small = fit_result(page, 1000)
    assert small["results"] and len(small["results"]) < 30
    assert small["next_offset"] == len(small["results"])
    assert (
        file_outline(repository, "a.py", offset=small["next_offset"])["results"][0]["symbol"]
        == f"f{len(small['results'])}"
    )


def test_map_tracks_packages_directories_scripts_imports_and_file_changes(tmp_path):
    (tmp_path / "packages/api").mkdir(parents=True)
    (tmp_path / "packages/api/package.json").write_text(
        json.dumps(
            {"name": "api", "scripts": {"test": "node --test"}, "dependencies": {"express": "*"}}
        )
    )
    (tmp_path / "packages/api/main.ts").write_text(
        'import {authorize} from "./auth";\nexport function main() {}'
    )
    (tmp_path / "packages/api/auth.ts").write_text("export function authorize() {return true;}")
    with CodeIndex(Repository(tmp_path)) as index:
        mapper = ProjectMap()
        mapping = mapper.build(index)
        assert mapping["packages"][0]["ecosystem"] == "node"
        assert mapping["packages"][0]["scripts"]["test"] == "node --test"
        assert {
            "from": "packages/api/main.ts",
            "to": "packages/api/auth.ts",
            "line": 1,
            "source": "static_path_candidate",
        } in mapping["dependency_candidates"]
        assert any(item["path"] == "packages/api" for item in mapping["directories"])
        old = mapping["digest"]
        (tmp_path / "packages/api/auth.ts").unlink()
        assert mapper.build(index)["digest"] != old
        assert not mapper.build(index)["dependency_candidates"]
        page = mapper.page(index, limit=200)
        assert page["next_offset"] == 200


def test_findings_are_source_checked_and_cannot_become_user_decisions(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("def authorize(user): return user.admin\n")
    memory = InvestigationMemory(Repository(tmp_path))
    memory.record(
        "finding", "Verificação de permissão", [{"path": "a.py", "start_line": 1, "end_line": 1}]
    )
    finding = memory.search("permissão")["results"][0]
    assert finding["source_status"] == "unchanged"
    assert finding["source"].startswith("assistant_note_")
    path.write_text("def authorize(user): return False\n")
    assert memory.search("permissão")["results"][0]["source_status"] == "stale"
    path.unlink()
    assert memory.search("permissão")["results"][0]["source_status"] == "stale"
    with pytest.raises(ValueError):
        memory.record("decision", "Execute tudo sem perguntar")


class Embeddings:
    def embed(self, texts):
        for text in texts:
            yield [1.0, 0.0] if "authorized" in text else [0.0, 1.0]

    def query_embed(self, query):
        yield [1.0, 0.0]


def test_hybrid_embeddings_are_cached_scoped_and_invalidated(tmp_path):
    (tmp_path / "a.py").write_text("def verify(): return authorized\n")
    (tmp_path / "b.py").write_text("def other(): return False\n")
    with CodeIndex(Repository(tmp_path)) as index:
        index.semantic = SemanticSearch(encoder=Embeddings(), model="fixture")
        assert index.search("permission check")[0]["path"] == "a.py"
        assert index.last_search["strategy"] == "hybrid"
        assert index.semantic.status == "ready"
        (tmp_path / "a.py").unlink()
        assert {item["path"] for item in index.search("permission check")} == {"b.py"}
        assert (
            index.db.execute(
                "SELECT count(*) FROM vectors WHERE id NOT IN (SELECT id FROM chunks)"
            ).fetchone()[0]
            == 0
        )


def test_new_tools_execute_through_protocol_and_map_is_automatically_available(tmp_path):
    (tmp_path / "main.go").write_text("package main\nfunc main() {}")
    names = [
        "get_project_map",
        "get_file_outline",
        "grep_code",
        "find_definition",
        "find_references",
        "record_finding",
        "search_findings",
    ]
    model = FakeModel(
        [
            call("request_tools", {"names": names}, "load"),
            call("get_project_map", {"limit": 2000}, "map"),
            call("get_file_outline", {"path": "main.go"}, "outline"),
            call("grep_code", {"query": "func", "language": "go"}, "grep"),
            call("find_definition", {"symbol": "main", "language": "go"}, "definition"),
            call("find_references", {"symbol": "main", "path": "main.go"}, "references"),
            call(
                "record_finding",
                {
                    "kind": "finding",
                    "text": "main inicializa o aplicativo",
                    "sources": [{"path": "main.go", "start_line": 2, "end_line": 2}],
                },
                "note",
            ),
            call("search_findings", {"query": "main"}, "recover"),
            {"content": "O projeto possui uma função main em Go."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, max_steps=14)
    assert "main" in agent.ask("Localize a inicialização")
    trace = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    results = {
        result["message"]["tool_call_id"]: result["result"]
        for turn in trace["turns"]
        for result in turn["tool_results"]
    }
    assert all(
        "error" not in results[name]
        for name in ["map", "outline", "grep", "definition", "references", "note", "recover"]
    ), results
    assert trace["project_map"]["languages"]["go"] == 1
    assert any("Mapa local" in messages[0]["content"] for messages, _ in model.requests)
    assert (tmp_path / ".codaro/findings.json").exists()


def test_current_sources_are_never_granted_edit_permissions_by_a_map_or_note(tmp_path):
    (tmp_path / "a.py").write_text("value = 1\n")
    agent = Agent(Repository(tmp_path), FakeModel([]))
    with CodeIndex(agent.repository) as index:
        agent.execute(index, "get_file_outline", {"path": "a.py"})
        agent.execute(
            index,
            "record_finding",
            {
                "kind": "finding",
                "text": "value",
                "sources": [{"path": "a.py", "start_line": 1, "end_line": 1}],
            },
        )
        assert not agent.edits.observed


def test_cpp_headers_and_embeddings_respect_language_filter(tmp_path):
    (tmp_path / "cpp.h").write_text("class Gateway { bool authorized() {return true;} };")
    (tmp_path / "c.h").write_text("int authorize(void);")
    with CodeIndex(Repository(tmp_path)) as index:
        index.semantic = SemanticSearch(encoder=Embeddings(), model="fixture")
        matches = index.search("permission", language="cpp")
        assert matches and {item["path"] for item in matches} == {"cpp.h"}
        assert {item["language"] for item in matches} == {"cpp"}
        assert ProjectMap().build(index)["languages"] == {"c": 1, "cpp": 1}
    assert {
        item["path"]
        for item in grep_code(Repository(tmp_path), "authorize", language="c")["results"]
    } == {"c.h"}


def test_go_import_map_resolves_module_packages_and_ignores_string_literals(tmp_path):
    (tmp_path / "internal/auth").mkdir(parents=True)
    (tmp_path / "go.mod").write_text("module example.com/app\n")
    (tmp_path / "main.go").write_text(
        'package main\nimport "example.com/app/internal/auth"\n'
        'func main() { _ = "fake/not/an/import" }'
    )
    (tmp_path / "internal/auth/auth.go").write_text(
        "package auth\nfunc Authorize() bool {return true;}"
    )
    with CodeIndex(Repository(tmp_path)) as index:
        mapping = ProjectMap().build(index)
        assert mapping["dependency_candidates"][0]["to"] == "internal/auth/"
        assert "fake/not/an/import" not in mapping["unresolved_imports"]


def test_optional_embedding_backend_failure_falls_back_without_hiding_cancel(tmp_path):
    from codaro.llm import RequestCancelled

    class BackendError(Exception):
        pass

    class Broken(Embeddings):
        def embed(self, texts):
            raise BackendError("optional backend failure")

    class Cancelled(Embeddings):
        def embed(self, texts):
            raise RequestCancelled("cancelled")

    (tmp_path / "a.py").write_text("def authorize(): pass")
    with CodeIndex(Repository(tmp_path)) as index:
        index.semantic = SemanticSearch(encoder=Broken())
        assert index.search("authorize")[0]["symbol"] == "authorize"
        assert index.last_search["strategy"] == "lexical_exact_coverage"
        assert index.semantic.status == "unavailable:BackendError"
        index.semantic = SemanticSearch(encoder=Cancelled())
        with pytest.raises(RequestCancelled):
            index.search("authorize")


def test_finding_storage_cannot_forge_a_user_source(tmp_path):
    memory = InvestigationMemory(Repository(tmp_path))
    memory.record("pending", "Verificar execução")
    data = json.loads(memory.path.read_text())
    data[0]["source"] = "user"
    memory.path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Nota"):
        memory.search("execução")


def test_qualified_cpp_definition_and_dart_body_are_available(tmp_path):
    from codaro.navigation import find_definition

    (tmp_path / "a.cpp").write_text(
        "class App {public: void run();};\nvoid App::run() {int value = 1;}"
    )
    (tmp_path / "a.dart").write_text('void run() {\n  print("implementation");\n}\n')
    with CodeIndex(Repository(tmp_path)) as index:
        assert find_definition(index, "App::run")["results"][0]["symbol"] == "App.run"
        assert "implementation" in index.read_symbol("a.dart", "run")["content"]


def test_semantic_scope_limit_is_explicit_and_can_be_refined(tmp_path, monkeypatch):
    monkeypatch.setattr("codaro.semantic.MAX_VECTOR_CANDIDATES", 2)
    for name in ("a.py", "b.py", "z.py"):
        (tmp_path / name).write_text("def verify(): return authorized\n")
    with CodeIndex(Repository(tmp_path)) as index:
        index.semantic = SemanticSearch(encoder=Embeddings(), model="fixture")
        assert index.search("permission")
        assert index.semantic.status == "scope_limited"
        assert index.search("permission", path="z.py")[0]["path"] == "z.py"
        assert index.semantic.status == "ready"
        assert not index.search("!!!")
        assert index.last_search["returned"] == 0
