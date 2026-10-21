import json
import sys
from pathlib import Path

import pytest
from test_agent import FakeModel

from codaro.agent import Agent
from codaro.agent.messages import serialize
from codaro.agent.results import ResultBudgetError, fit_result
from codaro.agent.synthesis import synthesis_messages
from codaro.commands import run_command
from codaro.llm import OllamaMemoryError, Settings
from codaro.repository import Repository
from codaro.trace import PromptFlow
from codaro.trace_metrics import evaluation_metrics


def evidence(messages):
    return json.loads(messages[1]["content"].split("\n", 2)[1])


def test_recovery_preserves_previous_answer_reference(tmp_path):
    model = FakeModel([{"content": None}, {"content": "Continuação."}])
    agent = Agent(Repository(tmp_path), model)
    agent.turns = [
        [
            {"role": "user", "content": "Explique o decoder."},
            {"role": "assistant", "content": "Consulte FRAME_DECODER."},
        ]
    ]
    agent.ask("Continue a resposta")
    messages = model.requests[-1][0]
    assert "FRAME_DECODER" in serialize(messages)
    assert "Continue a resposta" in messages[-1]["content"]
    assert all(m["role"] != "tool" for m in messages)


def test_synthesis_preserves_command_error_tail_and_marks_excerpt():
    result = {"output": "A" * 2000 + "\nERROR_AT_END", "exit_code": 1, "truncated": False}
    messages = synthesis_messages(
        "Falha?",
        [{"role": "tool", "name": "run_command", "content": serialize(result)}],
        guidance=[],
        bootstrap="",
        budget=4000,
    )
    sent = evidence(messages)["observed_results"][0]["result"]
    assert "ERROR_AT_END" in sent["output"]
    assert sent["exit_code"] == 1
    assert sent["truncated"] and sent["synthesis_excerpt_only"]
    assert len(sent["output"]) <= 1200


def test_synthesis_fits_part_of_search_instead_of_discarding_every_match():
    result = {
        "results": [
            {
                "path": f"src/module_{i}.py",
                "start_line": 1,
                "end_line": 50,
                "signature": "def process(" + "arg," * 30 + "):",
                "preview": "x" * 240,
            }
            for i in range(12)
        ]
    }
    messages = synthesis_messages(
        "Analise",
        [{"role": "tool", "name": "search_code", "content": serialize(result)}],
        guidance=[],
        bootstrap="",
        budget=2000,
    )
    payload = evidence(messages)
    assert len(serialize(payload)) <= 2000
    assert payload["observed_results"]
    assert 0 < len(payload["observed_results"][0]["result"]["results"]) < 12


def test_line_excerpt_reports_only_the_visible_range():
    result = {
        "path": "app.py",
        "start_line": 1,
        "end_line": 100,
        "content": "\n".join(f"{i}: " + "x" * 60 for i in range(1, 101)),
        "truncated": False,
    }
    messages = synthesis_messages(
        "Leia",
        [{"role": "tool", "name": "read_lines", "content": serialize(result)}],
        guidance=[],
        bootstrap="",
        budget=4000,
    )
    sent = evidence(messages)["observed_results"][0]["result"]
    assert sent["end_line"] == len(sent["content"].splitlines())
    assert sent["end_line"] < sent["source_end_line"] == 100
    assert sent["partial_line"] == sent["next_start_line"] == sent["end_line"]
    assert sent["truncated"]


@pytest.mark.parametrize("budget", [0, 1, 10, 30, 100, 300])
def test_result_budget_is_a_strict_contract(budget):
    result = {"results": [{"preview": "x" * 1000}]}
    try:
        fitted = fit_result(result, budget)
    except ResultBudgetError:
        assert budget < len(serialize({"error": "budget"}))
    else:
        assert len(serialize(fitted)) <= budget


def test_terminal_storage_error_is_not_misdiagnosed_as_prior_empty_response(tmp_path):
    flow = PromptFlow(
        tmp_path, "question", Settings("http://localhost/v1", "test"), allow_edits=False, limits={}
    )
    flow.add_turn({"messages": []}, {})
    flow.decision("empty_response")
    flow.finish("error", error=OSError("Synthetic storage failure"))
    assert flow.data["diagnosis"]["reason"] == "error"
    assert flow.data["diagnosis"]["termination"] == "OSError"
    assert flow.data["diagnosis"]["empty_response_attempts"] == 1


def test_effective_settings_follow_ollama_memory_recovery(tmp_path):
    class Model(FakeModel):
        settings = Settings(
            "http://localhost:11434/v1", "test", api_style="ollama", context_window=32768
        )

        def complete(self, messages, tools=None):
            self.requests.append((list(messages), tools))
            if len(self.requests) == 1:
                raise OllamaMemoryError("Memory pressure")
            return {"content": "OK"}

    model = Model([])
    Agent(Repository(tmp_path), model).ask("Olá")
    trace = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert trace["initial_settings"]["context_window"] == 32768
    assert trace["effective_settings"]["context_window"] == model.settings.context_window == 16384
    assert trace["turns"][-1]["effective_settings"]["context_window"] == 16384
    assert trace["effective_limits"]["output_reserve_tokens"] == 4096


