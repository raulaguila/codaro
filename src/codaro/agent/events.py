from __future__ import annotations

from dataclasses import dataclass

from codaro.llm import (
    RequestCancelled,
)

InvestigationCancelled = RequestCancelled


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    title: str
    detail: str = ""
    state: str = ""
    elapsed_ms: float | None = None
    context_chars: int | None = None
    context_tokens: int | None = None
    context_limit: int | None = None
    counter_method: str = ""
    reported_tokens: int | None = None
