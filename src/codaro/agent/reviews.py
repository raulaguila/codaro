"""Approval, application and receipts for a reviewed change set."""

import hashlib
import time

from codaro.agent.events import AgentEvent, InvestigationCancelled
from codaro.policies import Mode


def review_changes(agent, proposals):
    if agent.mode != Mode.EXECUTE:
        raise ValueError("Alterações disponíveis somente no modo Executar.")
    task = agent.tasks.current() or agent.tasks.start("Aplicar alteração solicitada")
    for proposal in proposals:
        proposal.task_id = task["id"]
    paths = [proposal.path for proposal in proposals]
    agent.tasks.state("awaiting_approval")
    agent._detail(AgentEvent("status", "Aguardando aprovação", " · ".join(paths)))
    started = time.monotonic()
    try:
        approved = agent.policy.permits_paths(task["id"], paths) or (
            agent.approve_edit is not None and agent.approve_edit(proposals, agent._cancelled)
        )
    finally:
        agent._deadline += time.monotonic() - started
    if agent._cancelled is not None and agent._cancelled.is_set():
        raise InvestigationCancelled("Execução cancelada durante a revisão.")
    agent.tasks.event(
        "approval",
        {
            "kind_action": "changes",
            "paths": paths,
            "approved": bool(approved),
            "policy": agent.policy.kind,
        },
    )
    changes = []
    if not approved:
        for proposal in proposals:
            agent.edits.reject(proposal.id)
        agent.tasks.state("blocked", "Alteração rejeitada pelo usuário.")
        return {
            "state": "rejected",
            "changes": [{"path": path, "state": "rejected"} for path in paths],
        }
    agent.tasks.state("executing")
    for position, proposal in enumerate(proposals):
        if agent._cancelled is not None and agent._cancelled.is_set():
            for pending in proposals[position:]:
                agent.edits.reject(pending.id)
            raise InvestigationCancelled("Execução cancelada; alterações aplicadas foram mantidas.")
        # Persist intent before touching source; a crash leaves an inspectable task/checkpoint.
        agent.tasks.event(
            "change_started",
            {
                "proposal": proposal.id,
                "path": proposal.path,
                "before": hashlib.sha256(proposal.before).hexdigest(),
                "after": hashlib.sha256(proposal.after).hexdigest(),
            },
        )
        try:
            agent.edits.apply(proposal.id)
        except (ValueError, OSError) as exc:
            changes.append({"path": proposal.path, "state": proposal.state, "error": str(exc)})
            for pending in proposals[position + 1 :]:
                agent.edits.reject(pending.id)
                changes.append({"path": pending.path, "state": "not_applied"})
            agent.tasks.state(
                "blocked", "Conjunto parcialmente aplicado ou bloqueado por conflito."
            )
            return {"state": "partial" if position else "conflict", "changes": changes}
        entry = {
            "path": proposal.path,
            "state": "applied",
            "checkpoint_id": proposal.checkpoint_id,
        }
        if proposal.checkpoint_warning:
            entry["warning"] = proposal.checkpoint_warning
        changes.append(entry)
        try:
            agent.tasks.changed(proposal.path, proposal.checkpoint_id)
            agent.memory.record_change(proposal, "Alteração aplicada durante a execução da tarefa.")
        except (ValueError, OSError) as exc:
            entry["warning"] = "Arquivo aplicado; registro de continuidade falhou: " + str(exc)
            for pending in proposals[position + 1 :]:
                agent.edits.reject(pending.id)
                changes.append({"path": pending.path, "state": "not_applied"})
            return {"state": "partial", "changes": changes}
        agent._detail(AgentEvent("status", "Alteração aplicada", proposal.path))
    agent.edits.observed.clear()
    result = {"state": "applied", "changes": changes, "validation_required": True}
    if agent.features["lsp"]["enabled"]:
        from codaro.lsp import diagnostics

        result["diagnostics"] = [
            diagnostics(agent.repository, path, agent.features["lsp"], agent._cancelled)
            for path in paths[:2]
            if (agent.repository.root / path).is_file()
        ]
    return result
