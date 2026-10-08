"""Opt-in implementation benchmarks in disposable project copies."""

import json
import tempfile
import time
from pathlib import Path

from codaro.agent import Agent
from codaro.commands import run_command, validate_command
from codaro.features import DEFAULTS
from codaro.repository import Repository
from codaro.storage import private_read


def load_workflows(path):
    data = json.loads(private_read(path, 256_000))
    if not isinstance(data, list) or not 1 <= len(data) <= 30 or path.stat().st_size > 256_000:
        raise ValueError("Até trinta casos de implementação, em 256 KB.")
    ids = set()
    for case in data:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or not 1 <= len(case["id"]) <= 80
            or case["id"] in ids
            or not isinstance(case.get("repo"), str)
            or not isinstance(case.get("query"), str)
            or not 1 <= len(case["query"]) <= 8000
            or not isinstance(case.get("expected_files"), dict)
            or not 1 <= len(case["expected_files"]) <= 16
            or not isinstance(case.get("validation_commands"), list)
            or not 1 <= len(case["validation_commands"]) <= 8
        ):
            raise ValueError("Caso de implementação inválido.")
        for name, expectations in case["expected_files"].items():
            if (
                not isinstance(name, str)
                or Path(name).is_absolute()
                or ".." in Path(name).parts
                or not isinstance(expectations, list)
                or not all(isinstance(text, str) and len(text) <= 2000 for text in expectations)
            ):
                raise ValueError("Expectativas de arquivos inválidas.")
        for argv in case["validation_commands"]:
            validate_command(argv, 60)
        ids.add(case["id"])
    return data


def evaluate_workflows(path, provider, *, allow_execution=False):
    if not allow_execution:
        raise ValueError("Benchmark executa código: informe --allow-execution explicitamente.")
    cases = load_workflows(path)
    results = []
    for case in cases:
        started = time.monotonic()
        record = {"id": case["id"], "status": "error", "expectations_passed": False}
        try:
            source = Repository(path.parent / case["repo"])
            with tempfile.TemporaryDirectory(prefix="codaro-eval-") as temporary:
                root = Path(temporary)
                count = total = 0
                for source_path in source.files():
                    raw = source.read_bytes(source_path)
                    count += 1
                    total += len(raw)
                    if count > 2000 or total > 20_000_000:
                        raise ValueError("Fixture excede 2000 arquivos/20 MB.")
                    destination = root / source_path.relative_to(source.root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(raw)
                repository = Repository(root)
                allowed = set(case["expected_files"])
                commands = case["validation_commands"]
                agent = Agent(
                    repository,
                    provider,
                    mode="execute",
                    persist_memory=False,
                    features={
                        **DEFAULTS,
                        "artifacts": False,
                        "exploration": False,
                        "semantic_compaction": False,
                        "mcp": {},
                        "plugins": {},
                        "lsp": {"enabled": False, "servers": {}},
                    },
                    approve_edit=lambda proposals, cancelled, allowed=allowed: all(
                        proposal.path in allowed for proposal in proposals
                    ),
                    approve_command=lambda argv, timeout, cancelled, commands=commands: (
                        argv in commands
                    ),
                    max_steps=32,
                    max_seconds=180,
                )
                record["answer"] = agent.ask(case["query"])
                matched = {}
                for name, phrases in case["expected_files"].items():
                    try:
                        content = repository.read_bytes(repository.resolve_file(name)).decode(
                            "utf-8"
                        )
                        matched[name] = all(phrase in content for phrase in phrases)
                    except (OSError, ValueError):
                        matched[name] = False
                validations = [run_command(root, argv, 60) for argv in case["validation_commands"]]
                trace = json.loads(private_read(root / ".codaro/prompt.json", 2_000_000))
                iterations = trace.get("turns", [])
                record.update(
                    status="completed",
                    file_expectations=matched,
                    validations=[
                        {
                            key: result[key]
                            for key in ("argv", "exit_code", "timed_out", "duration_ms")
                        }
                        for result in validations
                    ],
                    model_calls=len(iterations),
                    input_tokens_estimate=sum(
                        item.get("budget", {}).get("input_tokens_estimate", 0)
                        for item in iterations
                    ),
                    compactions=len(trace.get("compactions", [])),
                    reported_usage=[
                        attempt["usage"]
                        for item in iterations
                        for attempt in item.get("http_attempts", [])
                        if isinstance(attempt.get("usage"), dict)
                    ],
                    cost=None,
                    expectations_passed=all(matched.values())
                    and all(
                        result["exit_code"] == 0 and not result["timed_out"]
                        for result in validations
                    ),
                )
        except (OSError, ValueError, RuntimeError) as exc:
            record["error"] = str(exc)[:1000]
        record["duration_ms"] = round((time.monotonic() - started) * 1000)
        results.append(record)
    return {
        "schema_version": 1,
        "mode": "implementation",
        "results": results,
        "passed": sum(item["expectations_passed"] for item in results),
        "total": len(results),
    }
