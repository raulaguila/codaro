"""Replay approved edits through the real agent and execute every benchmark's tests.

Scripted responses verify orchestration and fixture contracts, not model quality.
The same datasets run against real models in the opt-in acceptance suite.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_agent import FakeModel, call

from codaro.workflow_evaluation import evaluate_workflows, load_workflows

ROOT = Path(__file__).resolve().parents[1]
CASES = load_workflows(ROOT / "evaluations/workflows.json")
SOLUTIONS = {
    "go-clamp-bounds": (
        "package bounds\n\nfunc Clamp(value, low, high int) int {\n"
        "    if value < low { return low }; if value > high { return high }; return value\n}\n"
    ),
    "node-slug-whitespace": (
        "export function slug(value) {\n"
        "    return value.trim().toLowerCase().replace(/\\s+/g, '-');\n}\n"
    ),
    "authorization-null-and-editor": (
        "def can_edit(user):\n"
        "    return isinstance(user, dict) and user.get('role') in {'admin', 'editor'}\n"
    ),
    "pagination-offset-validation": (
        "def page(items, offset=0, limit=20):\n"
        "    if type(offset) is not int or type(limit) is not int:\n"
        "        raise TypeError('integer required')\n"
        "    if offset < 0 or limit < 0:\n"
        "        raise ValueError('negative pagination')\n"
        "    return items[offset:offset + limit]\n"
    ),
    "settings-preserve-falsy-and-inputs": (
        "def merge_settings(defaults, overrides):\n"
        "    return {**defaults, **{k: v for k, v in overrides.items() if v is not None}}\n"
    ),
}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_benchmark_contracts_execute_through_agent_in_disposable_copy(tmp_path, case):
    if shutil.which(case["validation_commands"][0][0]) is None:
        pytest.skip("Runtime da fixture não instalado.")
    if case["validation_commands"][0][0] == "go":
        result = subprocess.run(["go", "version"], capture_output=True, text=True, timeout=10)
        if result.returncode or not result.stdout.startswith("go version go"):
            pytest.skip("Go SDK não instalado (outro programa chamado go no PATH).")
    source = ROOT / "evaluations" / case["repo"]
    path = next(iter(case["expected_files"]))
    original = (source / path).read_text()
    dataset = tmp_path / "cases.json"
    dataset.write_text(json.dumps([{**case, "repo": str(source)}]))
    steps = [{"title": "Corrigir implementação e executar os testes", "state": "doing"}]
    actions = [
        ("update_plan", {"steps": steps, "criteria": ["Testes aprovados"]}),
        ("read_lines", {"path": path, "start": 1, "end": 20}),
        ("run_command", {"argv": case["validation_commands"][0], "purpose": "validation"}),
        (
            "apply_changes",
            {
                "reason": "Corrigir comportamento coberto pelos testes",
                "operations": [
                    {
                        "kind": "edit",
                        "path": path,
                        "old_text": original,
                        "new_text": SOLUTIONS[case["id"]],
                    }
                ],
            },
        ),
        ("run_command", {"argv": case["validation_commands"][0], "purpose": "validation"}),
        (
            "update_plan",
            {"steps": [{**steps[0], "state": "done"}], "criteria": ["Testes aprovados"]},
        ),
        ("finish_task", {"status": "completed", "summary": "Implementação corrigida e validada"}),
    ]
    model = FakeModel(
        [
            *(call(name, args, f"step-{i}") for i, (name, args) in enumerate(actions)),
            {"content": "Implementação corrigida e testes aprovados."},
        ]
    )
    report = evaluate_workflows(dataset, model, allow_execution=True)
    assert report["passed"] == report["total"] == 1, report
    results = [
        json.loads(message["content"])
        for messages, _ in model.requests
        for message in messages
        if message["role"] == "tool"
    ]
    assert any(result.get("exit_code") not in (None, 0) for result in results)
    assert any(result.get("exit_code") == 0 for result in results)
    assert (source / path).read_text() == original
