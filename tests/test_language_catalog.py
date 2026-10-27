"""Catalog/filter parity tests exercise retrieval, not complete language semantics."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_agent import FakeModel, call
from typer.testing import CliRunner

from codaro.agent import Agent
from codaro.agent.messages import serialize
from codaro.agent.results import fit_result
from codaro.cli import app
from codaro.index import CodeIndex
from codaro.language_support import language_support
from codaro.languages import (
    ALIASES,
    BY_EXTENSION,
    EXTENSIONS,
    FILENAMES,
    SUPPORTED_LANGUAGES,
    language_for,
    language_for_extension,
    language_members,
    normalize_language,
)
from codaro.repository import Repository
from codaro.semantic import SemanticSearch


def test_extensions_have_one_owner_and_aliases_normalize():
    extensions = [suffix for suffixes in EXTENSIONS.values() for suffix in suffixes]
    assert len(extensions) == len(set(extensions))
    for suffix, language in BY_EXTENSION.items():
        assert language_for_extension(suffix.upper()) == language
        assert language_for_extension(suffix.lstrip(".")) == language
    for alias, language in ALIASES.items():
        assert normalize_language(f" {alias.upper()} ") == language


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Containerfile", "dockerfile"),
        ("Dockerfile", "dockerfile"),
        ("GNUmakefile", "make"),
        ("Makefile", "make"),
        ("CMakeLists.txt", "cmake"),
        ("package.json", "json"),
        ("pyproject.toml", "toml"),
        ("go.mod", "text"),
        ("module.ml", "ocaml"),
        ("module.mli", "ocaml_interface"),
    ],
)
def test_named_files_and_manifest_formats(name, expected):
    assert language_for(Path("nested") / name) == expected


@pytest.mark.parametrize("requested", [*SUPPORTED_LANGUAGES, *ALIASES])
def test_all_languages_and_aliases_filter_the_same_files_in_three_tools(tmp_path, requested):
    expected = set()
    for member in language_members(requested):
        names = [f"sample{suffix}" for suffix in EXTENSIONS.get(member, ())]
        names.extend(FILENAMES.get(member, ()))
        if member == "text":
            names.append("LICENSE")
        for name in names:
            (tmp_path / name).write_text("catalogneedle\n")
            expected.add(name)
    neighbor = "neighbor.sql" if normalize_language(requested) == "python" else "neighbor.py"
    (tmp_path / neighbor).write_text("catalogneedle\n")
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / next(iter(sorted(expected)))).write_text("catalogneedle\n")
    (tmp_path / ".gitignore").write_text("ignored/\n# catalogneedle\n")
    if normalize_language(requested) == "text":
        expected.add(".gitignore")
    repository = Repository(tmp_path)
    agent = Agent(repository, FakeModel([]), mode="ask", persist_memory=False)
    try:
        with CodeIndex(repository) as index:
            listed = agent.execute(index, "list_files", {"language": requested})
            grep = agent.execute(
                index, "grep_code", {"query": "catalogneedle", "language": requested, "limit": 60}
            )
            searched = agent.execute(
                index, "search_code", {"query": "catalogneedle", "language": requested, "limit": 12}
            )
            assert set(listed["files"]) == expected
            assert {row["path"] for row in grep["results"]} == expected
            assert {row["path"] for row in searched["results"]} == expected
    finally:
        agent.close()


@pytest.mark.parametrize("semantic", [False, True])
def test_ocaml_family_search_includes_interfaces_and_preserves_grammar(tmp_path, semantic):
    class Embeddings:
        def embed(self, texts):
            return ([1.0, 0.0] for _ in texts)

        def query_embed(self, query):
            return iter([[1.0, 0.0]])

    (tmp_path / "auth.ml").write_text("let authorize user = user\n")
    (tmp_path / "auth.mli").write_text("val authorize : bool -> bool\n")
    (tmp_path / "other.py").write_text("authorize = True\n")
    with CodeIndex(Repository(tmp_path)) as index:
        if semantic:
            index.semantic = SemanticSearch(encoder=Embeddings(), model="test")
        results = index.search("authorize", language="ocaml")
        assert {row["path"] for row in results} == {"auth.ml", "auth.mli"}
        assert {row["path"] for row in index.search("authorize", language="ocaml_interface")} == {
            "auth.mli"
        }
        assert {row[0] for row in index.db.execute("SELECT DISTINCT language FROM chunks")} == {
            "ocaml",
            "ocaml_interface",
            "python",
        }
        if semantic:
            assert index.semantic.status == "ready"
            assert {
                row[0]
                for row in index.db.execute(
                    "SELECT DISTINCT c.path FROM vectors v JOIN chunks c ON c.id=v.id"
                )
            } == {"auth.ml", "auth.mli"}


@pytest.mark.parametrize("detail", ["summary", "full"])
@pytest.mark.parametrize("budget", [900, 2400])
def test_inventory_can_be_fully_walked_after_budget_reduction(detail, budget):
    offset, seen = 0, []
    for _ in range(len(SUPPORTED_LANGUAGES) + 1):
        result = fit_result(language_support(offset=offset, limit=60, detail=detail), budget)
        assert len(serialize(result)) <= budget
        assert result["results"], result
        seen.extend(row["language"] for row in result["results"])
        assert result["total"] == len(SUPPORTED_LANGUAGES)
        cursor = result["next_offset"]
        if cursor is None:
            break
        assert cursor > offset
        offset = cursor
    assert seen == list(SUPPORTED_LANGUAGES)


def test_inventory_filtered_pages_and_local_details(monkeypatch):
    monkeypatch.setattr("codaro.language_support.shutil.which", lambda name: f"/bin/{name}")
    assert language_support("NODEJS")["results"][0]["language"] == "javascript"
    first = language_support("ocaml", limit=1, detail="full")
    assert first["results"][0]["filter_members"] == ["ocaml", "ocaml_interface"]
    assert first["total"] == 2 and first["next_offset"] == 1
    second = language_support("ocaml", offset=1)
    assert second["results"][0]["language"] == "ocaml_interface"
    assert second["next_offset"] is None
    assert language_support("ocaml_interface")["total"] == 1
    assert language_support("make")["results"][0]["filenames"] == ["Makefile", "GNUmakefile"]
    text = language_support("text")["results"][0]
    assert not text["parser_available"] and text["structural_verification"] == "text_only"
    go = language_support("go", detail="full")["results"][0]
    assert go["lsp_installed"] and go["lsp_default"]
    assert "golang" in go["aliases"]
    assert language_support(offset=10000)["results"] == []
    assert language_support(offset=10000)["next_offset"] is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"language": "unknown"},
        {"language": 1},
        {"language": ""},
        {"offset": -1},
        {"offset": True},
        {"offset": 10001},
        {"limit": 0},
        {"limit": 61},
        {"limit": False},
        {"detail": "unknown"},
    ],
)
def test_inventory_rejects_invalid_arguments(arguments):
    with pytest.raises(ValueError):
        language_support(**arguments)


def test_inventory_pagination_is_available_in_native_agent_loop(tmp_path):
    model = FakeModel(
        [
            call("request_tools", {"names": ["get_language_support"]}, "load"),
            call("get_language_support", {"language": "ocaml", "limit": 1, "detail": "full"}),
            call("get_language_support", {"language": "ocaml", "offset": 1}, "page-2"),
            {"content": "OCaml permite consultar implementações e interfaces."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, mode="ask", persist_memory=False)
    try:
        agent.ask("Quais são os níveis de suporte a OCaml?")
        outputs = [
            json.loads(message["content"])
            for message in model.requests[-1][0]
            if message["role"] == "tool" and message.get("name") == "get_language_support"
        ]
        assert outputs[0]["next_offset"] == 1
        assert outputs[1]["results"][0]["language"] == "ocaml_interface"
        agent.validate_arguments("get_language_support", {"limit": 1, "detail": "full"})
        with pytest.raises(ValueError):
            agent.validate_arguments("get_language_support", {"detail": "invalid"})
    finally:
        agent.close()


def test_cli_inventory_exposes_pagination_and_details():
    result = CliRunner().invoke(
        app, ["languages", "--language", "ocaml", "--offset", "1", "--full"]
    )
    assert result.exit_code == 0, result.output
    page = json.loads(result.output)
    assert page["results"][0]["language"] == "ocaml_interface"
    assert page["next_offset"] is None and page["detail"] == "full"


def test_documented_catalog_is_current():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/update_language_docs.py"), "--check"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
