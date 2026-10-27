"""Qualify a BYOK model using synthetic acceptance tasks, or inspect an existing JUnit.

Network/inference and fixture execution require separate explicit flags. No
provider selection or project files are changed by this runner.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from codaro.qualification import qualification
from codaro.trace import atomic_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--profile")
    source.add_argument("--junit", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--allow-execution", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    junit = args.junit or args.output.with_suffix(".xml")
    if args.profile:
        if not args.allow_model_calls:
            parser.error("Informe --allow-model-calls; somente fixtures sintéticas serão enviadas.")
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "tests/live",
            "-q",
            "--tb=short",
            "--live-provider",
            args.profile,
            "--junitxml=" + str(junit),
            "-o",
            "junit_family=legacy",
        ]
        if args.allow_execution:
            argv.append("--live-execution")
        subprocess.run(argv, cwd=Path(__file__).resolve().parents[1], timeout=7200, check=False)
    report = qualification(junit)
    atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2).encode())
    print(
        f"Relatório: {args.output} · "
        f"qualificado nas fixtures: {report['qualified_for_fixture_suite']}"
    )
    raise SystemExit(0 if report["qualified_for_fixture_suite"] else 1)


if __name__ == "__main__":
    main()
