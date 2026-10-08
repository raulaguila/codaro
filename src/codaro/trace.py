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

MAX_TRACE_BYTES = 2_000_000
MAX_EVENT_BYTES = 8_000_000
MAX_ARCHIVE_BYTES = 32_000_000
MAX_ARCHIVE_RUNS = 20

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
    def __init__(
        self, root: Path, question: str, settings, *, allow_edits: bool, limits: dict, redact=None
    ):
        self.path = root / ".codaro" / "prompt.json"
        self.redact = redact or (lambda value: value)
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
        self.actions = []
        self.event_bytes = 0
        self.event_sequence = 0
        self.turn_sequence = 0
        self.archive_path = self.path.parent / ("run-" + self.data["run_id"] + ".jsonl")
        self.data["archive"] = {"path": self.archive_path.name, "format": "jsonl", "complete": True}
        self.checkpoint()
        try:
            self.rotate_archives()
        except OSError:
            pass

    def append_event(self, kind, value):
        try:
            self.event_sequence += 1
            record = self.redact(
                self._redact({"sequence": self.event_sequence, "kind": kind, "data": value})
            )
            encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
            if self.event_bytes + len(encoded) > MAX_EVENT_BYTES:
                self.data["archive"]["complete"] = False
                self.data["archive"]["omitted_events"] = (
                    self.data["archive"].get("omitted_events", 0) + 1
                )
                return
            # Pin the directory and reject links before appending an incremental event.
            if self.path.parent.is_symlink() or self.archive_path.is_symlink():
                raise ValueError("Arquivo de eventos não pode usar links.")
            parent_fd = (
                os.open(
                    self.path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                if os.name == "posix"
                else None
            )
            try:
                fd = os.open(
                    self.archive_path.name if os.name == "posix" else self.archive_path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    **({"dir_fd": parent_fd} if os.name == "posix" else {}),
                )
                try:
                    state = os.fstat(fd)
                    if (
                        not stat.S_ISREG(state.st_mode)
                        or state.st_nlink != 1
                        or os.name == "posix"
                        and state.st_mode & 0o077
                    ):
                        raise ValueError("Arquivo de eventos inválido.")
                    with os.fdopen(fd, "ab", closefd=False) as output:
                        output.write(encoded)
                        output.flush()
                    self.event_bytes += len(encoded)
                finally:
                    os.close(fd)
            finally:
                if parent_fd is not None:
                    os.close(parent_fd)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            self.data["archive"]["complete"] = False
            self.data["archive"]["error"] = type(exc).__name__

    def capture(self, kind, value):
        self.append_event(kind, value)
        attempt = self.turn["http_attempts"][-1]
        if kind in {"sse", "ndjson"}:
            key = "sse_events" if kind == "sse" else "ndjson_events"
            events = attempt.setdefault(key, [])
            # Keep a bounded preview; full events reside in the per-run archive.
            if (
                len(json.dumps(events, ensure_ascii=False))
                + len(json.dumps(value, ensure_ascii=False))
                < 32_000
            ):
                events.append(value)
            else:
                attempt["events_in_archive"] = True
        else:
            encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
            attempt[kind] = (
                value
                if len(encoded) <= 65_000
                else {
                    "preview": encoded[:32_000].decode("utf-8", errors="replace"),
                    "content_in_archive": True,
                }
            )

    def rotate_archives(self):
        import re

        archives = [
            p
            for p in self.path.parent.glob("run-*.jsonl")
            if re.fullmatch(r"run-[0-9a-f-]{36}\.jsonl", p.name)
            and not p.is_symlink()
            and p.is_file()
            and p.stat().st_nlink == 1
        ]
        archives.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        size = 0
        for number, archive in enumerate(archives):
            size += archive.stat().st_size
            if archive != self.archive_path and (
                number >= MAX_ARCHIVE_RUNS or size > MAX_ARCHIVE_BYTES
            ):
                archive.unlink()

    @property
    def turn(self) -> dict | None:
        return self.data["turns"][-1] if self.data["turns"] else None

    def add_turn(self, request: dict, budget: dict):
        self.turn_sequence += 1
        self.data["turns"].append(
            {
                "iteration": self.turn_sequence,
                "kind": "stream" if request.get("stream") else "chat",
                "started_at": timestamp(),
                "request": snapshot(request),
                "budget": budget,
                "http_attempts": [],
                "tool_results": [],
            }
        )
        self.append_event("request", self.turn)
        self.checkpoint()

    def response(self, message):
        if self.turn is not None:
            self.turn["response"] = snapshot(message)
            self.append_event("response", message)
            self.turn["responded_at"] = timestamp()
            self.checkpoint()

    def tool_result(self, message: dict, arguments, result: dict, elapsed_ms: float):
        self.actions.append(
            {
                "tool": message.get("name"),
                **{
                    key: result[key]
                    for key in (
                        "path",
                        "start_line",
                        "end_line",
                        "exit_code",
                        "timed_out",
                        "proposal_id",
                        "error",
                    )
                    if key in result
                },
                **(
                    {"excerpt": result["content"][:1200]}
                    if isinstance(result.get("content"), str)
                    else {}
                ),
            }
        )
        self.actions = self.actions[-64:]
        self.append_event(
            "tool_result", {"message": message, "arguments": arguments, "result": result}
        )
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
        self.append_event(
            "finish",
            {
                "status": status,
                "answer": answer,
                "error": self.data.get("error"),
                "archive": self.data["archive"],
            },
        )
        self.checkpoint()
        try:
            self.rotate_archives()
        except OSError:
            pass

    def _redact(self, value):
        if isinstance(value, str):
            for secret in self.secret_forms:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()}
        return value

    def checkpoint(self):
        try:
            data = json.dumps(
                self.redact(self._redact(self.data)), ensure_ascii=False, indent=2
            ).encode("utf-8", errors="backslashreplace")
            while len(data) > MAX_TRACE_BYTES and len(self.data["turns"]) > 1:
                self.data["turns"].pop(0)
                self.data["older_turns_in_archive"] = True
                data = json.dumps(
                    self.redact(self._redact(self.data)), ensure_ascii=False, indent=2
                ).encode()
            if len(data) > MAX_TRACE_BYTES:
                self.append_event(
                    "snapshot_metadata",
                    {key: value for key, value in self.data.items() if key != "turns"},
                )
                reduced = self.redact(self._redact(self.data))
                reduced["turns"] = [
                    {"iteration": item.get("iteration"), "content_in_archive": True}
                    for item in reduced["turns"]
                ]
                for key in (
                    "events",
                    "project_map",
                    "local_retrievals",
                    "task_memory",
                    "compactions",
                ):
                    if key in reduced:
                        reduced[key] = {"content_in_archive": True}
                reduced["snapshot_limited"] = True
                data = json.dumps(reduced, ensure_ascii=False).encode()
            if len(data) > MAX_TRACE_BYTES:
                raise ValueError("Snapshot de debug excede a cota.")
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
