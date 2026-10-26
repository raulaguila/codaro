"""Compaction of completed protocol batches and their recoverable continuity."""

from codaro.context import compact_batch


def compact_completed(agent, turn, cancelled=None):
    before = list(turn)
    record = compact_batch(turn)
    if record and record["kind"] == "tool_batch" and agent.features["semantic_compaction"]:
        retained_ids = {id(message) for message in turn}
        discarded = [message for message in before if id(message) not in retained_ids]
        summary = agent.context.summarize(
            agent.provider,
            discarded,
            input_limit=agent.adaptive_input_limit,
            redact=agent.memory.redact,
            cancelled=cancelled,
        )
        if summary:
            record["continuity_summary"] = summary
            if not agent.tasks.ephemeral:
                try:
                    agent.context.save(
                        agent.sessions.summary_path(agent.session_id), agent.memory.redact
                    )
                except (OSError, ValueError):
                    pass
    return record
