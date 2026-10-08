from __future__ import annotations

import time

from codaro.agent.events import AgentEvent
from codaro.agent.messages import serialize
from codaro.artifacts import ARTIFACT_TOOLS
from codaro.commands import run_command
from codaro.index import CodeIndex
from codaro.policies import Mode
from codaro.trace import current_flow


def execute_tool(agent, index: CodeIndex, name: str, args: dict) -> dict:
    if tool := agent.registry.tools.get(name):
        if tool.handler is not None:
            agent.registry.validate(name, args)
            return tool.handler(args)
    if name in {tool["function"]["name"] for tool in ARTIFACT_TOOLS}:
        agent.registry.validate(name, args)
        identifier = args["artifact_id"]
        if name == "read_artifact":
            return agent.artifacts.read(identifier, args.get("offset", 0), args.get("limit", 2400))
        if name == "search_artifact":
            return agent.artifacts.search(identifier, args["query"], args.get("limit", 5))
        return agent.artifacts.info(identifier)
    agent.validate_arguments(name, args)
    agent._read_snapshot = None
    if name in {"get_context_status", "compact_context", "request_tools"}:
        raise ValueError("Ferramenta de contexto disponível apenas no fluxo ativo do agente.")
    if name == "get_task":
        return agent.tasks.page(args.get("offset", 0), args.get("limit", 2400))
    if name in {"update_plan", "finish_task"}:
        if agent.mode == Mode.ASK:
            raise ValueError("Planejamento desabilitado no modo Perguntar.")
        agent.tasks.start("Atividade atual")
        if name == "update_plan":
            task = agent.tasks.plan(
                args["steps"], args["criteria"], args.get("validation_commands")
            )
            agent._detail(AgentEvent("plan", "Plano atualizado", serialize(task["plan"])))
            return agent.tasks.projection()
        status = args["status"]
        agent.sync_workspace(index)
        if agent.mode == Mode.PLAN and status == "completed":
            raise ValueError("No modo Planejar, finalize com status planned.")
        if agent.mode == Mode.EXECUTE and status == "planned":
            raise ValueError("No modo Executar, conclua ou informe um bloqueio.")
        task = agent.tasks.current()
        if status == "completed" and args.get("verified_no_change"):
            current_checks = [
                item for item in task["validations"] if item["revision"] == task["revision"]
            ]
            if (
                not agent.edits.observed
                or not current_checks
                or any(item["exit_code"] != 0 or item["timed_out"] for item in current_checks)
            ):
                raise ValueError("Sem alteração exige leitura atual e validação aprovada.")
            agent.tasks.update(
                lambda item: item.update(verified_no_change_digest=task.get("workspace_digest"))
            )
            task = agent.tasks.current()
        if status == "completed" and (
            not agent.tasks.validation_ready()
            or any(step["state"] != "done" for step in task["plan"])
        ):
            raise ValueError("Etapas ou validação da revisão atual ainda estão pendentes.")
        agent.tasks.state(status, args["summary"])
        return {"state": status, "summary": args["summary"]}
    if name == "apply_changes":
        if not agent.allow_edits:
            raise ValueError("Alterações disponíveis somente no modo Executar.")
        proposals = agent.edits.prepare_operations(args["operations"], args["reason"])
        result = agent.review_changes(proposals)
        agent.sync_workspace(index)
        return result
    if name == "search_conversation":
        return agent.memory.search(args["query"], args.get("limit", 5))
    if name == "read_conversation":
        return agent.memory.read(args["turn_id"], args.get("offset", 0), args.get("limit", 2400))
    if name == "remember_task":
        return agent.memory.remember("agent_note", args["note"], source="assistant")
    if name == "get_repository_info":
        return agent.repository_info()
    if name == "search_code":
        return {"results": index.search(args["query"], args.get("limit", 6))}
    if name == "run_command":
        if not agent.legacy and agent.mode != Mode.EXECUTE:
            raise ValueError("Comandos disponíveis somente no modo Executar.")
        if not agent.commands_available:
            raise ValueError("Execução de comandos desabilitada nesta sessão.")
        if agent.edits.pending:
            raise ValueError(
                "Revise as propostas pendentes antes de executar comandos. "
                "O código proposto ainda não foi aplicado."
            )
        task = None if agent.legacy else agent.tasks.current()
        if task:
            if agent._failures_run >= agent.max_corrections:
                agent.tasks.state("blocked", "Limite de tentativas de correção atingido.")
                raise ValueError("Limite de correções atingido. Continue em nova interação.")
            agent.tasks.state("awaiting_approval")
        started = time.monotonic()
        try:
            approved = (task and agent.policy.permits_command(task["id"], args["argv"])) or (
                agent.approve_command is not None
                and agent.approve_command(args["argv"], args.get("timeout", 60), agent._cancelled)
            )
        finally:
            agent._deadline += time.monotonic() - started
        if task:
            agent.tasks.event(
                "approval",
                {
                    "kind_action": "command",
                    "argv": args["argv"],
                    "approved": bool(approved),
                    "policy": agent.policy.kind,
                },
            )
        if not approved:
            if task:
                agent.tasks.state("blocked", "Comando rejeitado pelo usuário.")
            return {"error": "Comando rejeitado; nenhuma execução realizada."}
        if task:
            agent.tasks.state("validating" if args.get("purpose") == "validation" else "executing")
            agent.tasks.event("command_started", {"argv": args["argv"]})
            agent.sync_workspace(index)
        result = run_command(
            agent.repository.root, args["argv"], args.get("timeout", 60), agent._cancelled
        )
        if task:
            agent.sync_workspace(index)
            if args.get("purpose") == "validation":
                agent.tasks.validation(result)
                if result["exit_code"] != 0 or result["timed_out"]:
                    agent._failures_run += 1
            else:
                # An arbitrary operation may change sources; old validations become stale.
                agent.tasks.update(lambda item: item.update(revision=item["revision"] + 1))
            agent.tasks.event(
                "command_finished",
                {
                    "argv": args["argv"],
                    "exit_code": result["exit_code"],
                    "timed_out": result["timed_out"],
                },
            )
        return result
    if name == "propose_edit":
        if not agent.allow_edits:
            raise ValueError("Edição desabilitada nesta sessão.")
        result = agent.edits.propose(**args)
        flow = current_flow.get()
        if flow is not None:
            agent.edits.proposals[result["proposal_id"]].task_id = flow.data["run_id"]
        if not agent.legacy or agent.approve_edit is not None:
            applied = agent.review_changes([agent.edits.proposals[result["proposal_id"]]])
            agent.sync_workspace(index)
            return {**applied, "proposal_id": result["proposal_id"], "path": result["path"]}
        return result
    if name in {"read_symbol", "read_lines"}:
        path = agent.repository.resolve_file(args["path"])
        canonical = path.relative_to(agent.repository.root).as_posix()
        data = agent.repository.read_bytes(path)
        if name == "read_symbol":
            result = index.read_symbol(canonical, args["symbol"], args.get("start_line"))
            if agent.repository.read_bytes(path) != data:
                raise ValueError("Arquivo mudou durante a leitura. Leia novamente.")
        else:
            result = agent.repository.render_lines(
                canonical, data.decode("utf-8-sig"), args["start"], args["end"]
            )
        agent._read_snapshot = data
        return result
    paths = [str(path.relative_to(agent.repository.root)) for path in agent.repository.files()]
    offset = args.get("offset", 0)
    limit = args.get("limit", 60)
    return {
        "files": paths[offset : offset + limit],
        "total": len(paths),
        "next_offset": offset + limit if offset + limit < len(paths) else None,
    }
