from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from rich.syntax import Syntax
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Footer, Header, Markdown, RichLog, Static, TextArea

from codaro.agent import Agent, AgentEvent, InvestigationCancelled
from codaro.edits import EditProposal
from codaro.index import safe_preview
from codaro.provider import ModelError

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Prompt(TextArea):
    """A wrapping composer with explicit submission and portable newline shortcuts."""

    BINDINGS = [
        Binding("enter", "submit", "Enviar", show=False, priority=True),
        Binding("alt+enter,shift+enter", "newline", "Nova linha", show=False, priority=True),
    ]

    class Submitted(Message):
        def __init__(self, input: Prompt):
            super().__init__()
            self.input = input
            self.value = input.text

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, value: str):
        self.load_text(value)

    def action_submit(self):
        if not self.disabled:
            self.post_message(self.Submitted(self))

    def action_newline(self):
        if not self.disabled:
            self.replace(
                "\n", self.selection.start, self.selection.end, maintain_selection_offset=False
            )


def short_path(root: Path, limit: int = 40) -> str:
    try:
        relative = root.relative_to(Path.home())
        display = "~" if relative == Path(".") else "~/" + relative.as_posix()
    except ValueError:
        display = str(root)
    return display if len(display) <= limit else "…" + display[-(limit - 1) :]


