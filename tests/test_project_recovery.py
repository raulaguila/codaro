import json

import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent, serialize
from codaro.index import CodeIndex
from codaro.repository import Repository, RepositoryError

QUESTION = "Explique a estrutura deste projeto e seus pontos de entrada."


def go_project(root):
    (root / ".gitignore").write_text("ignored/\n")
    (root / "go.mod").write_text("module example.org/thoth\n\ngo 1.23\n")
    (root / "cmd/backend").mkdir(parents=True)
    (root / "cmd/backend/main.go").write_text("package main\n\nfunc main() {}\n")


@pytest.mark.parametrize(
    "name", [".gitignore", "go.mod", "go.sum", "Makefile", "Dockerfile", "README"]
)
def test_named_project_files_can_be_listed_indexed_and_read_with_absolute_paths(tmp_path, name):
    (tmp_path / name).write_text("project configuration\n")
    repository = Repository(tmp_path)
    assert repository.files() == [tmp_path / name]
    assert repository.read_lines(str(tmp_path / name))["path"] == str(tmp_path / name)
    assert repository.read_lines(name)["content"] == "1: project configuration"
    with CodeIndex(repository) as index:
        assert index.update()["files"] == 1


@pytest.mark.parametrize("kind", ["outside", "symlink", "ignored", "secret"])
def test_named_file_support_preserves_path_and_secret_restrictions(tmp_path, kind):
    root = tmp_path / "project"
    root.mkdir()
    path = root / "Dockerfile"
    if kind in {"outside", "symlink"}:
        outside = tmp_path / "Dockerfile"
        outside.write_text("private\n")
        if kind == "outside":
            path = outside
        else:
            path.symlink_to(outside)
    elif kind == "ignored":
        path.write_text("private\n")
        (root / ".gitignore").write_text("Dockerfile\n")
    else:
        path = root / ".env"
        path.write_text("API_KEY=private\n")
    repository = Repository(root)
    assert path not in repository.files()
    with pytest.raises(RepositoryError):
        repository.resolve_file(str(path))


def test_photo_regression_reading_gitignore_recovers_go_manifest_and_real_entrypoint(tmp_path):
    go_project(tmp_path)
    model = FakeModel(
        [
            call("read_lines", {"path": str(tmp_path / ".gitignore"), "start": 1, "end": 10}),
            {
                "content": "go.mod:1 identifica o módulo; "
                "cmd/backend/main.go:3 define a função main."
            },
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    answer = agent.ask(QUESTION)
    assert "cmd/backend/main.go:3" in answer
    context = model.requests[-1][0][0]["content"]
    assert all(
        sum(message["role"] == "system" for message in messages) == 1
        for messages, _ in model.requests
    )
    assert "module example.org/thoth" in context
    assert "func main() {}" in context
    native_results = [message for message in model.requests[-1][0] if message["role"] == "tool"]
    assert len(native_results) == 1
    assert "error" not in json.loads(native_results[0]["content"])
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    retrieval = flow["local_retrievals"][0]
    assert retrieval["kind"] == "overview_recovery"
    assert {item["arguments"]["path"] for item in retrieval["calls"]} == {
        "go.mod",
        "cmd/backend/main.go",
    }
    assert all(item["path"] != ".gitignore" for item in flow["turns"][-1]["evidence"])
    assert flow["status"] == "success"


def test_failed_read_recovers_only_files_actually_available_and_not_ignored(tmp_path):
    go_project(tmp_path)
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored/main.py").write_text("DO_NOT_SEND\n")
    (tmp_path / ".env").write_text("DO_NOT_SEND\n")
    model = FakeModel(
        [
            call("read_lines", {"path": "missing.py", "start": 1, "end": 1}),
            {"content": "cmd/backend/main.go:3 é o ponto de entrada."},
        ]
    )
    answer = Agent(Repository(tmp_path), model).ask(QUESTION)
    assert "main.go:3" in answer
    assert "DO_NOT_SEND" not in serialize(model.requests)
    assert "missing.py" not in model.requests[-1][0][0]["content"]


def test_recovery_works_for_nested_projects_and_is_bounded(tmp_path):
    for name in ("backend", "frontend", "another"):
        root = tmp_path / name
        root.mkdir()
        go_project(root)
    for number in range(100):
        (tmp_path / f"file{number}.py").write_text("data = " + repr("x" * 6000))
    agent = Agent(Repository(tmp_path), FakeModel([]))
    with CodeIndex(agent.repository) as index:
        index.update()
        context, charged = agent.overview_context(index, 0, lambda _: None, None)
    assert len(context["reads"]) <= 5
    assert len(context["files"]) <= 60
    assert charged == len(serialize({"files": context["files"], "reads": context["reads"]}))
    assert charged <= 6000
    assert any("go.mod" in item["path"] for item in context["reads"])
    assert any("main.go" in item["path"] for item in context["reads"])


def test_prefetched_sources_do_not_require_formatted_citations(tmp_path):
    go_project(tmp_path)
    model = FakeModel([{"content": "O módulo Go inicia na função main do backend."}])
    agent = Agent(Repository(tmp_path), model)
    assert "função main" in agent.ask(QUESTION)
    assert len(model.requests) == 1
    assert "func main() {}" in model.requests[0][0][0]["content"]


def test_recovery_locates_main_beyond_initial_file_window(tmp_path):
    go_project(tmp_path)
    (tmp_path / "cmd/backend/main.go").write_text(
        "package main\n" + "// declarations\n" * 100 + "func main() {}\n"
    )
    model = FakeModel(
        [
            {"content": "cmd/backend/main.go:102 define main."},
        ]
    )
    answer = Agent(Repository(tmp_path), model).ask(QUESTION)
    assert "main.go:102" in answer
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    main = next(
        item
        for item in flow["local_retrievals"][0]["calls"]
        if item["arguments"]["path"].endswith("main.go")
    )
    assert main["arguments"]["start"] > 60
    assert "102: func main() {}" in main["result"]["content"]


def test_budget_truncation_updates_delivered_line_ranges_and_partial_line(tmp_path):
    content = "\n".join(f"line_{number} = '" + "x" * 100 + "'" for number in range(1, 21))
    original = Repository.render_lines("main.py", content, 1, 20)
    fitted = Agent.fit_result(original, 500)
    assert len(serialize(fitted)) <= 500
    assert fitted["end_line"] < 20
    assert fitted["partial_line"] == fitted["end_line"]
    assert fitted["next_start_line"] == fitted["partial_line"]
    assert str(fitted["end_line"]) + ":" in fitted["content"]
    assert original["end_line"] == 20
    assert Agent.fit_result(original, 8000) == original
