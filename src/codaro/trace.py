"""Local, always-on recording of the last agent investigation."""

from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

current_flow: ContextVar[PromptFlow | None] = ContextVar("codaro_prompt_flow", default=None)


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def snapshot(value):
    try:
        return copy.deepcopy(value)
    except RecursionError:
        # The original argument string / HTTP body remains available for diagnosis.
        return {"trace_omitted": "Estrutura excede o limite de profundidade."}


class PromptFlow:
    def __init__(self, root: Path, question: str, settings, *, allow_edits: bool, limits: dict):
        self.path = root / ".codaro" / "prompt.json"
        self.secret = getattr(settings, "api_key", "")
        forms = {self.secret}
        for _ in range(2):
            forms.update(
                json.dumps(value, ensure_ascii=ascii_only)[1:-1]
                for value in list(forms)
                for ascii_only in (True, False)
            )
        self.secret_forms = sorted(forms - {""}, key=len, reverse=True)
        self.started = time.monotonic()
        self.write_error: str | None = None
        self.data = {
            "schema_version": 1,
            "run_id": str(uuid.uuid4()),
            "repository_root": str(root),
            "mode": "edit" if allow_edits else "read_only",
            "model": getattr(settings, "model", ""),
            "provider": getattr(settings, "provider_id", ""),
            "api_style": getattr(settings, "api_style", "openai"),
            "context_source": getattr(settings, "context_source", "configuração padrão/ambiente"),
            "base_url": getattr(settings, "base_url", ""),
            "tls_insecure": getattr(settings, "tls_insecure", False),
            "user_question": question,
            "limits": limits,
            "started_at": timestamp(),
            "status": "running",
            "turns": [],
            "events": [],
        }
        self.checkpoint()

    @property
    def turn(self) -> dict | None:
        return self.data["turns"][-1] if self.data["turns"] else None

    def add_turn(self, request: dict, budget: dict):
        self.data["turns"].append(
            {
                "iteration": len(self.data["turns"]) + 1,
                "kind": "stream" if request.get("stream") else "chat",
                "started_at": timestamp(),
                "request": snapshot(request),
                "budget": budget,
                "http_attempts": [],
                "tool_results": [],
            }
        )
        self.checkpoint()

    def response(self, message):
        if self.turn is not None:
            self.turn["response"] = snapshot(message)
            self.turn["responded_at"] = timestamp()
            self.checkpoint()

    def tool_result(self, message: dict, arguments, result: dict, elapsed_ms: float):
        if self.turn is not None:
            self.turn["tool_results"].append(
                {
                    "message": snapshot(message),
                    "normalized_arguments": snapshot(arguments),
                    "result": snapshot(result),
                    "duration_ms": elapsed_ms,
                }
            )
            self.checkpoint()

    def finish(self, status: str, *, answer: str | None = None, error: BaseException | None = None):
        self.data.update(
            status=status,
            finished_at=timestamp(),
            duration_ms=round((time.monotonic() - self.started) * 1000, 3),
        )
        if answer is not None:
            self.data["final_answer"] = answer
        if error is not None:
            self.data["error"] = {"type": type(error).__name__, "message": str(error)}
            if self.turn is not None:
                self.turn["error"] = self.data["error"]
        self.checkpoint()

    def _redact(self, value):
        if isinstance(value, str):
            for secret in self.secret_forms:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        return value

    def checkpoint(self):
        try:
            data = json.dumps(self._redact(self.data), ensure_ascii=False, indent=2).encode(
                "utf-8", errors="backslashreplace"
            )
            atomic_write(self.path, data + b"\n")
            self.write_error = None
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            self.write_error = f"Não foi possível salvar {self.path}: {type(exc).__name__}."


def atomic_write(path: Path, data: bytes):
    """Pin the storage directory on POSIX; never follow a storage/destination symlink."""
    if os.name != "posix":
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("O dump não pode usar links simbólicos.")
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if path.exists():
            existing = path.stat()
            if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
                raise ValueError("O dump deve ser um arquivo regular sem links.")
        descriptor, temporary = tempfile.mkstemp(prefix=".prompt-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd = os.open(path.parent.parent, flags)
    try:
        try:
            os.mkdir(path.parent.name, mode=0o700, dir_fd=root_fd)
        except FileExistsError:
            pass
        storage_fd = os.open(path.parent.name, flags, dir_fd=root_fd)
    finally:
        os.close(root_fd)
    temporary = f".prompt-{uuid.uuid4().hex}.tmp"
    try:
        try:
            existing = os.stat(path.name, dir_fd=storage_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
            raise ValueError("O dump deve ser um arquivo regular sem links.")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=storage_fd,
        )
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path.name, src_dir_fd=storage_fd, dst_dir_fd=storage_fd)
        os.fsync(storage_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=storage_fd)
        except FileNotFoundError:
            pass
        os.close(storage_fd)
