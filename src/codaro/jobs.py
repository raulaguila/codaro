"""Owned, bounded background commands. Job IDs are never restored as live PIDs."""

from __future__ import annotations

import contextvars
import os
import threading
import time
import uuid
from dataclasses import dataclass, field

from codaro.command_environment import command_environment, mask_command_secrets
from codaro.commands import run_command, validate_command
from codaro.llm import RequestCancelled


@dataclass
class CommandJob:
    id: str
    argv: list
    cwd: str
    purpose: str
    task_id: str
    revision: int
    check_id: str | None = None
    state: str = "running"
    output: bytearray = field(default_factory=bytearray)
    total: int = 0
    result: dict = field(default_factory=dict)
    cancelled: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    recorded: bool = False
    mask: object = field(default=lambda value: value, repr=False)


class JobManager:
    def __init__(self, repository, *, redact=lambda value: value):
        self.repository, self.redact = repository, redact
        self.jobs = {}
        self.lock = threading.Lock()

    def start(
        self,
        argv,
        cwd=".",
        timeout=300,
        *,
        purpose="operation",
        task=None,
        check_id=None,
        cancelled=None,
    ):
        validate_command(argv, timeout, maximum=3600)
        target = command_directory(self.repository, cwd)
        environment = command_environment(os.environ)
        with self.lock:
            if cancelled is not None and cancelled.is_set():
                raise RequestCancelled("Execução cancelada antes de iniciar o comando.")
            if sum(not job.done.is_set() for job in self.jobs.values()) >= 4:
                raise ValueError("Até quatro comandos simultâneos.")
            if len(self.jobs) >= 32:
                oldest = next((key for key, job in self.jobs.items() if job.done.is_set()), None)
                if oldest is None:
                    raise ValueError("Limite de comandos da sessão atingido.")
                del self.jobs[oldest]
            job = CommandJob(
                uuid.uuid4().hex[:16],
                list(argv),
                cwd,
                purpose,
                task["id"] if task else "",
                task["revision"] if task else 0,
                check_id,
            )
            self.jobs[job.id] = job
            job.mask = lambda value: self.redact(mask_command_secrets(value, environment))

        def append(chunk):
            with job.lock:
                job.total += len(chunk)
                job.output.extend(chunk[: max(0, 2_000_000 - len(job.output))])

        def run():
            from codaro.runtime import request_deadline
            from codaro.trace import current_flow

            request_deadline.set(None)
            current_flow.set(None)
            try:
                job.result = run_command(
                    target,
                    argv,
                    timeout,
                    job.cancelled,
                    on_output=append,
                    maximum=3600,
                    workspace_root=self.repository.root,
                    environment=environment,
                )
                job.state = (
                    "completed"
                    if job.result["exit_code"] == 0 and not job.result["timed_out"]
                    else "failed"
                )
            except RequestCancelled:
                job.state = "cancelled"
            except (ValueError, OSError) as exc:
                job.state = "failed"
                job.result = {"error": self.redact(str(exc))[:200]}
            finally:
                job.done.set()

        context = contextvars.copy_context()
        threading.Thread(
            target=lambda: context.run(run), daemon=True, name="codaro-job-" + job.id
        ).start()
        return self.poll(job.id)

    def poll(self, job_id, *, offset=0, limit=4000, wait_ms=0, cancelled=None):
        if job_id not in self.jobs:
            raise ValueError(
                "Comando não pertence a esta sessão; processos antigos não são reexecutados."
            )
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 200 <= limit <= 8000
            or type(wait_ms) is not int
            or not 0 <= wait_ms <= 5000
        ):
            raise ValueError("Página/espera do comando inválida.")
        job = self.jobs[job_id]
        until = time.monotonic() + wait_ms / 1000
        while not job.done.is_set() and time.monotonic() < until:
            if cancelled is not None and cancelled.is_set():
                raise RequestCancelled(
                    "Espera cancelada; use cancel_command para encerrar o processo."
                )
            job.done.wait(min(0.05, max(0, until - time.monotonic())))
        with job.lock:
            text = job.output.decode("utf-8", errors="replace")
            if not job.done.is_set():
                text = text[: text.rfind("\n") + 1]
            # Do not display an incomplete PEM block while it is being written.
            if "-----BEGIN " in text and "-----END " not in text:
                text = text[: text.index("-----BEGIN ")]
            text = job.mask(text)
            page = text[offset : offset + limit]
        return {
            "job_id": job.id,
            "state": job.state,
            "argv": [job.mask(part) for part in job.argv],
            "cwd": job.cwd,
            "purpose": job.purpose,
            "output": page,
            "offset": offset,
            "next_offset": offset + len(page)
            if offset + len(page) < len(text) or not job.done.is_set()
            else None,
            "truncated": offset + len(page) < len(text) or job.total > 2_000_000,
            "exit_code": job.result.get("exit_code"),
            "timed_out": job.result.get("timed_out", False),
            **({"error": job.result["error"]} if "error" in job.result else {}),
        }

    def cancel(self, job_id):
        if job_id not in self.jobs:
            raise ValueError("Comando não pertence a esta sessão.")
        self.request_cancel([job_id])
        return self.poll(job_id, wait_ms=3000)

    def request_cancel(self, identifiers=None):
        """Signal owned processes immediately; UI polling confirms actual termination."""
        with self.lock:
            selected = list(self.jobs) if identifiers is None else list(identifiers)
            for identifier in selected:
                if identifier not in self.jobs:
                    raise ValueError("Comando não pertence a esta sessão.")
            active = [self.jobs[key] for key in selected if not self.jobs[key].done.is_set()]
            for job in active:
                job.cancelled.set()
            return [job.id for job in active]

    def close(self):
        self.request_cancel()
        deadline = time.monotonic() + 3
        for job in list(self.jobs.values()):
            job.done.wait(max(0, deadline - time.monotonic()))

    def active(self, *, purpose=None):
        return [
            job.id
            for job in self.jobs.values()
            if not job.done.is_set() and (purpose is None or job.purpose == purpose)
        ]


def command_directory(repository, cwd):
    from pathlib import Path

    if not isinstance(cwd, str) or len(cwd) > 2000:
        raise ValueError("Diretório de comando inválido.")
    if cwd == ".":
        return repository.root
    relative = repository._relative(repository.root / cwd)
    current = repository.root
    for component in relative.parts:
        current /= component
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("Diretório não pode usar links ou junctions.")
    if not current.is_dir() or current.resolve() != Path(current):
        raise ValueError("Diretório inválido ou fora do projeto.")
    from codaro.repository import EXCLUDED_DIRS

    if set(relative.parts) & EXCLUDED_DIRS:
        raise ValueError("Diretório excluído da sessão.")
    return current
