"""Preflight and conflict-preserving recovery of an approved change set."""

from codaro.agent.events import AgentEvent


def preflight(agent, proposals):
    for proposal in proposals:
        if proposal.state != "pending":
            raise ValueError("Proposta já resolvida.")
        if proposal.before_exists:
            path = agent.repository.resolve_file(proposal.path)
            if agent.repository.read_bytes(path) != proposal.before:
                raise ValueError("Arquivo mudou após a proposta: " + proposal.path)
        elif agent.repository.resolve_destination(proposal.path).exists():
            raise ValueError("Destino já existe: " + proposal.path)


def rollback(agent, applied):
    failures = []
    for proposal in reversed(applied):
        try:
            inverse = agent.edits._proposal(
                proposal.path,
                proposal.after,
                proposal.before,
                "Reverter lote aprovado após falha",
                before_exists=proposal.after_exists,
                after_exists=proposal.before_exists,
                file_mode=proposal.file_mode,
            )
            inverse.undo_of, inverse.task_id = proposal.checkpoint_id, proposal.task_id
            agent.edits.apply(inverse.id)
            proposal.state = "rolled_back"
            agent.tasks.changed(proposal.path, inverse.checkpoint_id)
            agent.memory.record_change(inverse, "Lote revertido após falha.")
            agent.tasks.event(
                "change_rolled_back", {"path": proposal.path, "checkpoint": inverse.checkpoint_id}
            )
            agent._detail(AgentEvent("status", "Alteração revertida", proposal.path))
        except (ValueError, OSError) as exc:
            failures.append({"path": proposal.path, "error": str(exc)[:200]})
    agent.edits.observed.clear()
    return failures
