"""Repeatable retrieval/agent evaluations with objective, inspectable metrics."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from codaro.agent import Agent, cites_observed_lines, serialize
from codaro.index import CodeIndex
from codaro.provider import ModelError
from codaro.repository import Repository
from codaro.storage import private_json
from codaro.trace import timestamp


def load_cases(path: Path):
    with path.open("rb") as stream:
        raw = stream.read(256_001)
    if len(raw) > 256_000:
        raise ValueError("Arquivo de avaliação excede 256 KB.")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError("Arquivo de casos inválido.") from exc
    if not isinstance(data, list) or not 1 <= len(data) <= 40:
        raise ValueError("Avaliação deve conter de 1 a 40 casos.")
    ids = set()
    for case in data:
        if (
            not isinstance(case, dict)
            or not isinstance(case.get("id"), str)
            or not 1 <= len(case["id"]) <= 100
            or case["id"] in ids
            or not isinstance(case.get("repo"), str)
            or len(case["repo"]) > 2000
            or not isinstance(case.get("question"), str)
            or not 1 <= len(case["question"]) <= 8000
            or not isinstance(case.get("query"), str)
            or not 1 <= len(case["query"]) <= 1000
            or not isinstance(case.get("expected_paths"), list)
            or not case["expected_paths"]
            or len(case["expected_paths"]) > 12
            or not all(
                isinstance(item, str) and 1 <= len(item) <= 2000 for item in case["expected_paths"]
            )
            or not isinstance(case.get("answer_contains", []), list)
            or not all(
                isinstance(item, str) and len(item) <= 300
                for item in case.get("answer_contains", [])
            )
        ):
            raise ValueError("Caso de avaliação inválido.")
        ids.add(case["id"])
    return data


def answer_metrics(answer: str, evidence: list, expected: list[str], contains: list[str]):
    spans, citations = [], []
    for path in sorted({item[0] for item in evidence}, key=len, reverse=True):
        for match in re.finditer(r"(?<![\w./-])" + re.escape(path) + r":(\d{1,9})(?!\d)", answer):
            if not any(first <= match.start() < last for first, last in spans):
                spans.append(match.span())
                citations.append((path, int(match[1])))
    for match in re.finditer(r"(?<![\w./-])([\w./-]+):(\d{1,9})(?!\d)", answer):
        if ("." in match[1] or "/" in match[1] or match[1] in expected) and not any(
            first <= match.start() < last for first, last in spans
        ):
            citations.append((match[1], int(match[2])))
    valid = [
        (path, line) for path, line in citations if cites_observed_lines(f"{path}:{line}", evidence)
    ]
    cited_paths = {item[0] for item in valid}
    expected_matches = [
        path
        for path in expected
        if any(found == path or found.endswith("/" + path) for found in cited_paths)
    ]
    facts = [phrase for phrase in contains if phrase.casefold() in answer.casefold()]
    observed_paths = {item[0] for item in evidence}
    observed_matches = [
        path
        for path in expected
        if any(found == path or found.endswith("/" + path) for found in observed_paths)
    ]
    return {
        "citations": len(citations),
        "valid_citations": len(valid),
        "citation_precision": len(valid) / len(citations) if citations else 0.0,
        "expected_path_recall": len(expected_matches) / len(expected),
        "observed_path_recall": len(observed_matches) / len(expected),
        "fact_match_rate": len(facts) / len(contains) if contains else None,
        "expectations_passed": len(observed_matches) == len(expected)
        and len(facts) == len(contains)
        and len(valid) == len(citations),
    }


def evaluate(path: Path, *, provider=None, mode="retrieval"):
    if mode not in {"retrieval", "agent"} or mode == "agent" and provider is None:
        raise ValueError("Modo de avaliação inválido.")
    cases = load_cases(path)
    results = []
    for case in cases:
        started = time.monotonic()
        result = {"id": case["id"], "status": "error"}
        try:
            repository = Repository(path.parent / case["repo"])
            for name in case["expected_paths"]:
                repository.resolve_file(name)
            result["repository_root"] = str(repository.root)
            if mode == "retrieval":
                with CodeIndex(repository) as index:
                    matches = index.search(case["query"], limit=6)
                found = {item["path"] for item in matches}
                relevant = sum(item["path"] in case["expected_paths"] for item in matches)
                recall = len(found.intersection(case["expected_paths"])) / len(
                    case["expected_paths"]
                )
                result.update(
                    retrieval_precision_at_6=relevant / len(matches) if matches else 0.0,
                    expected_path_recall=recall,
                    expectations_passed=recall == 1.0,
                    matches=[
                        {key: item[key] for key in ("path", "start_line", "end_line")}
                        for item in matches
                    ],
                )
            else:
                agent = Agent(repository, provider, persist_memory=False)
                answer = agent.ask(case["question"])
                flow = private_json(repository.root / ".codaro/prompt.json", 16_000_000)
                evidence = [
                    (item["path"], item["start_line"], item["end_line"])
                    for item in flow["turns"][-1].get("evidence", [])
                ]
                result.update(
                    answer_metrics(
                        answer, evidence, case["expected_paths"], case.get("answer_contains", [])
                    )
                )
                seen = set()
                repeated = 0
                estimates, actual_input, actual_output, usage_calls, output_calls = 0, 0, 0, 0, 0
                for turn in flow["turns"]:
                    estimates += turn["budget"].get("input_tokens_estimate", 0)
                    for action in turn["tool_results"]:
                        key = serialize(
                            [action["message"].get("name"), action["normalized_arguments"]]
                        )
                        repeated += key in seen
                        seen.add(key)
                    for attempt in turn["http_attempts"]:
                        usage = attempt.get("usage")
                        if isinstance(usage, dict) and type(usage.get("prompt_tokens")) is int:
                            actual_input += usage["prompt_tokens"]
                            usage_calls += 1
                        if isinstance(usage, dict) and type(usage.get("completion_tokens")) is int:
                            actual_output += usage["completion_tokens"]
                            output_calls += 1
                result.update(
                    answer=answer,
                    model_requests=len(flow["turns"]),
                    tool_calls=sum(len(item["tool_results"]) for item in flow["turns"]),
                    repeated_calls=repeated,
                    compactions=len(flow.get("compactions", [])),
                    estimated_input_tokens=estimates,
                    reported_input_tokens=actual_input if usage_calls else None,
                    reported_output_tokens=actual_output if output_calls else None,
                    usage_requests_covered=usage_calls,
                    output_usage_requests_covered=output_calls,
                )
            result["status"] = "success"
        except (ValueError, OSError, ModelError, sqlite3.Error) as exc:
            result["error"] = str(exc)
            result["expectations_passed"] = False
        result["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        results.append(result)
    return {
        "version": 1,
        "created": timestamp(),
        "mode": mode,
        "notice": "Métricas objetivas não substituem revisão semântica humana. "
        "O modo retrieval não avalia respostas da IA.",
        "cases": results,
        "passed": sum(item["expectations_passed"] for item in results),
        "total": len(results),
    }
