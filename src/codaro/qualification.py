"""Evidence-based model qualification from opt-in synthetic acceptance runs."""

import json
import math
import xml.etree.ElementTree as ET

from codaro.storage import private_read

REQUIRED = {
    "test_catalog_and_metadata",
    "test_streaming_keeps_reasoning_separate",
    "test_native_tool_roundtrip",
    "test_agent_reads_repository_before_analysis",
    "test_history_recall_after_removing_recent_turns",
    "test_confirmed_decision_reaches_model_after_restart",
    "test_large_source_is_read_in_pages",
    "test_implementation_and_validation",
    "test_multiple_decisions_survive_compaction_and_restart",
}


def qualification(path):
    raw = private_read(path, 8_000_000)
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise ValueError("Relatório XML não pode declarar entidades.")
    root = ET.fromstring(raw)
    cases = []
    identity = {}
    for case in root.iter("testcase"):
        state = "passed"
        if case.find("failure") is not None or case.find("error") is not None:
            state = "failed"
        elif case.find("skipped") is not None:
            state = "skipped"
        record = {
            "name": case.get("name", ""),
            "state": state,
            "seconds": float(case.get("time", 0)),
        }
        if not math.isfinite(record["seconds"]) or record["seconds"] < 0:
            raise ValueError("Duração inválida no relatório.")
        for prop in case.findall("./properties/property"):
            if prop.get("name") in {"model_id", "api_style"}:
                identity[prop.get("name")] = prop.get("value", "")[:200]
            if prop.get("name") == "metrics":
                record["metrics"] = {
                    key: value
                    for key, value in json.loads(prop.get("value", "{}")).items()
                    if type(value) in (int, float, bool) or value is None
                }
            if prop.get("name") == "workflows":
                data = json.loads(prop.get("value", "{}"))
                record["workflows"] = {key: data.get(key) for key in ("passed", "total")}
        cases.append(record)
    passed = {case["name"].split("[")[0] for case in cases if case["state"] == "passed"}
    implementation = next(
        (case for case in cases if case["name"].startswith("test_implementation_and_validation")),
        {},
    )
    workflows = implementation.get("workflows", {})
    complete = (
        not (REQUIRED - passed)
        and bool(cases)
        and all(case["state"] == "passed" for case in cases)
        and bool(identity.get("model_id"))
        and identity.get("api_style") in {"openai", "ollama", "anthropic"}
        and workflows.get("total", 0) >= 5
        and workflows.get("passed") == workflows.get("total")
    )
    return {
        "schema_version": 1,
        "identity": identity,
        "cost": None,
        "qualified_for_fixture_suite": bool(complete),
        "missing_or_unsuccessful": sorted(REQUIRED - passed),
        "cases": cases,
        "passed": sum(case["state"] == "passed" for case in cases),
        "failed": sum(case["state"] == "failed" for case in cases),
        "skipped": sum(case["state"] == "skipped" for case in cases),
        "scope": "synthetic fixtures only; not a guarantee for arbitrary projects",
    }
