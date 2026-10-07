import json

from test_agent import FakeModel, call

from codaro.evaluation import answer_metrics, evaluate, load_cases


def dataset(tmp_path):
    (tmp_path / "auth.py").write_text("def can_edit(user):\n    return user.is_admin\n")
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "auth",
                    "repo": ".",
                    "query": "can_edit",
                    "question": "Quem pode editar? Cite o arquivo e linha.",
                    "expected_paths": ["auth.py"],
                    "answer_contains": ["is_admin"],
                }
            ]
        )
    )
    return path


def test_evaluation_retrieval_uses_real_index_and_does_not_call_model(tmp_path):
    report = evaluate(dataset(tmp_path))
    assert report["passed"] == report["total"] == 1
    assert report["mode"] == "retrieval"
    assert report["cases"][0]["expected_path_recall"] == 1
    assert "estimated_input_tokens" not in report["cases"][0]


def test_evaluation_agent_records_objective_metrics_without_polluting_memory(tmp_path):
    model = FakeModel(
        [
            call("read_lines", {"path": "auth.py", "start": 1, "end": 2}),
            {"content": "auth.py:2 verifica user.is_admin."},
        ]
    )
    report = evaluate(dataset(tmp_path), mode="agent", provider=model)
    result = report["cases"][0]
    assert report["passed"] == 1
    assert result["valid_citations"] == 1
    assert result["model_requests"] == 2
    assert result["tool_calls"] == 1
    assert result["estimated_input_tokens"] > 0
    assert result["reported_input_tokens"] is None
    assert not (tmp_path / ".codaro/memory.sqlite3").exists()


def test_evaluation_does_not_accept_fabricated_citations_or_facts():
    assert not answer_metrics(
        "other.py:1 returns True.", [("auth.py", 1, 2)], ["auth.py"], ["is_admin"]
    )["expectations_passed"]
    assert not answer_metrics(
        "auth.py:99 is_admin.", [("auth.py", 1, 2)], ["auth.py"], ["is_admin"]
    )["expectations_passed"]


def test_evaluation_reports_one_failure_and_continues_remaining_cases(tmp_path):
    path = dataset(tmp_path)
    good = load_cases(path)[0]
    path.write_text(json.dumps([{**good, "id": "missing", "expected_paths": ["missing.py"]}, good]))
    report = evaluate(path)
    assert report["passed"] == 1 and report["total"] == 2
    assert report["cases"][0]["status"] == "error"
    assert report["cases"][1]["status"] == "success"


def test_citation_metrics_support_spaces_and_ignore_http_status_numbers():
    result = answer_metrics(
        "src/my module.py:2 trata HTTP:400.", [("src/my module.py", 1, 3)], ["src/my module.py"], []
    )
    assert result["expectations_passed"] and result["citations"] == 1