def test_totals_survive_snapshot_rotation_without_losing_usage(tmp_path, monkeypatch):
    monkeypatch.setattr("codaro.trace.MAX_TRACE_BYTES", 5000)
    flow = PromptFlow(
        tmp_path, "question", Settings("http://localhost/v1", "test"), allow_edits=False, limits={}
    )
    for _ in range(10):
        flow.add_turn(
            {"messages": [{"role": "user", "content": "x" * 1200}]}, {"input_tokens_estimate": 100}
        )
        flow.turn["http_attempts"].append({})
        flow.capture("usage", {"prompt_tokens": 100, "completion_tokens": 5})
    flow.finish("success", answer="OK")
    data = json.loads(flow.path.read_text())
    assert data["older_turns_in_archive"]
    totals = evaluation_metrics(data)
    assert totals["model_requests"] == 10
    assert totals["reported_input_tokens"] == 1000
    assert totals["reported_output_tokens"] == 50
    assert totals["metrics_complete"]


def test_legacy_pruned_metrics_are_marked_incomplete():
    assert not evaluation_metrics({"older_turns_in_archive": True, "turns": []})["metrics_complete"]


def test_command_environment_filters_secrets_with_explicit_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-value")
    monkeypatch.setenv("CODARO_API_KEY", "synthetic-value")
    monkeypatch.delenv("CODARO_COMMAND_ENV", raising=False)
    code = (
        "import os; print(','.join(k for k in "
        "('OPENAI_API_KEY','ANTHROPIC_API_KEY','CODARO_API_KEY') if k in os.environ))"
    )
    assert run_command(tmp_path, [sys.executable, "-c", code])["output"].strip() == ""
    monkeypatch.setenv("CODARO_COMMAND_ENV", "OPENAI_API_KEY")
    assert run_command(tmp_path, [sys.executable, "-c", code])["output"].strip() == "OPENAI_API_KEY"


def test_required_sandbox_never_falls_back_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("CODARO_SANDBOX", "required")
    monkeypatch.setattr("codaro.command_sandbox.shutil.which", lambda _: None)
    with pytest.raises(ValueError, match="Sandbox obrigatória indisponível"):
        run_command(tmp_path, [sys.executable, "-c", "raise AssertionError('must not run')"])


def test_allowed_environment_secrets_are_masked_in_captured_output(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-value")
    monkeypatch.setenv("CODARO_COMMAND_ENV", "OPENAI_API_KEY")
    result = run_command(
        tmp_path, [sys.executable, "-c", "import os; print(os.environ['OPENAI_API_KEY'])"]
    )
    assert result["output"].strip() == "[REDACTED]"


def test_usage_totals_update_cumulative_events_without_double_counting(tmp_path):
    flow = PromptFlow(
        tmp_path, "question", Settings("http://localhost/v1", "test"), allow_edits=False, limits={}
    )
    flow.add_turn({"messages": []}, {})
    flow.turn["http_attempts"].append({})
    flow.capture("usage", {"prompt_tokens": 100, "completion_tokens": 2})
    flow.capture("usage", {"prompt_tokens": 100, "completion_tokens": 5})
    totals = evaluation_metrics(flow.data)
    assert totals["reported_input_tokens"] == 100
    assert totals["reported_output_tokens"] == 5
    assert totals["usage_requests_covered"] == 1


@pytest.mark.parametrize("budget", [128, 256, 512, 4000])
def test_synthesis_envelope_fits_small_budgets(budget):
    messages = synthesis_messages(
        "Continue",
        [],
        guidance=[],
        bootstrap="",
        budget=budget,
        history=[[{"role": "assistant", "content": "A" * 2000}]] * 2,
        summary="S" * 5000,
        objective="O" * 400,
    )
    assert len(serialize(evidence(messages))) <= budget


def test_linux_sandbox_isolates_external_files_and_preserves_project_writes(tmp_path, monkeypatch):
    import shutil

    if not sys.platform.startswith("linux") or shutil.which("bwrap") is None:
        pytest.skip("Bubblewrap em Linux necessário")
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("must stay private")
    monkeypatch.setenv("CODARO_SANDBOX", "required")
    code = (
        "from pathlib import Path; "
        f"assert not Path({str(outside)!r}).exists(); "
        "Path('inside.txt').write_text('allowed'); print('isolated')"
    )
    result = run_command(project, [str(Path(sys.executable).resolve()), "-c", code])
    if result["exit_code"] != 0 and any(
        text in result["output"].lower()
        for text in (
            "operation not permitted",
            "creating new namespace failed",
            "permission denied",
        )
    ):
        assert not (project / "inside.txt").exists()
        pytest.skip("Kernel do executor bloqueia namespaces; sandbox não executou o comando")
    assert result["exit_code"] == 0, result["output"]
    assert result["sandbox"] == "bubblewrap"
    assert (project / "inside.txt").read_text() == "allowed"
    assert outside.read_text() == "must stay private"
