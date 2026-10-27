"""Measure history restore, Ctrl+Q and session persistence without calling a model.

Run with .venv/bin/python scripts/benchmark_ui.py --output artifacts/performance/report.json.
Results are indicative wall-clock samples, not CI speed gates. All sessions and
configuration use a disposable directory; SVG captures contain only synthetic text.
"""

import argparse
import asyncio
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import textual

from codaro.agent import Agent
from codaro.llm import Settings
from codaro.repository import Repository
from codaro.sessions import SessionStore
from codaro.ui.app import CodaroApp

ANSWER = (
    "## Verificação\n\nResultado e evidências consultadas no projeto.\n\n"
    "- Primeiro ponto\n- Segundo ponto\n\n```python\nvalue = 42\nprint(value)\n```\n\n"
)


class FixtureModel:
    settings = Settings("http://127.0.0.1:11434/v1", "performance-fixture")

    def complete(self, *args, **kwargs):
        raise AssertionError("Performance benchmarks must never call a model.")


class MeasuredApp(CodaroApp):
    CSS_PATH = Path(sys.modules[CodaroApp.__module__].__file__).with_name("styles.tcss")

    async def action_quit(self):
        self.quit_dispatched = time.perf_counter()
        await super().action_quit()

    def on_unmount(self):
        started = time.perf_counter()
        super().on_unmount()
        self.cleanup_seconds = time.perf_counter() - started


def turns(count, answer):
    return [
        [{"role": "user", "content": f"Pergunta {i}"}, {"role": "assistant", "content": answer}]
        for i in range(count)
    ]


async def measure_ui(root, count, blocks, output):
    project = root / f"project-{count}-{blocks}"
    project.mkdir()
    (project / "demo.py").write_text("x = 1\n", encoding="utf-8")
    store = SessionStore(project)
    store.save(turns(count, ANSWER * blocks), "fixture")
    app = MeasuredApp(Agent(Repository(project), FixtureModel()), resume=bool(count))
    started = time.perf_counter()
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        ready = time.perf_counter()
        nodes = len(app._registry)
        (output.parent / f"history-{count}-{blocks}.svg").write_text(
            app.export_screenshot(), encoding="utf-8"
        )
        requested = time.perf_counter()
        await pilot.press("ctrl+q")
    ended = time.perf_counter()
    return {
        "turns": count,
        "blocks": blocks,
        "nodes": nodes,
        "ready_ms": round((ready - started) * 1000, 1),
        "quit_dispatch_ms": round((app.quit_dispatched - requested) * 1000, 1),
        "quit_total_ms": round((ended - requested) * 1000, 1),
        "cleanup_ms": round(app.cleanup_seconds * 1000, 1),
    }


def loop_factory():
    loop = asyncio.new_event_loop()

    def heartbeat():
        if not loop.is_closed():
            loop.call_later(0.02, heartbeat)

    # Restricted test runners may not wake asyncio through its cross-thread socket.
    loop.call_later(0.02, heartbeat)
    return loop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/performance/report.json"))
    output = parser.parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "environment": {
            "python": platform.python_version(),
            "textual": textual.__version__,
            "platform": platform.platform(),
            "terminal_size": [120, 35],
            "model_requests": 0,
        },
        "ui": [],
        "storage": [],
    }
    with tempfile.TemporaryDirectory(prefix="codaro-performance-") as directory:
        root = Path(directory)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "settings")}):
            with asyncio.Runner(loop_factory=loop_factory) as runner:
                for count, blocks in ((0, 0), (10, 8), (30, 8), (30, 40)):
                    result = runner.run(measure_ui(root, count, blocks, output))
                    report["ui"].append(result)
                    print(json.dumps(result), flush=True)
            for chars in (500, 8000, 60_000):
                project = root / f"storage-{chars}"
                project.mkdir()
                store = SessionStore(project)
                history = turns(50, "x" * chars)
                started = time.perf_counter()
                store.save(history, "fixture")
                result = {
                    "input_chars": chars * 50,
                    "retained_turns": len(store.turns),
                    "save_ms": round((time.perf_counter() - started) * 1000, 1),
                    "stored_bytes": store.path.stat().st_size,
                }
                report["storage"].append(result)
                print(json.dumps(result), flush=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
