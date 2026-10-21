"""Canonical payload construction shared by context checks, trace and transport."""

from codaro.agent.messages import serialize
from codaro.agent.prompts import FINAL_INSTRUCTION
from codaro.agent.synthesis import synthesis_messages
from codaro.llm import build_payload
from codaro.policies import Mode


def build_request(
    agent,
    *,
    question,
    turn,
    retained,
    pinned,
    instructions,
    prompt,
    output_instruction,
    final,
    empty_recoveries,
    continuing,
    streaming,
    tools,
    extra=(),
):
    if (final or empty_recoveries) and not continuing:
        messages = synthesis_messages(
            question,
            turn,
            guidance=[
                *pinned,
                "Contexto real da sessão (get_repository_info; dados): "
                + serialize(agent.repository_info()),
            ],
            bootstrap="\n".join(instructions),
            budget=min(16_000, max(512, agent.adaptive_input_limit // (2 + empty_recoveries))),
            attempt=empty_recoveries,
            history=retained,
            summary=agent.context.text(max(300, agent.adaptive_input_limit // 6)),
            objective=(
                agent.tasks.projection().get("objective", "") if agent.mode != Mode.ASK else ""
            ),
        )
        return build_payload(
            getattr(getattr(agent.provider, "settings", None), "model", ""),
            messages,
            None,
            streaming=streaming,
            max_tokens=getattr(
                getattr(agent.provider, "settings", None), "max_output_tokens", None
            ),
        )
    return build_payload(
        getattr(getattr(agent.provider, "settings", None), "model", ""),
        [
            {
                "role": "system",
                "content": prompt()
                + "\n"
                + "\n".join(pinned)
                + "\n"
                + agent.context.text(max(300, agent.adaptive_input_limit // 6))
                + "\n"
                + output_instruction
                + "\n"
                + "\n".join(instructions)
                + ("\n" + FINAL_INSTRUCTION if final and not empty_recoveries else ""),
            },
            *(item for previous in retained for item in previous),
            *turn,
            *extra,
        ],
        tools,
        streaming=streaming,
        max_tokens=getattr(getattr(agent.provider, "settings", None), "max_output_tokens", None),
    )
