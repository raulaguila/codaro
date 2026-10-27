"""Observable run phases and progress measured by new evidence, not wording."""

from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from functools import wraps

from codaro.trace import current_flow


class Phase(StrEnum):
    PREPARING = "preparing"
    REQUESTING = "requesting_model"
    TOOLS = "executing_tools"
    COMPACTING = "compacting"
    APPROVAL = "awaiting_approval"
    USER = "awaiting_user"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class TurnProgress:
    def __init__(self):
        self.phase = None
        self.started = time.monotonic()
        self.seen = set()
        self.recent = deque(maxlen=8)
        self.flat = 0

    def transition(self, phase):
        if not isinstance(phase, Phase):
            raise ValueError("Fase de execução inválida.")
        if self.phase == phase:
            return
        if self.phase in {Phase.COMPLETED, Phase.INTERRUPTED}:
            raise ValueError("Uma execução encerrada não pode reiniciar.")
        if self.phase is None and phase != Phase.PREPARING:
            raise ValueError("A execução deve começar em preparação.")
        if phase == Phase.PREPARING and self.phase not in {None, Phase.APPROVAL, Phase.USER}:
            raise ValueError("Preparação não pode reiniciar no meio da execução.")
        self.phase = phase
        if flow := current_flow.get():
            flow.append_event(
                "phase_transition",
                {
                    "phase": phase.value,
                    "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                },
            )

    def observe(self, name, args, result):
        if name in {
            "get_context_status",
            "request_tools",
            "get_tools_catalog",
            "get_command_output",
        }:
            return False
        if name in {
            "search_code",
            "grep_code",
            "find_references",
            "find_definition",
            "list_files",
            "get_file_outline",
        }:
            payload = result.get("results", result.get("files", []))
            if isinstance(payload, list):
                # Ranking scores and snippets may change when the query is reworded;
                # the same source locations still provide no additional evidence.
                payload = [
                    {
                        key: item[key]
                        for key in ("path", "file_hash", "start_line", "end_line", "symbol", "name")
                        if key in item
                    }
                    if isinstance(item, dict)
                    else item
                    for item in payload
                ]
        elif result.get("content"):
            payload = [
                result.get("path"),
                result.get("file_hash"),
                result.get("start_line"),
                result.get("end_line"),
                result["content"],
            ]
        elif result.get("already_read"):
            payload = "already_read"
        else:
            payload = [
                name,
                args,
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"duration_ms", "retrieval", "at"}
                },
            ]
        key = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        repeated = key in self.seen or payload == [] or payload == "already_read"
        self.flat = self.flat + 1 if repeated else 0
        self.seen.add(key)
        self.recent.append({"tool": name, "new_evidence": not repeated})
        if len(self.seen) > 512:
            self.seen = {key}
        if flow := current_flow.get():
            flow.data["progress"] = {"flat_steps": self.flat, "recent": list(self.recent)}
        return self.flat == 3


active_progress = ContextVar("codaro_progress", default=None)


@contextmanager
def waiting(phase):
    """Approval/question waits are explicit and always restore the previous phase."""
    state = active_progress.get()
    previous = state.phase if state else None
    if state:
        state.transition(phase)
    try:
        yield
    finally:
        if state and state.phase not in {Phase.COMPLETED, Phase.INTERRUPTED}:
            state.transition(previous)


def tracked_run(function):
    @wraps(function)
    def run(*args, **kwargs):
        state = TurnProgress()
        token = active_progress.set(state)
        state.transition(Phase.PREPARING)
        try:
            result = function(*args, **kwargs)
        except BaseException:
            state.transition(Phase.INTERRUPTED)
            raise
        else:
            state.transition(Phase.COMPLETED)
            return result
        finally:
            active_progress.reset(token)

    return run
