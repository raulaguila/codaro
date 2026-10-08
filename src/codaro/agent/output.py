"""Bounded recovery of model output, independent of tasks and permissions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from codaro.agent.messages import textual_tool_call
from codaro.llm import MAX_MESSAGE_CHARS, ContextCapacityError, ModelError, OutputLimitError


@dataclass(frozen=True)
class Recovery:
    kind: str
    instruction: str = ""
    title: str = ""
    detail: str = ""
    partial_text: str = ""
    message: dict | None = None


@dataclass
class OutputRecovery:
    """Own only continuation state; the controller owns API calls and task outcomes."""

    attempts: int = 0
    prefix: str = ""
    messages: list[dict] = field(default_factory=list)

    def merge(self, message: dict) -> dict:
        if not self.prefix:
            return message
        if message.get("tool_calls"):
            raise ModelError("Ferramentas não são permitidas ao continuar texto.")
        combined = self.prefix + (message.get("content") or "")
        if len(combined) > MAX_MESSAGE_CHARS:
            raise ModelError("Resposta continuada excede o limite permitido.")
        self.prefix = ""
        return {**message, "content": combined}

    def recover(self, error: OutputLimitError, turn: list[dict], input_limit: int) -> Recovery:
        partial = error.partial_text
        plain_text = bool(partial.strip()) and not (
            textual_tool_call(partial)
            or re.search(r'<tool_call>|"(?:tool_calls|function|arguments|parameters)"\s*:', partial)
        )
        if self.attempts >= 2:
            if plain_text and self.prefix:
                combined = self.prefix + partial
                if len(combined) <= MAX_MESSAGE_CHARS:
                    notice = (
                        "\n\nResposta parcial: o provedor interrompeu a geração novamente. "
                        "O texto foi preservado; peça para continuar."
                    )
                    return Recovery(
                        "partial",
                        partial_text=partial,
                        message={
                            "role": "assistant",
                            "content": combined[: MAX_MESSAGE_CHARS - len(notice)] + notice,
                        },
                    )
            raise ContextCapacityError(
                "O modelo não finalizou esta etapa. O progresso foi salvo; "
                "podemos continuar com uma parte menor da tarefa."
            ) from error
        self.attempts += 1
        if not plain_text:
            return Recovery(
                "retry",
                instruction=(
                    "A saída anterior foi truncada e não foi aceita. "
                    "Responda em até 400 palavras, priorizando a conclusão. "
                    "Não enumere todos os arquivos; agrupe módulos e explique o essencial. "
                    "Reutilize resultados já presentes e divida operações em chamadas menores. "
                    "Nunca execute JSON incompleto nem repita ações já aplicadas."
                ),
                title="Limite de resposta atingido",
                detail="Gerando uma versão mais curta",
            )
        if len(self.prefix) + len(partial) > MAX_MESSAGE_CHARS:
            raise ModelError("Resposta continuada excede o limite permitido.") from error
        self.prefix += partial
        turn[:] = [item for item in turn if not any(item is old for old in self.messages)]
        tail = self.prefix[-min(2400, max(256, input_limit // 2)) :]
        self.messages = [
            {"role": "assistant", "content": tail},
            {
                "role": "user",
                "content": (
                    "A resposta foi interrompida pelo limite de geração. "
                    "O trecho acima é o final do texto já apresentado. "
                    "Continue exatamente de onde parou, sem repetir o início. "
                    "Conclua brevemente, sem novas ferramentas ou ações. "
                    "Esta continuação não autoriza mudanças no projeto."
                ),
            },
        ]
        turn.extend(self.messages)
        return Recovery(
            "continue",
            instruction=(
                "Continue apenas a resposta textual interrompida e conclua. "
                "Não repita o início nem simule ferramentas."
            ),
            title="Resposta interrompida",
            detail="Continuando o texto preservado",
            partial_text=partial,
        )
