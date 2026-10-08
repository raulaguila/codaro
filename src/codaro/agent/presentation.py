from __future__ import annotations

import shlex


def tool_target(name: str, args: dict) -> str:
    if name == "search_conversation":
        return args["query"]
    if name == "read_conversation":
        return args["turn_id"]
    if name == "remember_task":
        return args["note"]
    if name == "run_command":
        return shlex.join(args["argv"])[:500]
    if name == "get_repository_info":
        return "Diretório e capacidades da sessão"
    if name == "propose_edit":
        return str(args.get("path", ""))[:240]
    if name == "search_code":
        return f"Consulta: {args.get('query', '')[:160]}"
    if name == "read_symbol":
        return f"{args.get('path', '')} · {args.get('symbol', '')}"[:240]
    if name == "read_lines":
        return f"{args.get('path', '')}:{args.get('start', '')}–{args.get('end', '')}"[:240]
    return f"Página a partir do arquivo {args.get('offset', 0)}"


TOOL_TITLES = {
    "get_context_status": "Consultar orçamento de contexto",
    "compact_context": "Liberar contexto",
    "request_tools": "Carregar ferramentas",
    "search_conversation": "Buscar na conversa",
    "read_conversation": "Recuperar conversa",
    "remember_task": "Registrar nota da tarefa",
    "run_command": "Executar comando",
    "get_repository_info": "Consultar diretório",
    "propose_edit": "Propor edição",
    "search_code": "Buscar código",
    "read_symbol": "Ler símbolo",
    "read_lines": "Ler linhas",
    "list_files": "Listar arquivos",
}


def tool_outcome(result: dict) -> tuple[str, str]:
    if "error" in result:
        return "error", str(result["error"])[:200]
    if result.get("state") in {"applied", "partial", "conflict", "rejected"}:
        state = result["state"]
        label = {
            "applied": "Alteração aplicada · validação pendente",
            "partial": "Conjunto parcialmente aplicado; confira os arquivos",
            "conflict": "Conflito; alteração bloqueada",
            "rejected": "Alteração rejeitada; arquivos preservados",
        }[state]
        return "success" if state == "applied" else "error", label
    if "repository_root" in result:
        return "success", result["repository_root"]
    if "proposal_id" in result:
        return "pending", "Diff preparado · aguardando aprovação"
    if "text" in result:
        return "success", f"Turno {result.get('turn_id', '')} · {len(result['text'])} caracteres"
    if "saved" in result:
        return "success", "Nota registrada" if result["saved"] else "Nota já registrada"
    if "exit_code" in result:
        state = "error" if result["exit_code"] != 0 or result["timed_out"] else "success"
        outcome = "Tempo limite excedido" if result["timed_out"] else f"Saída {result['exit_code']}"
        return state, outcome + "\n" + result["output"][:1000]
    if result.get("already_read"):
        return "cached", "Conteúdo já consultado; arquivo sem alterações"
    if "results" in result:
        count = len(result["results"])
        summary = f"{count} {'resultado' if count == 1 else 'resultados'}"
    elif "content" in result:
        summary = (
            f"Linhas {result['start_line']}–{result['end_line']} · "
            f"{len(result['content'])} caracteres"
        )
    else:
        count = len(result.get("files", []))
        summary = f"{count} {'arquivo' if count == 1 else 'arquivos'}"
    if result.get("truncated"):
        summary += " · leitura parcial"
    return "success", summary
