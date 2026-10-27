from __future__ import annotations

import hashlib

from codaro.agent.events import AgentEvent
from codaro.agent.messages import serialize
from codaro.artifacts import ARTIFACT_TOOLS
from codaro.index import CodeIndex
from codaro.navigation import file_outline, find_definition, find_references
from codaro.policies import Mode
from codaro.retrieval import grep_code, scoped_files
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
                args["steps"],
                args["criteria"],
                args.get("validation_commands"),
                args.get("criterion_checks"),
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
        if status == "completed" and agent.jobs.active(purpose="validation"):
            raise ValueError("Verificações ainda estão em execução; consulte get_command_output.")
        if status == "completed" and (
            not agent.tasks.validation_ready()
            or any(step["state"] != "done" for step in task["plan"])
        ):
            raise ValueError("Etapas ou validação da revisão atual ainda estão pendentes.")
        agent.tasks.state(status, args["summary"])
        from codaro.validation import validation_report

        return {
            "state": status,
            "summary": args["summary"],
            "validation": validation_report(agent.tasks.current()),
        }
    if name == "apply_changes":
        if not agent.allow_edits:
            raise ValueError("Alterações disponíveis somente no modo Executar.")
        proposals = agent.edits.prepare_operations(args["operations"], args["reason"])
        result = agent.review_changes(proposals, atomic=args.get("atomic", True))
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
        filters = {
            key: args[key]
            for key in ("path", "glob", "language", "extension", "kind")
            if key in args
        }
        if agent.features.get("semantic_search") and index.semantic is None:
            index.semantic = agent.services.semantic()
        result = {"results": index.search(args["query"], args.get("limit", 6), **filters)}
        flow = current_flow.get()
        if flow is not None:
            flow.data.setdefault("retrieval_metrics", []).append(index.last_search)
        result["retrieval"] = index.last_search
        if agent.features.get("semantic_search"):
            agent.services.warm(filters)
            result["retrieval"]["warmup"] = agent.services.warmup_status
        return result
    if name == "get_project_map":
        return agent.project_map.page(index, **args)
    if name == "get_file_outline":
        return file_outline(agent.repository, **args)
    if name == "find_definition":
        from codaro.lsp import navigate

        if all(key in args for key in ("path", "line", "column")):
            resolved = navigate(
                agent.repository,
                args["path"],
                args["line"],
                args["column"],
                "definition",
                agent.features["lsp"],
                agent._cancelled,
                pool=agent.services.lsp,
            )
            if resolved is not None:
                return resolved
        return find_definition(
            index, **{key: value for key, value in args.items() if key not in {"line", "column"}}
        )
    if name == "find_references":
        from codaro.lsp import navigate

        if all(key in args for key in ("path", "line", "column")):
            resolved = navigate(
                agent.repository,
                args["path"],
                args["line"],
                args["column"],
                "references",
                agent.features["lsp"],
                agent._cancelled,
                pool=agent.services.lsp,
            )
            if resolved is not None:
                offset, limit = args.get("offset", 0), args.get("limit", 20)
                return {
                    **resolved,
                    "results": resolved["results"][offset : offset + limit],
                    "next_offset": offset + limit
                    if offset + limit < len(resolved["results"])
                    else None,
                }
        return find_references(
            agent.repository,
            cancelled=agent._cancelled,
            **{key: value for key, value in args.items() if key not in {"line", "column"}},
        )
    if name == "grep_code":
        return grep_code(agent.repository, cancelled=agent._cancelled, **args)
    if name == "record_finding":
        return agent.knowledge.record(**args)
    if name == "search_findings":
        return agent.knowledge.search(**args)
    if name in {
        "run_command",
        "start_command",
        "get_command_output",
        "cancel_command",
        "run_check",
        "discover_checks",
        "get_validation_report",
        "get_language_support",
    }:
        from codaro.agent.command_tools import execute_command

        return execute_command(agent, index, name, args)
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
        digest = hashlib.sha256(data).hexdigest()
        if args.get("file_hash") and args["file_hash"] != digest:
            raise ValueError("Arquivo mudou entre páginas; reinicie a leitura.")
        if name == "read_symbol":
            result = index.read_symbol(canonical, args["symbol"], args.get("start_line"))
            if agent.repository.read_bytes(path) != data:
                raise ValueError("Arquivo mudou durante a leitura. Leia novamente.")
        else:
            result = agent.repository.render_lines(
                canonical,
                data.decode("utf-8-sig"),
                args["start"],
                args["end"],
                column=args.get("column", 0),
                max_chars=args.get("max_chars", 6000),
            )
        result["file_hash"] = digest
        agent._read_snapshot = data
        return result
    filters = {key: args[key] for key in ("path", "glob", "language", "extension") if key in args}
    paths = [
        path.relative_to(agent.repository.root).as_posix()
        for path in scoped_files(agent.repository, **filters)
    ]
    offset = args.get("offset", 0)
    limit = args.get("limit", 60)
    return {
        "files": paths[offset : offset + limit],
        "total": len(paths),
        "next_offset": offset + limit if offset + limit < len(paths) else None,
    }
