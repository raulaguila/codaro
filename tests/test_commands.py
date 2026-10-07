import json
import sys
import threading
import time

import pytest

from codaro.agent import Agent
from codaro.commands import run_command, validate_command
from codaro.index import CodeIndex
from codaro.provider import RequestCancelled
from codaro.repository import Repository


def test_command_runs_in_project_without_shell_and_caps_output(tmp_path, monkeypatch):
    monkeypatch.setenv("CODARO_API_KEY", "private-key")
    script = "import os; print(os.getcwd()); print(os.getenv('CODARO_API_KEY')); print('x'*30000)"
    result = run_command(tmp_path, [sys.executable, "-c", script])
    assert result["exit_code"] == 0
    assert str(tmp_path) in result["output"]
    assert "private-key" not in result["output"]
    assert result["truncated"]
    assert len(result["output"]) <= 8000
    literal = run_command(
        tmp_path, [sys.executable, "-c", "import sys; print(sys.argv[1])", "$(touch bad)"]
    )
    assert "$(touch bad)" in literal["output"]
    assert not (tmp_path / "bad").exists()


def test_command_timeout_and_cancellation_stop_running_process(tmp_path):
    script = "import time; print('started', flush=True); time.sleep(20)"
    result = run_command(tmp_path, [sys.executable, "-c", script], timeout=1)
    assert result["timed_out"]
    assert result["exit_code"] != 0
    cancelled = threading.Event()
    timer = threading.Timer(0.15, cancelled.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(RequestCancelled):
            run_command(tmp_path, [sys.executable, "-c", script], cancelled=cancelled)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3


@pytest.mark.parametrize(
    "argv,timeout", [([], 1), (["x\n"], 1), ([1], 1), (["x"], True), (["x"], 301)]
)
def test_invalid_command_arguments(argv, timeout):
    with pytest.raises(ValueError):
        validate_command(argv, timeout)


def test_agent_native_command_protocol_approval_and_denial(tmp_path):
    from test_agent import FakeModel, call

    argv = [sys.executable, "-c", "print('verified')"]
    for approved in (False, True):
        decisions = []

        def approval(arguments, timeout, cancelled, decisions=decisions, approved=approved):
            decisions.append((arguments, timeout))
            return approved

        model = FakeModel([call("run_command", {"argv": argv}), {"content": "Resultado recebido."}])
        agent = Agent(Repository(tmp_path), model, approve_command=approval)
        agent.ask("Execute a validação.")
        assert decisions == [(argv, 60)]
        output = next(item for item in model.requests[-1][0] if item["role"] == "tool")
        result = json.loads(output["content"])
        assert ("exit_code" in result) == approved
        if approved:
            assert result["output"].strip() == "verified"
        else:
            assert "rejeitado" in result["error"]
        flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
        assert flow["turns"][0]["tool_results"][0]["result"] == result
    with CodeIndex(Repository(tmp_path)) as index:
        readonly = Agent(Repository(tmp_path), FakeModel([]))
        with pytest.raises(ValueError, match="desabilitada"):
            readonly.execute(index, "run_command", {"argv": argv})


def test_command_output_fitting_preserves_execution_status(tmp_path):
    result = run_command(tmp_path, [sys.executable, "-c", "print('x'*30000)"])
    fitted = Agent.fit_result(result, 1000)
    assert "error" not in fitted
    assert fitted["exit_code"] == 0
    assert fitted["truncated"]
    assert len(json.dumps(fitted, ensure_ascii=False, separators=(",", ":"))) <= 1000


def test_pending_diff_cannot_be_mistaken_for_validated_current_code(tmp_path):
    from test_agent import FakeModel, call, edit_responses

    (tmp_path / "code.py").write_text("x = 1\n")
    approvals = []
    model = FakeModel(
        [
            *edit_responses()[:-1],
            call("run_command", {"argv": [sys.executable, "-c", "print('OK')"]}, "validate"),
            {"content": "Aguarda revisão; testes não executados."},
        ]
    )
    agent = Agent(
        Repository(tmp_path),
        model,
        allow_edits=True,
        approve_command=lambda *args: approvals.append(args) or True,
    )
    agent.ask("Mude x e valide.")
    assert not approvals
    result = json.loads(
        [item for item in model.requests[-1][0] if item["role"] == "tool"][-1]["content"]
    )
    assert "propostas pendentes" in result["error"]
    assert (tmp_path / "code.py").read_text() == "x = 1\n"
