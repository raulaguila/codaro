from __future__ import annotations

import logging
import sqlite3
import threading

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, RichLog, Static

from codaro.agent import Agent, AgentEvent, InvestigationCancelled
from codaro.index import safe_preview
from codaro.provider import ModelError

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class CodaroApp(App):
    TITLE = "Codaro · explore seu código"
    CSS = """
    Screen { background: #111827; }
    Header { background: #172554; }
    #main { height: 1fr; }
    #conversation { width: 3fr; border: round #38bdf8; padding: 0 1; }
    #sidebar { width: 1fr; min-width: 28; max-width: 45; }
    .narrow #sidebar { display: none; }
    #repository { height: auto; max-height: 16; border: round #475569; padding: 1; }
    #activity { height: 1fr; border: round #475569; padding: 1; }
    #prompt { margin: 0 1; border: round #38bdf8; }
    #status { height: 1; margin: 0 2; color: #94a3b8; }
    .question { height: auto; margin: 1 0; padding: 1; background: #172554; }
    .assistant { margin: 0; padding: 0 1; background: #111827; }
    .speaker { height: 1; margin: 1 1 0 1; color: #38bdf8; text-style: bold; }
    .tool-card {
        height: auto; margin: 0 1; padding: 0 1;
        border-left: thick #475569; color: #94a3b8;
    }
    .tool-card.error { border-left: thick #f87171; }
    .tool-card.success { border-left: thick #34d399; }
    .notice { height: auto; margin: 1; color: #fbbf24; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Sair", priority=True),
        Binding("ctrl+l", "clear_chat", "Limpar", priority=True),
        Binding("ctrl+x", "cancel", "Cancelar", priority=True),
    ]

    def __init__(self, agent: Agent):
        super().__init__()
        self.agent = agent
        self.busy = False
        self.cancelled = threading.Event()
        self.response_text = ""
        self.rendered_text = ""
        self.reply: Markdown | None = None
        self.context_chars = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with VerticalScroll(id="conversation"):
                yield Markdown(
                    "# Codaro\nExplore seu repositório com respostas apoiadas no código.\n\n"
                    "Pergunte **onde está uma validação** ou **como funciona um fluxo**.\n\n"
                    "O chat envia a pergunta e os trechos consultados ao modelo configurado.",
                    classes="assistant",
                    open_links=False,
                )
            with Vertical(id="sidebar"):
                yield Static("", id="repository", markup=False)
                yield RichLog(id="activity", wrap=True, markup=False, max_lines=1000)
        yield Static("Pronto · busca textual e por símbolos", id="status")
        yield Input(placeholder="Pergunte sobre o repositório…", id="prompt", max_length=8000)
        yield Footer()

    def on_mount(self):
        self.screen.set_class(self.size.width < 90, "narrow")
        self.query_one("#repository", Static).update(
            f"REPOSITÓRIO\n{self.agent.repository.root}\n\nMODELO\n{self.agent.provider.settings.model}"
        )
        self.set_interval(0.08, self.flush_response)
        self.query_one(Input).focus()

    def on_resize(self):
        self.screen.set_class(self.size.width < 90, "narrow")

    def mount_message(self, widget):
        conversation = self.query_one("#conversation", VerticalScroll)
        if len(conversation.children) >= 100:
            for old in list(conversation.children)[:10]:
                old.remove()
        conversation.mount(widget)
        self.call_after_refresh(conversation.scroll_end, animate=False)

    def on_input_submitted(self, event: Input.Submitted):
        question = event.value.strip()
        if not question or self.busy:
            return
        self.busy = True
        self.cancelled.clear()
        self.response_text = self.rendered_text = ""
        self.reply = None
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
            self.context_chars = event.context_chars or 0
            self.query_one("#status", Static).update(
                f"Consultando modelo · contexto: {self.context_chars:,} caracteres"
            )
        elif event.kind == "model_end":
            self.flush_response()
        elif event.kind == "tool_start":
            self.query_one("#status", Static).update(
                f"{event.title} · {safe_preview(event.detail)}"
            )
        elif event.kind == "tool_end":
            duration = f"{event.elapsed_ms:.0f} ms" if event.elapsed_ms is not None else ""
            text = safe_preview(f"{event.title} · {duration}\n{event.detail}")
            color = "red" if event.state == "error" else "green"
            log = Text(event.title + " · " + duration, style=color)
            log.append("\n" + safe_preview(event.detail) + "\n", style="dim")
            self.query_one("#activity", RichLog).write(log)
            self.mount_message(Static(text, classes=f"tool-card {event.state}", markup=False))
            self.query_one("#status", Static).update(f"{event.title} · {duration}")
        else:
            self.query_one("#activity", RichLog).write(
                safe_preview(f"{event.title} · {event.detail}".rstrip(" ·"))
            )
            self.query_one("#status", Static).update(safe_preview(event.title))

    def append_delta(self, delta: str):
        self.response_text += safe_preview(delta)
        self.query_one("#status", Static).update(
            f"Respondendo… · contexto: {self.context_chars:,} caracteres · Ctrl+X para cancelar"
        )
        # Render the first fragment immediately; subsequent fragments are coalesced by the timer.
        if self.reply is None:
            self.flush_response()

    def flush_response(self):
        if not self.response_text or self.response_text == self.rendered_text:
            return
        conversation = self.query_one("#conversation", VerticalScroll)
        follow = conversation.is_vertical_scroll_end
        if self.reply is None:
            self.mount_message(Static("Codaro", classes="speaker", markup=False))
            self.reply = Markdown(self.response_text, classes="assistant", open_links=False)
            self.mount_message(self.reply)
        else:
            self.reply.update(self.response_text)
        self.rendered_text = self.response_text
        if follow:
            self.call_after_refresh(conversation.scroll_end, animate=False)

    def finish(self, answer: str, successful: bool = True):
        if successful:
            self.response_text = safe_preview(answer)
            self.flush_response()
        else:
            self.response_text = self.rendered_text = ""
            if self.reply is not None:
                self.reply.remove()
                self.reply = None
            self.mount_message(Static(safe_preview(answer), classes="notice", markup=False))
        self.busy = False
        prompt = self.query_one(Input)
        prompt.disabled = False
        prompt.focus()
        self.query_one("#status", Static).update("Pronto · faça outra pergunta")

    def action_clear_chat(self):
        if self.busy:
            return
        self.agent.turns.clear()
        self.response_text = self.rendered_text = ""
        self.reply = None
        self.query_one("#conversation", VerticalScroll).remove_children()
        self.query_one("#activity", RichLog).clear()

    def action_cancel(self):
        if self.busy:
            self.cancelled.set()
            self.query_one("#status", Static).update(
                "Cancelamento solicitado · aguardando resposta ativa…"
            )

    def on_unmount(self):
        self.cancelled.set()
