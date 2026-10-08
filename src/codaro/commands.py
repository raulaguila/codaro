"""Approved commands run without a shell, with bounded output and process cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from codaro.index import safe_preview
from codaro.llm import RequestCancelled
from codaro.runtime import remaining_seconds, request_artifacts


def validate_command(argv: list[str], timeout: int):
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 40
        or not all(
            isinstance(arg, str)
            and len(arg) <= 2000
            and not any(ord(c) < 32 or ord(c) == 127 for c in arg)
            for arg in argv
        )
        or not argv[0]
        or sum(map(len, argv)) > 8000
        or type(timeout) is not int
        or not 1 <= timeout <= 300
    ):
        raise ValueError("Use 1–40 argumentos válidos e timeout de 1–300 segundos.")


def run_command(root: Path, argv: list[str], timeout=60, cancelled=None) -> dict:
    validate_command(argv, timeout)
    cancelled = cancelled or threading.Event()
    if cancelled.is_set():
        raise RequestCancelled("Comando cancelado.")
    remaining = remaining_seconds()
    if remaining is not None:
        timeout = min(timeout, remaining)
    started = time.monotonic()
    # A pipe drained by a reader thread avoids unbounded disk writes and pipe deadlocks.
    captured = bytearray()
    store = request_artifacts.get()
    capture_limit = 2_000_000 if store else 12_000
    total = 0

    def consume(pipe):
        nonlocal total
        with pipe:
            while chunk := pipe.read(4096):
                total += len(chunk)
                captured.extend(chunk[: max(0, capture_limit - len(captured))])

    with tempfile.TemporaryFile() as empty_input:
        process = subprocess.Popen(
            argv,
            cwd=root,
            stdin=empty_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={key: value for key, value in os.environ.items() if key != "CODARO_API_KEY"},
            start_new_session=os.name == "posix",
        )
    reader = threading.Thread(target=consume, args=(process.stdout,), daemon=True)
    reader.start()
    timed_out = False
    try:
        while process.poll() is None:
            if cancelled.wait(0.05) or time.monotonic() - started >= timeout:
                timed_out = not cancelled.is_set()
                break
    finally:
        # Also stop children that kept stdout open after their parent exited.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=2)
    if cancelled.is_set():
        raise RequestCancelled("Comando cancelado.")
    artifact = None
    if store and total > 8000:
        try:
            artifact = store.save(
                bytes(captured).decode("utf-8", errors="replace"),
                source="run_command",
                complete=total <= capture_limit,
            )
        except (OSError, ValueError):
            # Persistence failure cannot erase the receipt of an executed command.
            pass
    return {
        **(
            {"artifact_id": artifact["id"], "artifact_complete": artifact["complete"]}
            if artifact
            else {}
        ),
        "argv": argv,
        "cwd": str(root),
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "output": safe_preview(bytes(captured).decode("utf-8", errors="replace"))[:8000],
        "truncated": total > 8000,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
