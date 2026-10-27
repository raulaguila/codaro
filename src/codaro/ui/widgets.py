from __future__ import annotations

import logging
import time

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
from codaro.ui.sections import ProcessSection

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class GenerationPreview(ProcessSection):
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
            self.remove_class("live-answer")

    def accept(self, answer: str) -> Markdown:
        self.state = "accepted"
        self.title = "Codaro · resposta final"
        self.collapsed = False
        reply = AssistantMessage(answer)

        async def mount_answer():
            parent = self.app.query_one("#conversation") if self.is_attached else None
            if not self.is_attached or parent is None:
                return
            speaker = Static("Codaro", classes="speaker", markup=False)
            # The final answer is a sibling in the conversation, never a child
            # of a reasoning or provisional-generation card.
            await parent.mount(speaker, reply)
            # Clear/new conversation may remove widgets while mounting yields.
            if not all(widget.is_attached for widget in (self, reply, speaker)):
                return
            self.content.display = False
            self.display = False

        self.call_after_refresh(mount_answer)
        return reply


class ReasoningGroup(ProcessSection):
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


class ActivityGroup(ProcessSection):
    """One expandable activity summary per turn; errors remain visible."""

    def __init__(self):
        self.events: list[AgentEvent] = []
        self.generations: list[GenerationPreview] = []
        self.details = Static("", markup=False)
        self.has_reasoning = False
        self.sequence = []
        self.mounted_steps = 0
        self.mounting_steps = False
        self.running_tool = None
        super().__init__(
            self.details,
            title="Etapas · detalhes",
            classes="tool-card",
            collapsed=False,
            collapsed_symbol="▸",
            expanded_symbol="▾",
        )

    def archive(self, preview):
        # The same widget remains at its original chronological position.
        # Only successful completion folds the whole process below one summary.
        preview.add_class("intermediate-step")

    def append_step(self, widget):
        self.sequence.append(widget)
        self.app.transcript_updated()
        if self.is_mounted:
            self.call_later(self.mount_pending_steps)

    async def on_mount(self):
        await self.mount_pending_steps()

    async def mount_pending_steps(self):
        if self.mounting_steps or not self.is_attached:
            return
        contents = list(self.query(Collapsible.Contents))
        if not contents or not contents[0].is_running:
            return
        self.mounting_steps = True
        try:
            while self.is_attached and contents[0].is_running:
                pending = self.sequence[self.mounted_steps :]
                if not pending:
                    break
                self.mounted_steps += len(pending)
                await contents[0].mount(*pending)
        finally:
            self.mounting_steps = False

    def add_preview(self, preview: GenerationPreview):
        self.generations.append(preview)
        self.append_step(preview)

    def start(self, event):
        self.running_tool = Static(
            safe_preview(f"◌ {event.title}\n{event.detail}"),
            markup=False,
            classes="timeline-tool",
        )
        self.append_step(self.running_tool)

    def finish(self, successful: bool):
        if not self.events and not self.has_reasoning and len(self.generations) <= 1:
            self.title = "Geração concluída" if successful else "Geração interrompida"
            self.display = False
        parent = self.parent
        if hasattr(parent, "finish"):
            parent.finish()
        self.collapsed = True
        elapsed = max(1, round(time.monotonic() - getattr(parent, "started", time.monotonic())))
        self.title = (
            "Concluído" if successful else "Interrompido"
        ) + f" em {elapsed}s · {len(self.events)} ações · Ver processo"

    def add(self, event: AgentEvent):
        self.events.append(event)
        text = safe_preview(
            f"{'✗' if event.state == 'error' else '✓'} {event.title} · "
            f"{event.elapsed_ms or 0:.0f} ms\n{event.detail}"
        )
        if self.running_tool is not None:
            self.running_tool.update(text)
            self.running_tool.set_class(event.state == "error", "error")
            self.running_tool = None
        else:
            self.append_step(Static(text, markup=False, classes="timeline-tool"))
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
        self.details.display = False


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
