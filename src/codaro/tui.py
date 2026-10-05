from __future__ import annotations

import logging
import sqlite3
import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from codaro.agent import Agent, InvestigationCancelled
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
    #conversation { width: 3fr; border: round #38bdf8; padding: 1 2; }
    #sidebar { width: 1fr; min-width: 25; max-width: 42; }
    .narrow #sidebar { display: none; }
    #repository { height: auto; max-height: 16; border: round #475569; padding: 1; }
    #activity { height: 1fr; border: round #475569; padding: 1; }
    #prompt { margin: 0 1; border: round #38bdf8; }
    #status { height: 1; margin: 0 2; color: #94a3b8; }
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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield RichLog(id="conversation", wrap=True, markup=False, max_lines=4000)
            with Vertical(id="sidebar"):
                yield Static("", id="repository", markup=False)
                yield RichLog(id="activity", wrap=True, markup=False, max_lines=1000)
        yield Static("Busca progressiva · ferramentas de leitura · fontes no código", id="status")
        yield Input(placeholder="Pergunte sobre o repositório…", id="prompt", max_length=8000)
        yield Footer()

    def on_mount(self):
        self.screen.set_class(self.size.width < 90, "narrow")
        self.query_one("#repository", Static).update(
            f"REPOSITÓRIO\n{self.agent.repository.root}\n\nMODELO\n{self.agent.provider.settings.model}"
        )
        self.query_one("#conversation", RichLog).write(
            "Codaro — assistente de investigação\n\n"
            "Exemplos: onde está a validação de acesso? Como funciona a indexação?\n"
            "O chat envia a pergunta e os trechos consultados ao modelo configurado.\n"
            "Busca atual: textual e por símbolos."
        )
        self.query_one(Input).focus()

    def on_resize(self):
        self.screen.set_class(self.size.width < 90, "narrow")

    def on_input_submitted(self, event: Input.Submitted):
        question = event.value.strip()
        if not question or self.busy:
            return
        self.busy = True
        self.cancelled.clear()
        event.input.value = ""
        event.input.disabled = True
        self.query_one("#conversation", RichLog).write(f"\nVocê › {question}\n")
        self.query_one("#status", Static).update("Investigando…")
        self.investigate(question)

    def deliver(self, callback, *args):
        if self.is_running:
            try:
                self.call_from_thread(callback, *args)
            except RuntimeError:
                # The app may close while a network request is returning.
                return

    @work(thread=True, exclusive=True)
    def investigate(self, question: str):
        try:
            answer = self.agent.ask(
                question,
                lambda message: self.deliver(self.activity, message),
                self.cancelled,
            )
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
        self.deliver(self.finish, answer)

    def activity(self, message: str):
        self.query_one("#activity", RichLog).write(safe_preview(message))
        self.query_one("#status", Static).update(safe_preview(message))

    def finish(self, answer: str):
        self.query_one("#conversation", RichLog).write(f"Codaro › {safe_preview(answer)}\n")
        self.busy = False
        prompt = self.query_one(Input)
        prompt.disabled = False
        prompt.focus()
        self.query_one("#status", Static).update("Pronto · faça outra pergunta")

    def action_clear_chat(self):
        if self.busy:
            return
        self.agent.turns.clear()
        self.query_one("#conversation", RichLog).clear()
        self.query_one("#activity", RichLog).clear()

    def action_cancel(self):
        if self.busy:
            self.cancelled.set()
            self.query_one("#status", Static).update(
                "Cancelamento solicitado · aguardando requisição ativa…"
            )

    def on_unmount(self):
        self.cancelled.set()
