"""Discover declared checks as data and connect criteria to revision-bound receipts."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tomllib
from pathlib import PurePosixPath


def discover_checks(repository, path=None):
    from codaro.retrieval import scoped_files

    files = scoped_files(repository, path=path) if path else repository.files()
    names = {file.relative_to(repository.root).as_posix() for file in files}
    checks = []

    def add(manifest, argv, category):
        cwd = str(PurePosixPath(manifest).parent)
        identifier = hashlib.sha256(json.dumps([cwd, argv]).encode()).hexdigest()[:16]
        if any(item["id"] == identifier for item in checks):
            return
        checks.append(
            {
                "id": identifier,
                "argv": argv,
                "cwd": cwd,
                "category": category,
                "source": manifest,
                "available": bool(shutil.which(argv[0])),
                "notice": "Comando declarado/candidato; exige aprovação antes de executar.",
            }
        )

    for name in sorted(names):
        base = PurePosixPath(name).name.lower()
        try:
            if base == "package.json":
                data = json.loads(repository.read_text(name))
                scripts = data.get("scripts", {})
                if isinstance(scripts, dict):
                    for script in ("test", "lint", "typecheck", "check", "build"):
                        if isinstance(scripts.get(script), str):
                            add(name, ["npm", "run", script], script)
            elif base == "pyproject.toml":
                data = tomllib.loads(repository.read_text(name))
                tool = data.get("tool", {})
                parent = str(PurePosixPath(name).parent)
                prefix = "" if parent == "." else parent + "/"
                if "pytest" in tool or any(item.startswith(prefix + "tests/") for item in names):
                    add(name, [sys.executable, "-m", "pytest", "-q"], "test")
                if "ruff" in tool:
                    add(name, [sys.executable, "-m", "ruff", "check", "."], "lint")
                if "mypy" in tool:
                    add(name, [sys.executable, "-m", "mypy", "."], "typecheck")
            elif base == "go.mod":
                add(name, ["go", "test", "./..."], "test")
                add(name, ["go", "vet", "./..."], "lint")
            elif base == "cargo.toml":
                add(name, ["cargo", "test"], "test")
                add(name, ["cargo", "check"], "build")
            elif base == "pom.xml":
                add(name, ["mvn", "test"], "test")
            elif base in {"build.gradle", "build.gradle.kts"}:
                add(name, ["gradle", "test"], "test")
            elif base.endswith((".sln", ".csproj", ".fsproj")):
                add(name, ["dotnet", "test", PurePosixPath(name).name], "test")
                add(name, ["dotnet", "build", PurePosixPath(name).name], "build")
        except (ValueError, OSError, TypeError, AttributeError):
            continue
        if len(checks) >= 60:
            break
    return checks[:60]


def validate_bindings(criteria, bindings):
    if not isinstance(bindings, list) or len(bindings) > 16:
        raise ValueError("Até dezesseis vínculos de critério/checagem.")
    seen = set()
    for item in bindings:
        if not isinstance(item, dict) or set(item) != {"criterion", "check_ids"}:
            raise ValueError("Vínculo exige criterion e check_ids.")
        if item["criterion"] not in criteria or item["criterion"] in seen:
            raise ValueError("Critério ausente ou repetido no vínculo.")
        seen.add(item["criterion"])
        if (
            not isinstance(item["check_ids"], list)
            or not 1 <= len(item["check_ids"]) <= 8
            or not all(isinstance(key, str) and len(key) == 16 for key in item["check_ids"])
        ):
            raise ValueError("Identificadores de checagem inválidos.")


def validation_report(task):
    if not task:
        return {"status": "unavailable", "criteria": []}
    bindings = {item["criterion"]: item["check_ids"] for item in task.get("criterion_checks", [])}
    latest = {}
    for result in task["validations"]:
        if result["revision"] == task["revision"] and result.get("check_id"):
            latest[result["check_id"]] = result
    criteria = []
    for criterion in task["criteria"]:
        keys = bindings.get(criterion, [])
        status = "unavailable" if not keys else "pending"
        if keys and all(key in latest for key in keys):
            status = (
                "passed"
                if all(
                    latest[key]["exit_code"] == 0 and not latest[key]["timed_out"] for key in keys
                )
                else "failed"
            )
        criteria.append({"criterion": criterion, "status": status, "check_ids": keys})
    passed = sum(item["status"] == "passed" for item in criteria)
    status = (
        "failed"
        if any(item["status"] == "failed" for item in criteria)
        else "validated"
        if criteria and passed == len(criteria)
        else "partial"
        if passed or task["validations"]
        else "unavailable"
    )
    return {"status": status, "revision": task["revision"], "criteria": criteria}
