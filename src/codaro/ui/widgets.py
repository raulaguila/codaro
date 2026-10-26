from __future__ import annotations

import logging

from rich.markdown import Markdown as RichMarkdown
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Collapsible, Markdown, Static

from codaro.agent import AgentEvent
from codaro.edits import EditProposal
from codaro.index import safe_preview
from codaro.llm import (
    MAX_MESSAGE_CHARS,
)
from codaro.ui.messages import AssistantMessage

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class GenerationPreview(Collapsible):
    """Visible live text, promoted only after the agent accepts an answer."""

    def __init__(self, *, reasoning: bool = False):
        self.reasoning = reasoning
        self.text = ""
        self.state = "generating"
        self.reason = ""
        self.content = Static("", markup=False, classes="generation-text")
        super().__init__(
            *([] if reasoning else [Static("Codaro", classes="live-speaker", markup=False)]),
            self.content,
            title=("Raciocínio · em andamento" if reasoning else "Respondendo…"),
            classes="generation-preview reasoning-preview"
            if reasoning
            else "generation-preview response-preview live-answer",
            collapsed=False,
        )
        self.tooltip = "Texto provisório; a resposta final aparece ao concluir a interação."

    def update_text(self, text: str):
        limit = (
            4000
            if self.reasoning or self.state not in {"generating", "answer"}
            else MAX_MESSAGE_CHARS
        )
        self.text = text[:limit]
        suffix = (
            "\n… prévia limitada; fluxo completo em .codaro/prompt.json"
            if len(text) > limit
            else ""
        )
        detail = f"Motivo: {self.reason}\n\n" if self.state == "retry" and self.reason else ""
        self.content.update(detail + self.text + suffix)
        # Render completed Markdown blocks; keep unmatched fences literal while streaming.
        stable = (
            not self.reasoning
            and self.state in {"generating", "answer"}
            and self.text.count("```") % 2 == 0
        )
        if stable:
            self.content.update(RichMarkdown(self.text + suffix))

    def finish(self, state: str, reason: str = ""):
        self.state = state
        if reason:
            self.reason = safe_preview(reason)
            self.tooltip = self.reason
        if self.reasoning:
            self.title = (
                "Raciocínio enviado pelo modelo · interrompido"
                if state in {"retry", "cancelled"}
                else "Raciocínio · etapa encerrada"
            )
            self.collapsed = True
            return
        self.title = {
            "tools": "Etapa intermediária · chamada de ferramentas",
            "retry": "Revisando resposta…",
            "answer": "Concluindo resposta…",
            "accepted": "Prévia da resposta · geração concluída",
            "cancelled": "Prévia interrompida · sem resposta concluída",
        }.get(state, "Prévia interrompida · sem resposta concluída")
        if state != "answer":
            self.update_text(self.text)
            self.collapsed = True
            self.remove_class("live-answer")

    def accept(self, answer: str) -> Markdown:
        self.state = "accepted"
        self.title = "Codaro · resposta final"
        self.collapsed = False
        reply = AssistantMessage(answer)

        async def mount_answer():
            parent = self.parent
            if not self.is_attached or parent is None:
                return
            speaker = Static("Codaro", classes="speaker", markup=False)
            # The final answer is a sibling in the conversation, never a child
            # of a reasoning or provisional-generation card.
            await parent.mount(speaker, reply, before=self)
            # Clear/new conversation may remove widgets while mounting yields.
            if not all(widget.is_attached for widget in (self, reply, speaker)):
                return
            self.content.display = False
            self.display = False

        self.call_after_refresh(mount_answer)
        return reply


class ReasoningGroup(Collapsible):
    """One quiet reasoning archive per interaction; updates respect manual toggles."""

    def __init__(self, preview, *, collapsed=True):
        self.steps = 1
        super().__init__(
            preview,
            title="Raciocínio do modelo · 1 etapa",
            collapsed=collapsed,
            classes="reasoning-group",
            collapsed_symbol="▸",
            expanded_symbol="▾",
        )

    def add_preview(self, preview):
        self.steps += 1
        self.title = f"Raciocínio do modelo · {self.steps} etapas"

        def mount_step():
            contents = list(self.query(Collapsible.Contents))
            if self.is_attached and contents:
                contents[0].mount(preview)

        self.call_after_refresh(mount_step)


class ActivityGroup(Collapsible):
    """One expandable activity summary per turn; errors remain visible."""

    def __init__(self):
        self.events: list[AgentEvent] = []
        self.generations: list[GenerationPreview] = []
        self.details = Static("", markup=False)
        self.has_reasoning = False
        super().__init__(
            self.details,
            title="Etapas · detalhes",
            classes="tool-card",
            collapsed=True,
            collapsed_symbol="▸",
            expanded_symbol="▾",
        )

    def archive(self, preview):
        archived = Collapsible(
            Static(preview.text[:4000], markup=False),
            title=preview.title,
            collapsed=True,
            classes="intermediate-step",
            collapsed_symbol="▸",
            expanded_symbol="▾",
        )

        def mount_archive():
            contents = list(self.query(Collapsible.Contents))
            if self.is_attached and contents:
                contents[0].mount(archived)

        self.call_after_refresh(mount_archive)
        preview.display = False

    def add_preview(self, preview: GenerationPreview):
        self.generations.append(preview)

    def finish(self, successful: bool):
        if not self.events and not self.has_reasoning:
            self.title = "Geração concluída" if successful else "Geração interrompida"
            self.display = False
        parent = self.parent
        if hasattr(parent, "finish"):
            parent.finish()

    def add(self, event: AgentEvent):
        self.events.append(event)
        reads = {
            item.detail.split("\n", 1)[0] for item in self.events if item.title.startswith("Ler ")
        }
        searches = sum(item.title == "Buscar código" for item in self.events)
        errors = sum(item.state == "error" for item in self.events)
        count = len(self.events)
        parts = [f"{count} {'ação' if count == 1 else 'ações'}"]
        if reads:
            parts.append(f"{len(reads)} {'leitura' if len(reads) == 1 else 'leituras'}")
        if searches:
            parts.append(f"{searches} {'busca' if searches == 1 else 'buscas'}")
        if errors:
            parts.append(f"{errors} {'erro' if errors == 1 else 'erros'}")
        self.title = "Etapas · " + " · ".join(parts)
        self.set_class(bool(errors), "error")
        self.set_class(not errors, "success")
        text = safe_preview(
            "\n\n".join(
                f"{item.title} · {item.elapsed_ms or 0:.0f} ms\n{item.detail}"
                for item in self.events
            )
        )
        if len(text) > 48_000:
            text = "… detalhes anteriores em .codaro/prompt.json\n" + text[-48_000:]
        self.details.update("FERRAMENTAS\n" + text)


class ProposalCard(Vertical):
    DEFAULT_CSS = """
    ProposalCard { height: auto; margin: 1; padding: 1; border: round $warning; }
    ProposalCard Static { height: auto; }
    ProposalCard Button { margin-top: 1; }
    """

    def __init__(self, proposal: EditProposal):
        super().__init__()
        self.proposal = proposal

    def compose(self) -> ComposeResult:
        yield Static(
            safe_preview(f"Edição pendente · {self.proposal.path}\n{self.proposal.reason}"),
            markup=False,
        )
        yield Button("Revisar diff", id=f"review-{self.proposal.id}", variant="primary")

    def resolve(self, message: str):
        self.query_one(Static).update(safe_preview(message))
        self.query_one(Button).disabled = True
