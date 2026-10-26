"""Recovery actions tied to the outcome of one interaction."""

import re

from textual.containers import Horizontal
from textual.widgets import Button, Collapsible, Static

from codaro.llm.errors import ContextLimitError, OutputLimitError


def failure_kind(error):
    if isinstance(error, (ContextLimitError, OutputLimitError)):
        return "limit"
    message = str(error).lower()
    if re.search(r"\bhttp\s+(401|403)\b", message):
        return "authentication"
    if re.search(r"\bhttp\s+(429|5\d\d)\b", message) or any(
        word in message for word in ("indisponível", "conectar", "conexão", "timeout")
    ):
        return "unavailable"
    return "provider"


class RecoveryCard(Collapsible):
    """Keep diagnostic details available after the attempt is resolved."""

    def __init__(self, message, kind):
        titles = {
            "cancelled": "Execução interrompida",
            "authentication": "Não foi possível autenticar no provedor",
            "unavailable": "A conexão com o provedor foi interrompida",
            "context": "Vamos reduzir o escopo da pergunta",
            "limit": "Resposta interrompida pelo limite do modelo",
        }
        actions = {
            "cancelled": ["retry-question", "recover-changes"],
            "authentication": ["recover-provider"],
            "unavailable": ["retry-question", "recover-model", "recover-provider"],
            "context": ["retry-question", "recover-model"],
            "limit": ["retry-question", "recover-model"],
            "error": ["retry-question"],
        }.get(kind, ["retry-question", "recover-provider", "recover-model"])
        labels = {
            "retry-question": "Revisar e retomar" if kind == "cancelled" else "Tentar novamente",
            "recover-provider": "Configurar provedor",
            "recover-model": "Trocar modelo",
            "recover-changes": "Ver alterações",
        }
        self.actions = Horizontal(
            *(Button(labels[action], id=action) for action in actions),
            classes="recovery-actions",
        )
        super().__init__(
            Static(
                "Confira a chave cadastrada para continuar."
                if kind == "authentication"
                else "Seu progresso e rascunho foram preservados.",
                classes="recovery-description",
                markup=False,
            ),
            self.actions,
            Collapsible(
                Static(message, classes="notice", markup=False), title="Detalhes", collapsed=True
            ),
            title=titles.get(kind, "Não foi possível concluir"),
            collapsed=False,
            classes="recovery-card neutral" if kind == "cancelled" else "recovery-card",
        )

    def retire(self):
        self.actions.remove()
        self.collapsed = True

    def resolve(self):
        self.title = "Tentativa resolvida · detalhes"
        self.set_class(True, "resolved")
        self.collapsed = True
