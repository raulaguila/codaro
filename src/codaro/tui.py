from __future__ import annotations

import asyncio
import json
import logging
import shlex
import sqlite3
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from rich.syntax import Syntax
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Footer, Markdown, Static, TextArea

from codaro.agent import Agent, AgentEvent, InvestigationCancelled
from codaro.edits import EditProposal
from codaro.index import CodeIndex, safe_preview
from codaro.interaction import COMMANDS, InputHistory, completions
from codaro.policies import ApprovalPolicy, Mode
from codaro.provider import ModelError, OpenAICompatible
from codaro.sessions import SessionStore
from codaro.storage import private_lock

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Prompt(TextArea):
    """A wrapping composer with explicit submission and portable newline shortcuts."""

    suggesting = False

    BINDINGS = [
        Binding("enter", "submit", "Enviar", show=False, priority=True),
        Binding("alt+enter,shift+enter", "newline", "Nova linha", show=False, priority=True),
        Binding("tab", "complete", "Completar", show=False, priority=True),
        Binding("up", "previous", "Anterior", show=False, priority=True),
        Binding("down", "next", "Próxima", show=False, priority=True),
        Binding("alt+up", "history_previous", "Histórico", show=False, priority=True),
        Binding("alt+down", "history_next", "Histórico", show=False, priority=True),
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

    class Complete(Message):
        pass

    class Navigate(Message):
        def __init__(self, direction: int, history_only=False):
            super().__init__()
            self.direction, self.history_only = direction, history_only

    def action_complete(self):
        self.post_message(self.Complete())

    def action_previous(self):
        row = self.wrapped_document.location_to_offset(self.cursor_location).y
        if (self.suggesting or row == 0) and self.selection.is_empty:
            self.post_message(self.Navigate(-1))
        else:
            self.action_cursor_up()

    def action_next(self):
        row = self.wrapped_document.location_to_offset(self.cursor_location).y
        if (self.suggesting or row == self.wrapped_document.height - 1) and self.selection.is_empty:
            self.post_message(self.Navigate(1))
        else:
            self.action_cursor_down()

    def action_history_previous(self):
        self.post_message(self.Navigate(-1, True))

    def action_history_next(self):
        self.post_message(self.Navigate(1, True))


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
                safe_preview(
                    f"Revisar · {getattr(self.proposal, 'operation', 'Conjunto')} · "
                    f"{self.proposal.path}\n{self.proposal.reason}"
                ),
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


class ChangesReview(EditReview):
    def __init__(self, proposals):
        combined = SimpleNamespace(
            path=f"{len(proposals)} arquivo(s)",
            reason="Aprovar aplica este conjunto; o agente continuará para validar.",
            diff="\n".join(
                f"{item.operation} · {item.path} · {item.reason}\n{item.diff}" for item in proposals
            ),
        )
        super().__init__(combined)


class ScopeReview(ModalScreen[dict | None]):
    DEFAULT_CSS = """
    ScopeReview { align: center middle; }
    #scope-review { width: 90%; height: 80%; border: round #fbbf24; padding: 1; }
    #scope-review TextArea { height: 1fr; }
    #scope-review Horizontal { height: auto; }
    """
    BINDINGS = [Binding("escape", "reject", "Cancelar")]

    def __init__(self, task):
        super().__init__()
        self.scope_task = task

    def compose(self):
        with Vertical(id="scope-review"):
            yield Static("Autorizar escopo desta tarefa/sessão", markup=False)
            yield Static(
                safe_preview(self.scope_task["id"] + " · " + self.scope_task["objective"]),
                markup=False,
            )
            yield Static(
                "Permite criar/alterar/remover/renomear nos caminhos e executar os "
                "comandos exatos. Fora do escopo exige nova aprovação.",
                markup=False,
            )
            yield TextArea('{"paths": ["src", "tests"], "commands": []}', id="scope-json")
            yield Static("", id="scope-error", markup=False)
            with Horizontal():
                yield Button("Autorizar escopo", id="grant-scope", variant="warning")
                yield Button("Cancelar", id="cancel-scope")

    def on_mount(self):
        self.query_one("#cancel-scope", Button).focus()

    def action_reject(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        if event.button.id != "grant-scope":
            self.dismiss(None)
            return
        try:
            text = self.query_one("#scope-json", TextArea).text
            if len(text) > 8000:
                raise ValueError("Escopo grande demais.")
            value = json.loads(text)
            if not isinstance(value, dict) or set(value) != {"paths", "commands"}:
                raise ValueError("Use paths e commands.")
            ApprovalPolicy().grant("preview", value["paths"], value["commands"])
        except (ValueError, TypeError) as exc:
            self.query_one("#scope-error", Static).update(safe_preview(str(exc)))
            return
        self.dismiss(value)


class CommandReview(ModalScreen[bool]):
    DEFAULT_CSS = """
    CommandReview { align: center middle; background: #000000 65%; }
    #command-review { width: 90%; max-width: 100; height: auto; padding: 1 2;
                      border: round #fbbf24; background: #111827; }
    #command-review Static { height: auto; margin-bottom: 1; }
    #command-review Horizontal { height: 3; }
    #command-review Button { margin-right: 1; }
    """
    BINDINGS = [Binding("escape", "reject", "Rejeitar", priority=True)]

    def __init__(self, root: Path, argv: list[str], timeout: int):
        super().__init__()
        self.root, self.argv, self.timeout = root, argv, timeout

    def compose(self) -> ComposeResult:
        with Vertical(id="command-review"):
            yield Static("Autorizar comando", markup=False)
            yield Static(
                safe_preview(
                    f"Diretório: {self.root}\nTimeout: {self.timeout}s\n\n" + shlex.join(self.argv)
                ),
                markup=False,
            )
            yield Static(
                "O comando pode alterar arquivos. A aprovação vale só para esta execução.",
                markup=False,
            )
            with Horizontal():
                yield Button("Executar", id="approve-command", variant="warning")
                yield Button("Rejeitar", id="reject-command")

    def on_mount(self):
        self.query_one("#reject-command", Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "approve-command")

    def action_reject(self):
        self.dismiss(False)


class GenerationPreview(Collapsible):
    """Opt-in, bounded preview; never presented as an accepted assistant answer."""

    def __init__(self):
        self.text = ""
        self.state = "generating"
        self.content = Static("", markup=False, classes="generation-text")
        super().__init__(
            self.content,
            title="Prévia não validada · gerando…",
            classes="generation-preview",
            collapsed=True,
        )

    def update_text(self, text: str):
        self.text = text[:4000]
        suffix = (
            "\n… prévia limitada; fluxo completo em .codaro/prompt.json" if len(text) > 4000 else ""
        )
        self.content.update(self.text + suffix)

    def finish(self, state: str):
        self.state = state
        self.title = {
            "tools": "Etapa intermediária · chamada de ferramentas",
            "retry": "Prévia rejeitada · nova tentativa",
            "answer": "Prévia não validada · aguardando conclusão",
            "accepted": "Prévia da resposta · geração concluída",
            "cancelled": "Prévia interrompida · sem resposta concluída",
        }.get(state, "Prévia interrompida · sem resposta concluída")
        self.collapsed = True


class ActivityGroup(Collapsible):
    """One expandable activity summary per turn; errors remain visible."""

    def __init__(self):
        self.events: list[AgentEvent] = []
        self.generations: list[GenerationPreview] = []
        self.previews = Vertical(classes="generation-previews")
        self.details = Static("", markup=False)
        super().__init__(
            self.details, self.previews, title="Investigação", classes="tool-card", collapsed=True
        )

    def add_preview(self, preview: GenerationPreview):
        self.generations.append(preview)
        if self.previews.is_attached:
            self.previews.mount(preview)
        else:
            self.call_after_refresh(self.previews.mount, preview)

    def finish(self, successful: bool):
        if self.generations and self.generations[-1].state == "answer":
            self.generations[-1].finish("accepted" if successful else "cancelled")
        if not self.events:
            self.title = "Geração concluída" if successful else "Geração interrompida"

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
            self.collapsed = False
        self.title = (
            " · ".join(parts) + f" · {sum(item.elapsed_ms or 0 for item in self.events):.0f} ms"
        )
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
        self.details.update(text)


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
    #brand { height: 1; margin: 1 2 0 2; color: #f1f5f9; text-style: bold; }
    #session { height: 1; margin: 0 2; color: #94a3b8; }
    #session.insecure { color: #fbbf24; }
    #conversation { width: 100%; height: 1fr; padding: 0 2; }
    #welcome { height: auto; max-width: 78; margin: 1; }
    #welcome-title { height: 1; color: #f1f5f9; text-style: bold; }
    #welcome-description { height: auto; margin-bottom: 1; color: #94a3b8; }
    #suggestions { height: 3; }
    #welcome Button { width: 1fr; min-width: 12; background: #172033; border: none; }
    #welcome Button:hover, #welcome Button:focus { border: round #38bdf8; }
    #prompt { height: 3; max-height: 8; margin: 0 1; border: none;
              border-top: solid #475569; padding: 0 1; }
    #prompt:focus { border-top: solid #38bdf8; }
    #completion { display: none; height: auto; max-height: 7; margin: 0 2; color: #94a3b8; }
    #prompt-hint { height: 1; margin: 0 2; color: #94a3b8; }
    #status { height: 1; margin: 0 2; color: #cbd5e1; }
    .question { height: auto; margin: 1 0; color: #f1f5f9; }
    .assistant { margin: 0; padding: 0 1; background: #0f172a; }
    .speaker { height: 1; margin: 1 1 0 1; color: #e2e8f0; text-style: bold; }
    .tool-card { height: auto; margin: 0 1; border: none; border-left: thick #475569; padding: 0; }
    .tool-card.error { border-left: thick #f87171; }
    .tool-card.success { border-left: thick #34d399; }
    .tool-card CollapsibleTitle { color: #94a3b8; }
    .tool-card > Contents { padding: 0 1; }
    .generation-previews { height: auto; }
    .generation-preview { height: auto; border: none; padding: 0; }
    .generation-preview > Contents { padding: 0 1; }
    .generation-text { height: auto; color: #94a3b8; }
    .notice { height: auto; margin: 1; color: #fbbf24; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Sair", priority=True, key_display="Ctrl+Q"),
        Binding("ctrl+l", "clear_chat", "Limpar", priority=True, key_display="Ctrl+L"),
        Binding("ctrl+x", "cancel", "Cancelar", priority=True, key_display="Ctrl+X"),
        Binding("escape", "cancel", "Cancelar", show=False),
        Binding("ctrl+p", "command_palette", "Comandos", priority=True, key_display="Ctrl+P"),
    ]

    def __init__(self, agent: Agent, *, resume: bool = False):
        super().__init__()
        self.is_macos = sys.platform == "darwin"
        if self.is_macos:
            # The extended terminal keyboard protocol names Command "super".
            # Keep Ctrl bindings: some terminals reserve Command for their menus.
            for binding in self.BINDINGS:
                if binding.key.startswith("ctrl+"):
                    self._bindings.bind(
                        binding.key.replace("ctrl+", "super+", 1),
                        binding.action,
                        binding.description,
                        show=False,
                        priority=binding.priority,
                    )
        self.agent = agent
        self.busy = False
        self.cancelled = threading.Event()
        self.response_text = ""
        self.rendered_text = ""
        self.draft_text = ""
        self.draft_rendered = ""
        self.generation_preview: GenerationPreview | None = None
        self.reply: Markdown | None = None
        self.speaker: Static | None = None
        self.context_chars = 0
        self.context_tokens = 0
        self.reported_tokens: int | None = None
        self.context_limit = agent.adaptive_input_limit
        self.proposal_cards: dict[str, ProposalCard] = {}
        self.prompt_too_long = False
        self.active_question = ""
        self.history = InputHistory()
        self.reference_paths: list[str] = []
        self.completion_index = 0
        self.activity_group: ActivityGroup | None = None
        self.resume_requested = resume
        self.session = SessionStore(
            agent.repository.root, getattr(getattr(agent.provider, "settings", None), "api_key", "")
        )
        self.session_turns: list[list[dict]] = list(agent.turns)
        self.approval_dialog: CommandReview | None = None
        if not agent.legacy or agent.allow_edits:
            agent.approve_command = self.approve_command
        if not agent.legacy:
            agent.approve_edit = self.approve_changes
        self.plan_card: Collapsible | None = None

    def shortcut_display(self, key: str) -> str:
        key = key.upper()
        return f"⌘{key} / Ctrl+{key}" if self.is_macos else f"Ctrl+{key}"

    def option_display(self, key: str) -> str:
        return f"⌥{key}" if self.is_macos else f"Alt+{key}"

    def get_key_display(self, binding: Binding) -> str:
        if self.is_macos and binding in self.BINDINGS and binding.key.startswith("ctrl+"):
            return self.shortcut_display(binding.key.removeprefix("ctrl+"))
        return super().get_key_display(binding)

    def welcome(self) -> Vertical:
        return Vertical(
            Static("Vamos trabalhar no seu projeto", id="welcome-title", markup=False),
            Static(
                "Escolha um ponto de partida ou escreva sua pergunta.\n"
                "As respostas usam os arquivos locais; edições exigem sua aprovação.",
                id="welcome-description",
                markup=False,
            ),
            Horizontal(
                Button("Explorar", id="suggest-explore"),
                Button("Buscar código", id="suggest-search"),
                Button("Propor mudança", id="suggest-edit", disabled=not self.agent.allow_edits),
                id="suggestions",
            ),
            id="welcome",
        )

    def compose(self) -> ComposeResult:
        yield Static("◈ Codaro", id="brand", markup=False)
        yield Static("", id="session", markup=False)
        with VerticalScroll(id="conversation"):
            yield self.welcome()
        yield Static("Pronto", id="status")
        yield Static("", id="completion", markup=False)
        yield Prompt(
            placeholder="Pergunte ou peça uma mudança…",
            id="prompt",
            show_line_numbers=False,
            highlight_cursor_line=False,
        )
        yield Static(
            f"Enter envia · {self.option_display('Enter')} nova linha · "
            "/ comandos · @ arquivo · ↑ histórico",
            id="prompt-hint",
            markup=False,
        )
        yield Footer(show_command_palette=False)

    async def on_mount(self):
        self.update_session_header()
        self.set_interval(0.08, self.flush_preview)
        self.query_one(Prompt).focus()
        self.refresh_reference_paths()
        if self.resume_requested:
            await self.restore_session()
        try:
            self.show_plan()
        except (ValueError, OSError) as exc:
            self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))

    def update_session_header(self):
        settings = self.agent.provider.settings
        mode = (
            self.agent.mode.label
            + " · "
            + ("Aprovação por tarefa" if self.agent.policy.kind == "task" else "Aprovação por ação")
        )
        tls = "TLS sem verificação" if settings.tls_insecure else "TLS verificação ativa"
        if settings.base_url.startswith("http://"):
            tls = "HTTP"
        root = short_path(self.agent.repository.root)
        summary = f"{root} · {settings.model} · {mode}"
        summary = f"{tls} · {summary}" if settings.tls_insecure else f"{summary} · {tls}"
        session = self.query_one("#session", Static)
        session.update(safe_preview(summary))
        session.set_class(settings.tls_insecure, "insecure")
        session.tooltip = safe_preview(str(self.agent.repository.root))

    @work(thread=True, exclusive=True, group="files")
    def refresh_reference_paths(self):
        try:
            paths = sorted(
                path.relative_to(self.agent.repository.root).as_posix()
                for path in self.agent.repository.files()
            )
            self.deliver(self.set_reference_paths, paths)
        except (ValueError, OSError):
            return

    def set_reference_paths(self, paths):
        self.reference_paths = paths
        self.update_completions()

    def completion_options(self):
        prompt = self.query_one(Prompt)
        cursor = prompt.document.get_index_from_location(prompt.cursor_location)
        return completions(prompt.text, cursor, self.reference_paths)

    def update_completions(self):
        options = self.completion_options()
        menu = self.query_one("#completion", Static)
        menu.display = bool(options) and not self.busy
        self.query_one(Prompt).suggesting = menu.display
        if options:
            self.completion_index %= len(options)
            first = max(0, min(self.completion_index - 2, len(options) - 5))
            menu.update(
                safe_preview(
                    "\n".join(
                        ("› " if i == self.completion_index else "  ") + item.label
                        for i, item in enumerate(options)
                        if first <= i < first + 5
                    )
                )
                + "\nTab completa · ↑↓ escolhem"
            )

    def on_prompt_complete(self, event: Prompt.Complete):
        options = self.completion_options()
        if not options:
            self.action_focus_next()
            return
        option = options[self.completion_index % len(options)]
        prompt = self.query_one(Prompt)
        start = prompt.document.get_location_from_index(option.start)
        end = prompt.document.get_location_from_index(option.end)
        prompt.replace(option.value, start, end, maintain_selection_offset=False)
        prompt.move_cursor(
            prompt.document.get_location_from_index(option.start + len(option.value))
        )

    def on_prompt_navigate(self, event: Prompt.Navigate):
        if self.completion_options() and not event.history_only:
            self.completion_index += event.direction
            self.update_completions()
            return
        prompt = self.query_one(Prompt)
        value = (
            self.history.previous(prompt.value)
            if event.direction < 0
            else self.history.next(prompt.value)
        )
        if value != prompt.value:
            prompt.value = value
            prompt.move_cursor(prompt.document.end)

    async def local_command(self, question: str):
        name, _, argument = question.partition(" ")
        argument = argument.strip()
        if name not in COMMANDS:
            self.mount_message(Static("Comando desconhecido. Use /help.", classes="notice"))
            return
        if argument and name not in {
            "/model",
            "/history",
            "/memory",
            "/undo",
            "/mode",
            "/task",
            "/permissions",
        }:
            self.mount_message(Static("Este comando não recebe argumentos.", classes="notice"))
            return
        if name in {"/resume", "/compact", "/model", "/undo"} and self.agent.edits.pending:
            self.mount_message(Static("Revise as edições pendentes primeiro.", classes="notice"))
            return
        self.query_one(Prompt).value = ""
        if name == "/clear":
            await self.action_clear_chat()
            return
        if name == "/resume":
            await self.restore_session()
            return
        self.hide_welcome()
        if name in {"/mode", "/ask", "/plan", "/execute"}:
            try:
                selected = argument if name == "/mode" else name[1:]
                if selected:
                    self.agent.set_mode(selected)
                    self.update_session_header()
                    for button in self.query("#suggest-edit"):
                        button.disabled = not self.agent.allow_edits
                text = f"Modo: {self.agent.mode.label}. Permissões: {self.agent.policy.kind}."
                if name == "/execute" and self.agent.tasks.current():
                    self.query_one(
                        Prompt
                    ).value = "Execute o plano da tarefa ativa e valide o resultado."
            except (ValueError, OSError) as exc:
                text = str(exc)
        elif name == "/task":
            try:
                action, _, value = argument.partition(" ")
                if action == "new":
                    if not value.strip() or len(value) > 8000:
                        raise ValueError("Use /task new OBJETIVO.")
                    task = self.agent.tasks.start(value, new=True)
                    self.agent.policy.reset()
                elif action == "resume":
                    task = self.agent.tasks.select(value)
                    self.agent.policy.reset()
                elif action == "list":
                    task = [
                        {key: item[key] for key in ("id", "objective", "state")}
                        for item in self.agent.tasks.load()["tasks"]
                    ]
                elif not action:
                    task = self.agent.tasks.current()
                else:
                    raise ValueError("Use /task, /task list, /task new ou /task resume.")
                text = json.dumps(task, ensure_ascii=False, indent=2)
                self.update_session_header()
            except (ValueError, OSError) as exc:
                text = str(exc)
        elif name == "/permissions":
            if argument == "action":
                self.agent.policy.reset()
                self.update_session_header()
                text = "Aprovação por ação ativada."
            elif argument == "task":
                if self.agent.mode != Mode.EXECUTE or not self.agent.tasks.current():
                    text = "Use Executar e defina /task new OBJETIVO antes de autorizar o escopo."
                else:
                    task = self.agent.tasks.current()
                    self.push_screen(
                        ScopeReview(task), lambda value: self.scope_decision(value, task["id"])
                    )
                    return
            else:
                text = "Use /permissions action ou /permissions task."
        elif name == "/help":
            text = "\n".join(f"{key} · {description}" for key, description in COMMANDS.items())
            text += (
                "\n\nTab completa · ↑↓ escolhem sugestões/histórico · "
                f"{self.option_display('↑↓')} histórico\n"
            )
            text += 'Referências: @src/main.py ou @"pasta com espaços/main.py"\n'
            text += (
                f"{self.shortcut_display('x')} cancela · {self.shortcut_display('l')} limpa · "
                f"{self.shortcut_display('p')} comandos · {self.shortcut_display('q')} sai"
            )
            if self.is_macos:
                text += (
                    "\n\n⌘ = Command · ⌥ = Option. Command funciona quando o terminal envia "
                    "a tecla ao Codaro; se abrir um menu ou encerrar o terminal, use Ctrl. "
                    "Para Option, configure o terminal para enviar Alt/Esc. "
                    "Shift+Enter também insere uma nova linha quando reconhecido pelo terminal."
                )
        elif name == "/pwd":
            text = f"Diretório da sessão\n{self.agent.repository.root}"
        elif name in {"/memory", "/history", "/map", "/changes", "/undo"}:
            try:
                if name == "/history":
                    value = self.agent.memory.search(argument)
                elif name == "/memory":
                    if argument == "clear":
                        self.agent.memory.clear_task()
                    elif argument:
                        kind, _, content = argument.partition(" ")
                        self.agent.memory.remember(kind, content)
                    value = self.agent.memory.task()
                elif name == "/map":

                    def build_map():
                        with CodeIndex(self.agent.repository) as index:
                            return self.agent.project_map.build(index)

                    value = await asyncio.to_thread(build_map)
                elif name == "/changes":
                    value = self.agent.edits.checkpoints.list()
                else:
                    if not self.agent.allow_edits:
                        raise ValueError("Desfazer desabilitado no modo somente leitura.")
                    proposal = self.agent.edits.propose_undo(argument or None)
                    card = ProposalCard(proposal)
                    self.proposal_cards[proposal.id] = card
                    self.mount_message(card)
                    self.push_screen(
                        EditReview(proposal),
                        lambda decision: self.review_decision(proposal.id, decision),
                    )
                    return
                text = json.dumps(value, ensure_ascii=False, indent=2)
            except (ValueError, OSError, sqlite3.Error) as exc:
                text = str(exc)
        elif name == "/model":
            if argument:
                try:
                    settings = replace(self.agent.provider.settings, model=argument)
                    self.agent.provider = OpenAICompatible(
                        settings, transport=getattr(self.agent.provider, "transport", None)
                    )
                    self.update_session_header()
                except ValueError as exc:
                    self.mount_message(
                        Static(safe_preview(str(exc)), classes="notice", markup=False)
                    )
                    return
            text = f"Modelo: {self.agent.provider.settings.model}\nTroque com /model nome"
        elif name == "/compact":
            before = len(str(self.agent.turns))
            self.agent.turns = [[turn[0], turn[-1]] for turn in self.agent.turns[-4:]]
            text = f"Histórico compactado: {before} → {len(str(self.agent.turns))} caracteres. "
            text += (
                "Contexto limitado aos quatro turnos mais recentes; "
                "o código será consultado novamente."
            )
            self.save_session()
        else:
            text = (
                f"Projeto: {self.agent.repository.root}\n"
                f"Modelo: {self.agent.provider.settings.model}\n"
                f"Modo: {self.agent.mode.label} · aprovação {self.agent.policy.kind}\n"
                f"Turnos no contexto: {len(self.agent.turns)}\n"
                f"Turnos da conversa: {len(self.session_turns)}\n"
                f"Último contexto enviado: {self.context_chars} "
                f"caracteres / limite {self.agent.context_budget}\n"
                f"Entrada estimada: {self.context_tokens} / {self.context_limit} tokens\n"
                f"Janela configurada: {self.agent.context_window} tokens · "
                f"reserva de saída: {self.agent.max_output_tokens} · margem: 512\n"
                f"Contagem: {self.agent.counter.method}\n"
                f"Tokens informados pelo servidor: {self.reported_tokens}\n"
                f"Calibração: {self.agent.counter.scale:.2f} · "
                f"amostras: {len(self.agent.counter.samples)}\n"
                f"Sessão: {self.session.path}\nDebug: .codaro/prompt.json"
            )
        self.mount_message(Static(safe_preview(text), classes="question", markup=False))

    def save_session(self):
        try:
            self.session.save(self.session_turns, self.agent.provider.settings.model)
        except (ValueError, OSError, ModelError) as exc:
            self.mount_message(
                Static(
                    safe_preview(f"Não foi possível salvar a sessão: {exc}"),
                    classes="notice",
                    markup=False,
                )
            )

    async def restore_session(self):
        try:
            turns = self.session.load()
        except (ValueError, OSError, ModelError) as exc:
            self.mount_message(
                Static(
                    safe_preview(f"Não foi possível retomar: {exc}"), classes="notice", markup=False
                )
            )
            return
        if not turns:
            self.mount_message(Static("Nenhuma conversa salva neste projeto.", classes="notice"))
            return
        self.session_turns = turns
        self.agent.turns = list(turns)
        self.history = InputHistory(turn[0]["content"] for turn in turns)
        self.reply = self.speaker = None
        self.response_text = self.rendered_text = ""
        self.activity_group = None
        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.remove_children()
        for turn in turns[-30:]:
            self.mount_message(
                Static("› " + safe_preview(turn[0]["content"]), classes="question", markup=False)
            )
            self.mount_message(Static("Codaro", classes="speaker", markup=False))
            self.mount_message(
                Markdown(safe_preview(turn[-1]["content"]), classes="assistant", open_links=False)
            )
        self.mount_message(
            Static(
                "Sessão retomada. Revise os arquivos atuais; propostas anteriores "
                "não são reaplicadas.",
                classes="notice",
                markup=False,
            )
        )
        self.query_one("#status", Static).update("Pronto · sessão retomada")

    def approve_command(self, argv, timeout, cancelled):
        decision, ready = [], threading.Event()

        def show():
            dialog = CommandReview(self.agent.repository.root, argv, timeout)
            self.approval_dialog = dialog
            self.query_one("#status", Static).update("Aguardando aprovação do comando")

            def resolved(value):
                decision.append(value)
                self.approval_dialog = None
                ready.set()

            self.push_screen(dialog, resolved)

        self.deliver(show)
        while not ready.wait(0.05):
            if not self.is_running or cancelled is not None and cancelled.is_set():
                self.deliver(self.dismiss_command)
                return False
        return bool(decision and decision[0])

    def approve_changes(self, proposals, cancelled):
        decision, ready = [], threading.Event()

        def show():
            dialog = ChangesReview(proposals)
            self.approval_dialog = dialog
            self.query_one("#status", Static).update("Aguardando revisão das alterações")

            def resolved(value):
                decision.append(value == "apply")
                self.approval_dialog = None
                ready.set()

            self.push_screen(dialog, resolved)

        self.deliver(show)
        while not ready.wait(0.05):
            if not self.is_running or cancelled is not None and cancelled.is_set():
                self.deliver(self.dismiss_command)
                return False
        return bool(decision and decision[0])

    def scope_decision(self, value, task_id):
        if value is None:
            return
        try:
            task = self.agent.tasks.current()
            if task is None or task["id"] != task_id:
                raise ValueError("A tarefa mudou; revise novamente o escopo.")
            self.agent.policy.grant(task["id"], value["paths"], value["commands"])
            self.agent.tasks.event("scope_approval", value)
            self.update_session_header()
            self.mount_message(
                Static("Escopo autorizado para esta tarefa/sessão.", classes="notice")
            )
        except (ValueError, OSError) as exc:
            self.agent.policy.reset()
            self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))

    def dismiss_command(self):
        if self.approval_dialog is not None and self.screen is self.approval_dialog:
            self.approval_dialog.dismiss(False)

    def hide_welcome(self):
        for welcome in self.query("#welcome"):
            welcome.display = False

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged):
        if event.text_area.id == "prompt":
            self.update_completions()

    def on_text_area_changed(self, event: TextArea.Changed):
        if event.text_area.id != "prompt":
            return
        prompt = event.text_area
        self.completion_index = 0
        self.update_completions()
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

    async def on_prompt_submitted(self, event: Prompt.Submitted):
        question = event.value.strip()
        if not question or self.busy:
            return
        if len(event.value) > 8000:
            self.query_one("#status", Static).update(
                "Mensagem excede 8.000 caracteres · reduza antes de enviar"
            )
            return
        if question.startswith("/"):
            await self.local_command(question)
            return
        if self.agent.edits.pending:
            self.mount_message(
                Static("Revise as edições pendentes antes de outra pergunta.", classes="notice")
            )
            return
        self.hide_welcome()
        for button in self.query("#execute-plan"):
            button.disabled = True
        for card in self.proposal_cards.values():
            for button in card.query(Button):
                button.disabled = True
        self.proposal_cards.clear()
        self.history.push(question)
        self.active_question = question
        self.activity_group = None
        self.plan_card = None
        self.busy = True
        self.cancelled.clear()
        self.response_text = self.rendered_text = ""
        self.reply = None
        self.speaker = None
        event.input.value = ""
        event.input.disabled = True
        self.mount_message(Static(f"› {safe_preview(question)}", classes="question", markup=False))
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
        if event.kind == "plan":
            self.show_plan()
        elif event.kind == "model_start":
            self.update_session_header()
            self.finish_preview("retry")
            self.context_chars = event.context_chars or 0
            self.context_tokens = event.context_tokens or 0
            self.reported_tokens = None
            self.context_limit = event.context_limit or self.agent.adaptive_input_limit
            self.query_one("#status", Static).update(
                f"Consultando modelo… · contexto ≈ {self.context_tokens:,} / "
                f"{self.context_limit:,} tokens"
            )

        elif event.reported_tokens is not None:
            self.reported_tokens = event.reported_tokens
            self.query_one("#status", Static).update(safe_preview(event.detail))
        elif event.kind == "model_end":
            self.finish_preview(event.state)
        elif event.kind == "tool_start":
            self.query_one("#status", Static).update(
                safe_preview(f"{event.title}… · {event.detail}".rstrip(" ·"))
            )
        elif event.kind == "tool_end":
            duration = f"{event.elapsed_ms:.0f} ms" if event.elapsed_ms is not None else ""
            if self.activity_group is None:
                self.activity_group = ActivityGroup()
                self.mount_message(self.activity_group)
            self.activity_group.add(event)
            self.query_one("#status", Static).update(f"{event.title} · {duration}")
        else:
            self.query_one("#status", Static).update(
                safe_preview(f"{event.title} · {event.detail}".rstrip(" ·"))
            )

    def show_plan(self):
        task = self.agent.tasks.current()
        if not task or not task["plan"]:
            return
        symbols = {"todo": "○", "doing": "◉", "done": "✓"}
        text = "\n".join(f"{symbols[step['state']]} {step['title']}" for step in task["plan"])
        text += "\n\nCritérios de aceite:\n" + "\n".join(task["criteria"])
        if self.plan_card is None:
            self.plan_card = Collapsible(
                Static(safe_preview(text), markup=False),
                Button("Executar plano", id="execute-plan"),
                title="Plano da tarefa",
                collapsed=False,
            )
            self.mount_message(self.plan_card)
        else:
            self.plan_card.query_one(Static).update(safe_preview(text))

    def append_delta(self, delta: str):
        # A content delta may precede native tool_calls in the same message.
        # Only finish() can commit an answer to the conversation.
        self.draft_text = (self.draft_text + safe_preview(delta))[:4001]
        self.query_one("#status", Static).update(
            f"Gerando… · prévia em Investigação · {self.shortcut_display('x')} para cancelar"
        )
        # Render the first fragment immediately; subsequent fragments are coalesced by the timer.
        if self.generation_preview is None:
            self.flush_preview()

    def flush_preview(self):
        if not self.draft_text or self.draft_text == self.draft_rendered:
            return
        if self.generation_preview is None:
            self.generation_preview = GenerationPreview()
            if self.activity_group is None:
                self.activity_group = ActivityGroup()
                self.mount_message(self.activity_group)
            self.activity_group.add_preview(self.generation_preview)
        self.generation_preview.update_text(self.draft_text)
        self.draft_rendered = self.draft_text

    def finish_preview(self, state: str):
        self.flush_preview()
        if self.generation_preview is not None:
            self.generation_preview.finish(state)
        self.draft_text = self.draft_rendered = ""
        self.generation_preview = None

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
        self.finish_preview("answer" if successful else "cancelled")
        if self.activity_group is not None:
            self.activity_group.finish(successful)
        if successful:
            self.show_plan()
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
        self.busy = False
        prompt = self.query_one(Prompt)
        prompt.disabled = False
        prompt.focus()
        self.query_one("#status", Static).update(
            "Aguardando revisão de edições" if self.agent.edits.pending else "Pronto"
        )

        if not self.agent.legacy:
            try:
                task = self.agent.tasks.current()
                if task:
                    labels = {
                        "planned": "Plano pronto · /execute para continuar",
                        "completed": "Pronto · tarefa concluída",
                        "blocked": "Tarefa bloqueada · /task para detalhes",
                        "cancelled": "Tarefa cancelada · alterações anteriores foram mantidas",
                    }
                    self.query_one("#status", Static).update(labels.get(task["state"], "Pronto"))
            except (ValueError, OSError):
                pass

        if successful:
            self.session_turns.append(
                [
                    {"role": "user", "content": self.active_question},
                    {"role": "assistant", "content": answer},
                ]
            )
            self.session_turns = self.session_turns[-50:]
            self.save_session()

    async def action_clear_chat(self):
        if self.busy or isinstance(self.screen, (EditReview, CommandReview, ScopeReview)):
            return
        for proposal in self.agent.edits.pending:
            self.agent.edits.reject(proposal.id)
        self.proposal_cards.clear()
        self.activity_group = None
        self.plan_card = None
        self.agent.turns.clear()
        self.session_turns.clear()
        self.response_text = self.rendered_text = ""
        self.reply = None
        self.speaker = None
        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.remove_children()
        await conversation.mount(self.welcome())
        self.query_one(Prompt).focus()
        self.query_one("#status", Static).update("Pronto · conversa limpa")

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "execute-plan":
            if self.busy:
                return
            self.agent.set_mode(Mode.EXECUTE)
            self.update_session_header()
            self.query_one(Prompt).value = "Execute o plano da tarefa ativa e valide o resultado."
            self.query_one(Prompt).focus()
            return
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
        if (event.button.id or "").startswith("validate-") and not self.busy:
            if self.agent.edits.pending:
                self.mount_message(Static("Revise as outras edições primeiro.", classes="notice"))
                return
            identifier = event.button.id.removeprefix("validate-")
            if identifier not in self.proposal_cards:
                return
            proposal = self.proposal_cards[identifier].proposal
            prompt = self.query_one(Prompt)
            prompt.value = (
                f"Valide a alteração aplicada em {proposal.path}. Leia o código atual, "
                "descubra os testes relevantes e solicite sua execução com run_command. "
                "Informe o resultado real e o que não foi possível verificar."
            )
            self.post_message(Prompt.Submitted(prompt))
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
            with private_lock(self.agent.repository.root / ".codaro/agent.lock"):
                self.agent.edits.apply(identifier)
            proposal = self.agent.edits.proposals[identifier]
            message = (
                "Edição aplicada. Testes não foram executados. "
                f"Checkpoint: {proposal.checkpoint_id}. " + proposal.checkpoint_warning
            )
        except (ValueError, OSError) as exc:
            message = f"Edição bloqueada: {exc} Faça uma nova proposta sobre o arquivo atual."
        except Exception:
            logger.exception("Unexpected edit failure")
            message = "Falha inesperada na aplicação. Confira o arquivo antes de continuar."
        self.deliver(self.resolve_edit, identifier, message)

    def resolve_edit(self, identifier: str, message: str):
        card = self.proposal_cards[identifier]
        card.resolve(f"{card.proposal.path} · {message}")
        if card.proposal.state == "applied":
            card.mount(Button("Validar alteração", id=f"validate-{identifier}"))
            self.refresh_reference_paths()
        # Save the actual approval outcome alongside the answer for follow-up questions.
        review = f"\n\nResultado da revisão: {card.proposal.path}: {message}"
        try:
            self.agent.memory.review(review)
            self.agent.memory.record_change(card.proposal, message)
        except (ValueError, OSError) as exc:
            self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
        if self.agent.turns:
            self.agent.turns[-1][-1]["content"] += review
        if self.session_turns:
            self.session_turns[-1][-1]["content"] += review
            self.save_session()
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