class EditReview(ModalScreen[str]):
    DEFAULT_CSS = """
    EditReview { align: center middle; background: #000000 65%; }
    #review { width: 95%; height: 90%; border: round #38bdf8; background: #111827; }
    #review-title { height: auto; padding: 1 2; color: #38bdf8; text-style: bold; }
    #review-diff { height: 1fr; padding: 0 1; }
    #review-actions { height: auto; align-horizontal: center; padding: 1; }
    #review-actions Button { margin: 0 1; min-width: 10; }
    """
    BINDINGS = [Binding("escape", "back", "Voltar")]

    def __init__(self, proposal: EditProposal):
        super().__init__()
        self.proposal = proposal

    def compose(self) -> ComposeResult:
        with Vertical(id="review"):
            yield Static(
                safe_preview(f"Revisar edição · {self.proposal.path}\n{self.proposal.reason}"),
                id="review-title",
                markup=False,
            )
            with VerticalScroll(id="review-diff"):
                yield Static(
                    Syntax(
                        safe_preview(self.proposal.diff), "diff", theme="monokai", word_wrap=True
                    )
                )
            with Horizontal(id="review-actions"):
                yield Button("Aplicar", variant="success", id="apply-edit")
                yield Button("Rejeitar", variant="error", id="reject-edit")
                yield Button("Voltar", id="back-edit")

    def on_mount(self):
        # Enter must never approve a patch just because the modal appeared.
        self.query_one("#back-edit", Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss({"apply-edit": "apply", "reject-edit": "reject"}.get(event.button.id, "back"))

    def action_back(self):
        self.dismiss("back")


class ProposalCard(Vertical):
    DEFAULT_CSS = """
    ProposalCard { height: auto; margin: 1; padding: 1; border: round #fbbf24; }
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


class CodaroApp(App):
    TITLE = "Codaro · explore seu código"
    CSS = """
    Screen { background: #0f172a; }
    Header { background: #111827; color: #f1f5f9; }
    #session { height: 1; margin: 0 2; color: #94a3b8; }
    #session.insecure { color: #fbbf24; }
    #main { height: 1fr; }
    #conversation { width: 1fr; border: round #334155; padding: 0 1; }
    #sidebar { width: 32; max-width: 38; margin-left: 1; }
    .narrow #sidebar, .sidebar-hidden #sidebar { display: none; }
    #repository { height: auto; max-height: 17; border: round #334155; padding: 1; }
    #activity { display: none; height: 1fr; border: round #334155; padding: 0 1; }
    #current-action { display: none; height: auto; padding: 1; color: #38bdf8; }
    #welcome { height: auto; max-width: 78; margin: 1; }
    #welcome-title { height: 2; color: #f1f5f9; text-style: bold; }
    #welcome-description { height: auto; margin-bottom: 1; color: #94a3b8; }
    #welcome Button { width: 100%; margin-bottom: 1; background: #172033; border: round #334155; }
    #welcome Button:hover, #welcome Button:focus { border: round #38bdf8; }
    #prompt { height: 3; max-height: 8; margin: 0 1; border: round #475569; padding: 0 1; }
    #prompt:focus { border: round #38bdf8; }
    #prompt-hint { height: 1; margin: 0 2; color: #94a3b8; }
    #status { height: 1; margin: 0 2; color: #cbd5e1; }
    .question { height: auto; margin: 1 0; padding: 1; background: #172033; }
    .assistant { margin: 0; padding: 0 1; background: #0f172a; }
    .speaker { height: 1; margin: 1 1 0 1; color: #e2e8f0; text-style: bold; }
    .tool-card { height: auto; margin: 0 1; border: none; border-left: thick #475569; padding: 0; }
    .tool-card.error { border-left: thick #f87171; }
    .tool-card.success { border-left: thick #34d399; }
    .tool-card CollapsibleTitle { color: #94a3b8; }
    .tool-card > Contents { padding: 0 1; }
    .notice { height: auto; margin: 1; color: #fbbf24; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Sair", priority=True, key_display="Ctrl+Q"),
        Binding("ctrl+l", "clear_chat", "Limpar", priority=True, key_display="Ctrl+L"),
        Binding("ctrl+x", "cancel", "Cancelar", priority=True, key_display="Ctrl+X"),
        Binding("ctrl+b", "toggle_sidebar", "Painel", priority=True, key_display="Ctrl+B"),
        Binding("ctrl+p", "command_palette", "Comandos", priority=True, key_display="Ctrl+P"),
    ]

    def __init__(self, agent: Agent):
        super().__init__()
        self.agent = agent
        self.busy = False
        self.cancelled = threading.Event()
        self.response_text = ""
        self.rendered_text = ""
        self.reply: Markdown | None = None
        self.speaker: Static | None = None
        self.context_chars = 0
        self.proposal_cards: dict[str, ProposalCard] = {}
        self.sidebar_visible = True
        self.has_activity = False
        self.prompt_too_long = False

    def welcome(self) -> Vertical:
        return Vertical(
            Static("Vamos trabalhar no seu projeto", id="welcome-title", markup=False),
            Static(
                "Escolha um ponto de partida ou escreva sua pergunta.\n"
                "As respostas usam os arquivos locais; edições exigem sua aprovação.",
                id="welcome-description",
                markup=False,
            ),
            Button("Explorar projeto", id="suggest-explore"),
            Button("Encontrar código", id="suggest-search"),
            Button("Propor mudança", id="suggest-edit", disabled=not self.agent.allow_edits),
            id="welcome",
        )

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="session", markup=False)
        with Horizontal(id="main"):
            with VerticalScroll(id="conversation"):
                yield self.welcome()
            with Vertical(id="sidebar"):
                yield Static("", id="repository", markup=False)
                yield Static("", id="current-action", markup=False)
                yield RichLog(id="activity", wrap=True, markup=False, max_lines=1000)
        yield Static("Pronto", id="status")
        yield Prompt(
            placeholder="Pergunte ou peça uma mudança…",
            id="prompt",
            show_line_numbers=False,
            highlight_cursor_line=False,
        )
        yield Static(
            "Enter envia · Alt+Enter nova linha · /pwd diretório", id="prompt-hint", markup=False
        )
        yield Footer(show_command_palette=False)

    def on_mount(self):
        self.update_layout()
        settings = self.agent.provider.settings
        mode = "Com aprovação" if self.agent.allow_edits else "Somente leitura"
        tls = "TLS sem verificação" if settings.tls_insecure else "TLS verificação ativa"
        if settings.base_url.startswith("http://"):
            tls = "HTTP"
        root = short_path(self.agent.repository.root)
        summary = f"{root} · {settings.model} · {mode}"
        summary = f"{tls} · {summary}" if settings.tls_insecure else f"{summary} · {tls}"
        self.query_one("#session", Static).update(safe_preview(summary))
        self.query_one("#session", Static).set_class(settings.tls_insecure, "insecure")
        repository = self.query_one("#repository", Static)
        repository.border_title = "Sessão"
        repository.tooltip = str(self.agent.repository.root)
        repository.update(
            safe_preview(
                f"PROJETO\n{root}\n\nMODELO\n{settings.model}\n\nEDIÇÃO\n{mode}\n\nCONEXÃO\n{tls}"
            )
        )
        self.query_one("#activity", RichLog).border_title = "Atividade"
        self.set_interval(0.08, self.flush_response)
        self.query_one(Prompt).focus()

    def update_layout(self):
        screen = self.screen_stack[0]
        screen.set_class(self.size.width < 90, "narrow")
        screen.set_class(not self.sidebar_visible, "sidebar-hidden")

    def on_resize(self):
        self.update_layout()

    def action_toggle_sidebar(self):
        self.sidebar_visible = not self.sidebar_visible
        self.update_layout()

    def hide_welcome(self):
        for welcome in self.query("#welcome"):
            welcome.display = False

    def log_activity(self, message):
        self.has_activity = True
        log = self.query_one("#activity", RichLog)
        log.display = True
        log.write(message)

    def on_text_area_changed(self, event: TextArea.Changed):
        if event.text_area.id != "prompt":
            return
        prompt = event.text_area
        prompt.styles.height = min(8, max(3, prompt.wrapped_document.height + 2))
        if len(prompt.text) > 8000:
            self.prompt_too_long = True
            self.query_one("#status", Static).update(
                "Mensagem excede 8.000 caracteres · reduza antes de enviar"
            )
        elif self.prompt_too_long:
            self.prompt_too_long = False
            self.query_one("#status", Static).update(
                "Aguardando revisão de edições" if self.agent.edits.pending else "Pronto"
            )

    def mount_message(self, widget):
        conversation = self.query_one("#conversation", VerticalScroll)
        if len(conversation.children) >= 100:
            for old in list(conversation.children)[:10]:
                old.remove()
        conversation.mount(widget)
        self.call_after_refresh(conversation.scroll_end, animate=False)

    def on_prompt_submitted(self, event: Prompt.Submitted):
        question = event.value.strip()
        if not question or self.busy:
            return
        if len(event.value) > 8000:
            self.query_one("#status", Static).update(
                "Mensagem excede 8.000 caracteres · reduza antes de enviar"
            )
            return
        if question == "/pwd":
            self.hide_welcome()
            event.input.value = ""
            self.mount_message(
                Static(
                    f"Diretório da sessão\n{safe_preview(str(self.agent.repository.root))}",
                    classes="question",
                    markup=False,
                )
            )
            return
        if self.agent.edits.pending:
            self.mount_message(
                Static("Revise as edições pendentes antes de outra pergunta.", classes="notice")
            )
            return
        self.hide_welcome()
        self.proposal_cards.clear()
        self.busy = True
        self.cancelled.clear()
        self.response_text = self.rendered_text = ""
        self.reply = None
        self.speaker = None
        event.input.value = ""
        event.input.disabled = True
        self.mount_message(
            Static(f"Você\n{safe_preview(question)}", classes="question", markup=False)
        )
        self.query_one("#status", Static).update("Investigando…")
        self.investigate(question)

    def deliver(self, callback, *args):
        if self.is_running:
            try:
                self.call_from_thread(callback, *args)
            except RuntimeError:
                return

    @work(thread=True, exclusive=True)
    def investigate(self, question: str):
        successful = False
        try:
            answer = self.agent.ask(
                question,
                cancelled=self.cancelled,
                on_delta=lambda delta: self.deliver(self.append_delta, delta),
                on_detail=lambda event: self.deliver(self.activity, event),
            )
            successful = True
        except InvestigationCancelled:
            answer = "Investigação cancelada."
        except (ModelError, ValueError, OSError) as exc:
            answer = f"Não foi possível concluir: {exc}"
        except sqlite3.Error:
            answer = (
                "Falha no índice SQLite. Confira permissões, espaço livre e integridade do índice."
            )
        except Exception:
            logger.exception("Unexpected investigation failure")
            answer = "Falha inesperada. Execute codaro ask para diagnosticar o fluxo."
        self.deliver(self.finish, answer, successful)

    def activity(self, event: AgentEvent):
        if event.kind == "model_start":
            self.flush_response()
            self.response_text = self.rendered_text = ""
            self.reply = None
            self.speaker = None
            self.context_chars = event.context_chars or 0
            self.query_one("#status", Static).update("Consultando modelo…")
        elif event.kind == "model_end":
            if event.state == "answer":
                self.flush_response()
            else:
                self.discard_response()
        elif event.kind == "tool_start":
            action = self.query_one("#current-action", Static)
            action.update(safe_preview(f"Em andamento\n{event.title}\n{event.detail}"))
            action.display = True
            self.query_one("#status", Static).update(f"{event.title}…")
        elif event.kind == "tool_end":
            duration = f"{event.elapsed_ms:.0f} ms" if event.elapsed_ms is not None else ""
            text = safe_preview(f"{event.title} · {duration}\n{event.detail}")
            color = (
                "red"
                if event.state == "error"
                else "yellow"
                if event.state == "pending"
                else "green"
            )
            log = Text(event.title + " · " + duration, style=color)
            log.append("\n" + safe_preview(event.detail) + "\n", style="dim")
            self.query_one("#current-action", Static).display = False
            self.log_activity(log)
            outcome = event.detail.split("\n")[-1]
            self.mount_message(
                Collapsible(
                    Static(text, markup=False),
                    title=safe_preview(f"{event.title} · {duration} · {outcome}"),
                    collapsed=event.state != "error",
                    classes=f"tool-card {event.state}",
                )
            )
            self.query_one("#status", Static).update(f"{event.title} · {duration}")
        else:
            self.log_activity(safe_preview(f"{event.title} · {event.detail}".rstrip(" ·")))
            self.query_one("#status", Static).update(safe_preview(event.title))

    def append_delta(self, delta: str):
        self.response_text += safe_preview(delta)
        self.query_one("#status", Static).update("Respondendo… · Ctrl+X para cancelar")
        # Render the first fragment immediately; subsequent fragments are coalesced by the timer.
        if self.reply is None:
            self.flush_response()

    def flush_response(self):
        if not self.response_text or self.response_text == self.rendered_text:
            return
        conversation = self.query_one("#conversation", VerticalScroll)
        follow = conversation.is_vertical_scroll_end
        if self.reply is None:
            self.speaker = Static("Codaro", classes="speaker", markup=False)
            self.mount_message(self.speaker)
            self.reply = Markdown(self.response_text, classes="assistant", open_links=False)
            self.mount_message(self.reply)
        else:
            self.reply.update(self.response_text)
        self.rendered_text = self.response_text
        if follow:
            self.call_after_refresh(conversation.scroll_end, animate=False)

    def discard_response(self):
        self.response_text = self.rendered_text = ""
        if self.reply is not None:
            self.reply.remove()
            self.reply = None
        if self.speaker is not None:
            self.speaker.remove()
            self.speaker = None

    def finish(self, answer: str, successful: bool = True):
        if successful:
            self.response_text = safe_preview(answer)
            self.flush_response()
        else:
            self.discard_response()
            self.mount_message(Static(safe_preview(answer), classes="notice", markup=False))
        if successful:
            for proposal in self.agent.edits.pending:
                card = ProposalCard(proposal)
                self.proposal_cards[proposal.id] = card
                self.mount_message(card)
        self.query_one("#current-action", Static).display = False
        self.busy = False
        prompt = self.query_one(Prompt)
        prompt.disabled = False
        prompt.focus()
        self.query_one("#status", Static).update(
            "Aguardando revisão de edições" if self.agent.edits.pending else "Pronto"
        )

    async def action_clear_chat(self):
        if self.busy or isinstance(self.screen, EditReview):
            return
        for proposal in self.agent.edits.pending:
            self.agent.edits.reject(proposal.id)
        self.proposal_cards.clear()
        self.agent.turns.clear()
        self.response_text = self.rendered_text = ""
        self.reply = None
        self.speaker = None
        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.remove_children()
        await conversation.mount(self.welcome())
        self.has_activity = False
        self.query_one("#current-action", Static).display = False
        self.query_one("#activity", RichLog).clear()
        self.query_one("#activity", RichLog).display = False
        self.query_one(Prompt).focus()
        self.query_one("#status", Static).update("Pronto · conversa limpa")

    def on_button_pressed(self, event: Button.Pressed):
        suggestions = {
            "suggest-explore": "Explique a estrutura deste projeto e seus pontos de entrada.",
            "suggest-search": "Localize as validações de entrada e explique onde elas são usadas.",
            "suggest-edit": "Proponha uma melhoria pequena no código com diff para revisão.",
        }
        if event.button.id in suggestions and not self.busy:
            prompt = self.query_one(Prompt)
            prompt.value = suggestions[event.button.id]
            prompt.move_cursor(prompt.document.end)
            prompt.focus()
            return
        identifier = (event.button.id or "").removeprefix("review-")
        if self.busy or identifier not in self.proposal_cards:
            return
        proposal = self.proposal_cards[identifier].proposal
        if proposal.state != "pending":
            return
        self.push_screen(
            EditReview(proposal), lambda decision: self.review_decision(identifier, decision)
        )

    def review_decision(self, identifier: str, decision: str):
        if decision == "reject":
            self.agent.edits.reject(identifier)
            self.resolve_edit(identifier, "Edição rejeitada; arquivo preservado.")
        elif decision == "apply":
            self.busy = True
            self.query_one(Prompt).disabled = True
            self.query_one("#status", Static).update("Verificando arquivo e aplicando edição…")
            self.apply_edit(identifier)

    @work(thread=True, exclusive=True, group="edits")
    def apply_edit(self, identifier: str):
        try:
            self.agent.edits.apply(identifier)
            message = "Edição aplicada. Testes não foram executados."
        except (ValueError, OSError) as exc:
            message = f"Edição bloqueada: {exc} Faça uma nova proposta sobre o arquivo atual."
        except Exception:
            logger.exception("Unexpected edit failure")
            message = "Falha inesperada na aplicação. Confira o arquivo antes de continuar."
        self.deliver(self.resolve_edit, identifier, message)

    def resolve_edit(self, identifier: str, message: str):
        card = self.proposal_cards[identifier]
        card.resolve(f"{card.proposal.path} · {message}")
        # Save the actual approval outcome alongside the answer for follow-up questions.
        if self.agent.turns:
            self.agent.turns[-1][-1]["content"] += (
                f"\n\nResultado da revisão: {card.proposal.path}: {message}"
            )
        self.query_one("#current-action", Static).display = False
        self.busy = False
        prompt = self.query_one(Prompt)
        prompt.disabled = False
        prompt.focus()
        self.query_one("#status", Static).update(
            "Aguardando revisão de edições" if self.agent.edits.pending else "Pronto"
        )

    def action_cancel(self):
        if self.busy:
            self.cancelled.set()
            self.query_one("#status", Static).update(
                "Cancelamento solicitado · aguardando resposta ativa…"
            )

    def on_unmount(self):
        self.cancelled.set()
