"""Approved foreground/background execution and revision-bound validation receipts."""

import inspect
import time

from codaro.commands import run_command, validate_command
from codaro.jobs import command_directory
from codaro.policies import Mode
from codaro.validation import discover_checks, validation_report


def approval(agent, argv, timeout, cwd):
    task = None if agent.legacy else agent.tasks.current()
    if task and cwd == "." and agent.policy.permits_command(task["id"], argv):
        return True
    callback = agent.approve_command
    if callback is None:
        return False
    if cwd == ".":
        return callback(argv, timeout, agent._cancelled)
    parameters = inspect.signature(callback).parameters
    if "cwd" in parameters or any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
        return callback(argv, timeout, agent._cancelled, cwd=cwd)
    if any(p.kind == p.VAR_POSITIONAL for p in parameters.values()):
        return callback(argv, timeout, agent._cancelled, cwd)
    raise ValueError(
        "O revisor precisa exibir o diretório de trabalho para autorizar este comando."
    )


def record_result(agent, index, result, *, purpose, task_id, revision, cwd=".", check_id=None):
    task = agent.tasks.current()
    if task is None or task["id"] != task_id:
        return
    agent.sync_workspace(index)
    task = agent.tasks.current()
    result.update(cwd=cwd, validated_revision=revision)
    if check_id:
        result["check_id"] = check_id
    result["stale_validation"] = task["revision"] != revision
    if purpose == "validation" and type(result.get("exit_code")) is int:
        agent.tasks.validation(result)
        if result["exit_code"] != 0 or result.get("timed_out"):
            agent._failures_run += 1
    elif purpose == "operation":
        agent.tasks.update(
            lambda item: item.update(revision=item["revision"] + 1, state="executing")
        )
    agent.tasks.event(
        "command_finished",
        {
            key: result[key]
            for key in ("argv", "cwd", "exit_code", "timed_out", "job_id", "check_id")
            if key in result
        },
    )


def execute_command(agent, index, name, args):
    if name == "get_language_support":
        from codaro.language_support import language_support

        return language_support(**args)
    if name == "discover_checks":
        return {
            "results": discover_checks(agent.repository, args.get("path")),
            "source": "project_declared_checks_not_executed",
        }
    if name == "get_validation_report":
        agent.sync_workspace(index, force=True)
        return {
            **validation_report(agent.tasks.current()),
            "running_jobs": agent.jobs.active(purpose="validation"),
        }
    if name in {"get_command_output", "cancel_command"}:
        job_id = args["job_id"]
        result = (
            agent.jobs.cancel(job_id)
            if name == "cancel_command"
            else agent.jobs.poll(
                job_id,
                **{key: value for key, value in args.items() if key != "job_id"},
                cancelled=agent._cancelled,
            )
        )
        job = agent.jobs.jobs[job_id]
        if job.done.is_set() and not job.recorded:
            job.recorded = True
            record_result(
                agent,
                index,
                result,
                purpose=job.purpose,
                task_id=job.task_id,
                revision=job.revision,
                cwd=job.cwd,
                check_id=job.check_id,
            )
        return result
    if not agent.legacy and agent.mode != Mode.EXECUTE:
        raise ValueError("Comandos disponíveis somente no modo Executar.")
    if not agent.commands_available:
        raise ValueError("Execução de comandos desabilitada nesta sessão.")
    if agent.edits.pending:
        raise ValueError("Revise as propostas pendentes antes de executar comandos.")
    check = None
    if name == "run_check":
        check = next(
            (item for item in discover_checks(agent.repository) if item["id"] == args["check_id"]),
            None,
        )
        if check is None:
            raise ValueError("Verificação não está no catálogo atual; use discover_checks.")
        args = {**args, "argv": check["argv"], "cwd": check["cwd"], "purpose": "validation"}
    argv, cwd, timeout = (
        args["argv"],
        args.get("cwd", "."),
        args.get("timeout", 300 if name != "run_command" else 60),
    )
    validate_command(argv, timeout, maximum=3600 if name != "run_command" else 300)
    target = command_directory(agent.repository, cwd)
    task = None if agent.legacy else agent.tasks.current()
    if name == "start_command" or args.get("background", False):
        for job in agent.jobs.jobs.values():
            if (
                not job.done.is_set()
                and job.argv == argv
                and job.cwd == cwd
                and job.task_id == (task["id"] if task else "")
            ):
                return {**agent.jobs.poll(job.id), "reused_result": True}
    if task:
        if agent._failures_run >= agent.max_corrections:
            agent.tasks.state("blocked", "Limite de tentativas de correção atingido.")
            raise ValueError("Limite de correções atingido. Continue em nova interação.")
        agent.tasks.state("awaiting_approval")
    started = time.monotonic()
    try:
        approved = approval(agent, argv, timeout, cwd)
    finally:
        agent._deadline += time.monotonic() - started
    if agent._cancelled is not None and agent._cancelled.is_set():
        from codaro.llm import RequestCancelled

        raise RequestCancelled("Comando cancelado durante aprovação.")
    if task:
        agent.tasks.event(
            "approval",
            {
                "kind_action": "command",
                "argv": argv,
                "cwd": cwd,
                "approved": bool(approved),
                "policy": agent.policy.kind,
            },
        )
    if not approved:
        if task:
            agent.tasks.state("blocked", "Comando rejeitado pelo usuário.")
        return {"error": "Comando rejeitado; nenhuma execução realizada."}
    agent.sync_workspace(index)
    task = agent.tasks.current() if task else None
    purpose = args.get("purpose", "operation")
    if task:
        agent.tasks.state("validating" if purpose == "validation" else "executing")
        agent.tasks.event("command_started", {"argv": argv, "cwd": cwd})
    if name == "start_command" or args.get("background", False):
        result = agent.jobs.start(
            argv,
            cwd,
            timeout,
            purpose=purpose,
            task=task,
            check_id=check["id"] if check else None,
            cancelled=agent._cancelled,
        )
        if task:
            agent.tasks.event(
                "background_started", {"job_id": result["job_id"], "argv": argv, "cwd": cwd}
            )
        return result
    revision = task["revision"] if task else 0
    result = run_command(
        target, argv, timeout, agent._cancelled, workspace_root=agent.repository.root
    )
    if task:
        record_result(
            agent,
            index,
            result,
            purpose=purpose,
            task_id=task["id"],
            revision=revision,
            cwd=cwd,
            check_id=check["id"] if check else None,
        )
    return result
