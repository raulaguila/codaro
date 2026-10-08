"""Read-only child investigations, never permission or proof for parent edits."""

import copy
import math

from codaro.features import DEFAULTS
from codaro.provider import ModelError, create_provider
from codaro.runtime import remaining_seconds, request_budget
from codaro.tool_registry import Tool, definition
from codaro.trace import current_flow

EXPLORE_TOOL = definition(
    "explore_code",
    "Delegue uma investigação curta, somente leitura, com contexto separado. "
    "Informe objetivo e escopo. O resultado não autoriza edição: releia os arquivos atuais.",
    {"objective": {"type": "string", "minLength": 1, "maxLength": 2000}},
    ["objective"],
)


def register_exploration(agent):
    def explore(args):
        from codaro.agent import Agent

        settings = getattr(agent.provider, "settings", None)
        if settings is None:
            raise ValueError("Exploração requer provedor configurado.")
        if not agent.features["exploration"]:
            raise ValueError("Exploração desativada.")
        remaining = remaining_seconds()
        if remaining is not None and remaining < 2:
            raise ModelError("Tempo insuficiente para iniciar exploração.")
        child_features = copy.deepcopy(DEFAULTS)
        child_features.update(artifacts=False, semantic_compaction=False, exploration=False)
        child = Agent(
            agent.repository,
            create_provider(settings, transport=getattr(agent.provider, "transport", None)),
            mode="ask",
            max_steps=6,
            tool_budget=12_000,
            history_budget=0,
            persist_memory=False,
            features=child_features,
            max_seconds=min(120, max(1, math.floor(remaining))) if remaining else 120,
        )
        child._trace_name = "exploration.json"
        child.memory.redact = agent.memory.redact
        child.tasks.redact = agent.memory.redact
        child.artifacts.redact = agent.memory.redact
        parent_flow = current_flow.get()
        before = request_budget.get().requests if request_budget.get() else 0
        budget = request_budget.get()
        if budget:
            budget.reserved_requests += 1
            budget.reserved_tokens += agent.context_window
        try:
            answer = child.ask(args["objective"], cancelled=agent._cancelled)
        finally:
            if budget:
                budget.reserved_requests -= 1
                budget.reserved_tokens -= agent.context_window
        if parent_flow:
            parent_flow.append_event(
                "exploration_result",
                {
                    "objective": args["objective"],
                    "answer": answer,
                    "requests": request_budget.get().requests - before
                    if request_budget.get()
                    else 0,
                },
            )
        return {
            "answer": answer[:3000],
            "truncated": len(answer) > 3000,
            "source": "read_only_child_report_not_current_edit_evidence",
        }

    agent.registry.register(Tool(EXPLORE_TOOL, lazy=True, handler=explore, source="exploration"))
