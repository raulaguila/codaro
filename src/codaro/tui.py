from __future__ import annotations

import asyncio
import copy
import json
import logging
import shlex
import sqlite3
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax
from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.command import CommandPalette
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Collapsible, Footer, Markdown, Static, TextArea

from codaro.agent import Agent, AgentEvent, InvestigationCancelled
from codaro.edits import EditProposal
from codaro.index import CodeIndex, safe_preview
from codaro.interaction import COMMANDS, InputHistory, completions
from codaro.policies import ApprovalPolicy, Mode
from codaro.provider import (
    MAX_MESSAGE_CHARS,
    ContextCapacityError,
    ModelError,
    Settings,
    create_provider,
)
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
    #review { width: 95%; height: 90%; border: round $accent; background: $surface; }
    #review-title { height: auto; padding: 1 2; color: $accent; text-style: bold; }
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
    #scope-review { width: 95%; height: 90%; border: round $warning;
                    background: $surface; padding: 0 1; }
    #scope-fields { height: 1fr; }
    #scope-task { max-height: 2; }
    #scope-review Static { height: auto; }
    #scope-review TextArea { height: 4; }
    #scope-review Horizontal { height: 3; }
    #scope-review Button { width: 1fr; min-width: 8; padding: 0; }
    #scope-json { display: none; }
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
                id="scope-task",
                markup=False,
            )
            yield Static(
                "Permite criar/alterar/remover/renomear nos caminhos e executar os "
                "comandos exatos. Fora do escopo exige nova aprovação.",
                markup=False,
            )
            with VerticalScroll(id="scope-fields"):
                yield Static("Caminhos autorizados · um por linha; inclui subpastas", markup=False)
                yield TextArea("src\ntests", id="scope-paths")
                yield Static(
                    "Comandos exatos · um por linha; aspas preservam argumentos", markup=False
                )
                yield TextArea("", id="scope-commands")
                yield Static("", id="scope-preview", markup=False)
                yield Checkbox("Editar JSON avançado", id="scope-advanced")
                yield TextArea('{"paths": ["src", "tests"], "commands": []}', id="scope-json")
            yield Static(
                "Revogue com /permissions action. Fora do escopo exige nova aprovação.",
                markup=False,
            )
            yield Static("", id="scope-error", markup=False)
            with Horizontal():
                yield Button("Autorizar escopo", id="grant-scope", variant="warning")
                yield Button("Cancelar", id="cancel-scope")

    def on_mount(self):
        self.query_one("#cancel-scope", Button).focus()

    def on_checkbox_changed(self, event):
        if event.checkbox.id == "scope-advanced":
            editor = self.query_one("#scope-json", TextArea)
            editor.display = event.value
            if event.value:
                from codaro.ux_screens import ScopeFields

                try:
                    value = ScopeFields.parse(
                        self.query_one("#scope-paths", TextArea).text,
                        self.query_one("#scope-commands", TextArea).text,
                    )
                    editor.text = json.dumps(value, ensure_ascii=False, indent=2)
                except ValueError as exc:
                    self.query_one("#scope-error", Static).update(str(exc))

    def on_text_area_changed(self, event):
        if event.text_area.id in {"scope-paths", "scope-commands"}:
            paths = self.query_one("#scope-paths", TextArea).text
            commands = self.query_one("#scope-commands", TextArea).text
            self.query_one("#scope-preview", Static).update(
                "Escopo: "
                + ", ".join(paths.splitlines())
                + " · comandos: "
                + str(len(commands.splitlines()))
            )

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
            if self.query_one("#scope-advanced", Checkbox).value:
                value = json.loads(text)
            else:
                from codaro.ux_screens import ScopeFields

                value = ScopeFields.parse(
                    self.query_one("#scope-paths", TextArea).text,
                    self.query_one("#scope-commands", TextArea).text,
                )
            if not isinstance(value, dict) or set(value) != {"paths", "commands"}:
                raise ValueError("Use paths e commands.")
            ApprovalPolicy().grant("preview", value["paths"], value["commands"])
        except (ValueError, TypeError, RecursionError) as exc:
            self.query_one("#scope-error", Static).update(safe_preview(str(exc)))
            return
        self.dismiss(value)


class CommandReview(ModalScreen[bool]):
    DEFAULT_CSS = """
    CommandReview { align: center middle; background: #000000 65%; }
    #command-review { width: 95%; max-width: 100; height: 18; max-height: 90%; padding: 0 1;
                      border: round $warning; background: $surface; }
    #command-fields { height: 1fr; }
    #command-review Static { height: auto; margin-bottom: 1; }
    #command-review Horizontal { height: 3; }
    #command-review Button { width: 1fr; min-width: 8; padding: 0; }
    """
    BINDINGS = [Binding("escape", "reject", "Rejeitar", priority=True)]

    def __init__(self, root: Path, argv: list[str], timeout: int):
        super().__init__()
        self.root, self.argv, self.timeout = root, argv, timeout

    def compose(self) -> ComposeResult:
        with Vertical(id="command-review"):
            yield Static("Autorizar comando", markup=False)
            with VerticalScroll(id="command-fields"):
                yield Static(
                    safe_preview(
                        f"Diretório: {self.root}\nTimeout: {self.timeout}s\n\n"
                        + shlex.join(self.argv)
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


class ExternalToolReview(CommandReview):
    """Dedicated approval text: an external call is not a shell command."""

    def __init__(self, root, source, name, arguments):
        super().__init__(root, [], 30)
        self.source, self.tool_name, self.arguments = source, name, arguments

    def compose(self) -> ComposeResult:
        with Vertical(id="command-review"):
            yield Static("Autorizar ferramenta externa", markup=False)
            with VerticalScroll(id="command-fields"):
                yield Static(
                    safe_preview(
                        self.source
                        + " / "
                        + self.tool_name
                        + "\n"
                        + json.dumps(self.arguments, ensure_ascii=False, indent=2)
                    ),
                    markup=False,
                )
                yield Static(
                    "Pode produzir efeitos externos. Aprovação válida para esta chamada.",
                    markup=False,
                )
            with Horizontal():
                yield Button("Autorizar", id="approve-command", variant="warning")
                yield Button("Rejeitar", id="reject-command")


class GenerationPreview(Collapsible):
    """Visible live text, promoted only after the agent accepts an answer."""

    def __init__(self, *, reasoning: bool = False):
        self.reasoning = reasoning
        self.text = ""
        self.state = "generating"
        self.content = Static("", markup=False, classes="generation-text")
        super().__init__(
            self.content,
            title=(
                "Raciocínio enviado pelo modelo · provisório"
                if reasoning
                else "Texto em geração · não validado"
            ),
            classes="generation-preview reasoning-preview"
            if reasoning
            else "generation-preview response-preview",
            collapsed=False,
        )

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
        self.content.update(self.text + suffix)
        # Render completed Markdown blocks; keep unmatched fences literal while streaming.
        stable = (
            not self.reasoning
            and self.state in {"generating", "answer"}
            and self.text.count("```") % 2 == 0
        )
        if stable:
            self.content.update(RichMarkdown(self.text + suffix))

    def finish(self, state: str):
        self.state = state
        if self.reasoning:
            self.title = (
                "Raciocínio enviado pelo modelo · interrompido"
                if state in {"retry", "cancelled"}
                else "Raciocínio enviado pelo modelo · concluído"
            )
            self.collapsed = True
            return
        self.title = {
            "tools": "Etapa intermediária · chamada de ferramentas",
            "retry": "Prévia rejeitada · nova tentativa",
            "answer": "Prévia não validada · aguardando conclusão",
            "accepted": "Prévia da resposta · geração concluída",
            "cancelled": "Prévia interrompida · sem resposta concluída",
        }.get(state, "Prévia interrompida · sem resposta concluída")
        if state != "answer":
            self.update_text(self.text)
            self.collapsed = True

    def accept(self, answer: str) -> Markdown:
        self.state = "accepted"
        self.title = "Codaro · resposta final"
        self.collapsed = False
        reply = Markdown(answer, classes="assistant", open_links=False)

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


class ActivityGroup(Collapsible):
    """One expandable activity summary per turn; errors remain visible."""

    def __init__(self):
        self.events: list[AgentEvent] = []
        self.generations: list[GenerationPreview] = []
        self.details = Static("", markup=False)
        super().__init__(self.details, title="Investigação", classes="tool-card", collapsed=True)

    def add_preview(self, preview: GenerationPreview):
        self.generations.append(preview)

    def finish(self, successful: bool):
        if not self.events:
            self.title = "Geração concluída" if successful else "Geração interrompida"
            self.display = False

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


class CodaroApp(App):
    TITLE = "Codaro · explore seu código"
    CSS = """
    Screen { background: $background; }
    #brand { height: 1; margin: 1 2 0 2; color: $text; text-style: bold; }
    #session { height: auto; max-height: 2; margin: 0 2; color: $text-muted; }
    #session.insecure { color: $warning; }
    #conversation { width: 100%; height: 1fr; padding: 0 2; }
    #welcome { height: auto; max-width: 78; margin: 1; }
    #welcome-title { height: 1; color: $text; text-style: bold; }
    #welcome-description { height: auto; margin-bottom: 1; color: $text-muted; }
    #suggestions { height: 3; }
    #welcome Button { width: 1fr; min-width: 12; background: $panel; border: none; }
    #welcome Button:hover, #welcome Button:focus { border: round $accent; }
    #prompt { height: 3; max-height: 8; margin: 0 1; border: none;
              border-top: solid $primary; padding: 0 1; }
    #prompt:focus { border-top: solid $accent; }
    #completion { display: none; height: auto; max-height: 7; margin: 0 2; color: $text-muted; }
    #prompt-hint { height: 1; margin: 0 2; color: $text-muted; }
    #context-meter { height: 1; margin: 0 2; color: $text-muted; }
    #new-messages { display: none; height: 1; min-height: 1; border: none; margin: 0 2; }
    #status { height: 1; margin: 0 2; color: $text; }
    .recovery-actions { height: 3; }
    .recovery-actions Button { width: 1fr; min-width: 8; padding: 0; }
    #configure-start { height: 1; min-height: 1; width: auto; border: none; padding: 0; }
    .question { height: auto; margin: 1 0; color: $text; }
    .assistant { margin: 0; padding: 0 1; background: $background; }
    .speaker { height: 1; margin: 1 1 0 1; color: $text; text-style: bold; }
    .tool-card { height: auto; margin: 0 1; border: none; border-left: thick $primary;
                 padding: 0; }
    .tool-card.error { border-left: thick $error; }
    .tool-card.success { border-left: thick $success; }
    .tool-card CollapsibleTitle { color: $text-muted; }
    .tool-card > Contents { padding: 0 1; }
    .generation-previews { height: auto; }
    .generation-preview { height: auto; border: none; padding: 0; }
    .generation-preview > Contents { padding: 0 1; }
    .generation-text { height: auto; color: $text; }
    .generation-preview CollapsibleTitle { color: $text-muted; }
    .reasoning-preview { margin: 0 1 1 1; }
    .reasoning-preview .generation-text { color: $text-muted; text-style: italic;
                                         max-height: 8; overflow-y: auto; }
    .response-preview { margin: 1 0; }

    .notice { height: auto; margin: 1; color: $warning; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "Sair", priority=True, key_display="Ctrl+Q"),
        Binding("ctrl+l", "clear_chat", "Mensagens", priority=True, key_display="Ctrl+L"),
        Binding("ctrl+x", "cancel", "Cancelar", priority=True, key_display="Ctrl+X"),
        Binding("escape", "cancel", "Cancelar", show=False),
        Binding("ctrl+p", "command_palette", "Comandos", priority=True, key_display="Ctrl+P"),
    ]

    def action_command_palette(self):
        if self.use_command_palette and not CommandPalette.is_open(self):
            self.push_screen(CommandPalette(id="--command-palette", placeholder="Buscar comandos…"))

    def get_system_commands(self, screen):
        if isinstance(screen, ModalScreen):
            return
        yield SystemCommand(
            "Ajuda",
            "/help · Comandos e atalhos",
            lambda: self.run_worker(self.open_provider_menu("/help")),
        )
        if self.busy:
            yield SystemCommand(
                "Cancelar atividade",
                "Solicitar interrupção; rascunho preservado",
                self.action_cancel,
            )
            return
        actions = {
            "Cadastrar provedor": (
                "/providers",
                "Cadastrar API key e listar modelos do provedor (BYOK).",
            ),
            "Gerenciar provedores": (
                "/provider-manage",
                "Editar, testar, renomear e remover perfis.",
            ),
            "Selecionar provedor e modelo": (
                "/models",
                "Escolher entre os provedores cadastrados e seus modelos.",
            ),
            "Perguntar": ("/ask", "Consultar sem modificar arquivos"),
            "Planejar": ("/plan", "Investigar e definir etapas"),
            "Executar": ("/execute", "Implementar e validar com aprovação"),
            "Retomar conversa": ("/resume", "Retomar a última conversa salva"),
            "Revisar alterações": ("/changes", "Ver alterações e checkpoints"),
            "Permissões por tarefa": (
                "/permissions task",
                "Autorizar caminhos e comandos da tarefa ativa",
            ),
            "Revogar escopo": ("/permissions action", "Voltar à aprovação por ação"),
            "Contexto": ("/status", "Orçamento, origem e contagem"),
            "Recalibrar contexto": ("/recalibrate", "Reaprender limites do servidor"),
            "Compactar contexto": ("/compact", "Reduzir histórico enviado ao modelo"),
            "Cadastrar MCP ou plugin": (
                "/integrations",
                "Confiar, testar e cadastrar uma integração",
            ),
            "Desfazer interação": ("/undo-turn", "Revisar reversão de todos os arquivos"),
            "Refazer interação": ("/redo", "Revisar reaplicação da interação desfeita"),
            "Sessões": ("/sessions", "Conversas independentes neste projeto"),
            "Funcionalidades e integrações": (
                "/features",
                "Contexto, exploração, MCP, plugins e LSP",
            ),
            "Histórico": ("/history", "Consultar a memória da conversa"),
            "Tarefas": ("/task list", "Ver tarefas deste projeto"),
            "Desfazer limpeza": ("/restore-clear", "Restaurar mensagens e contexto"),
        }
        for title, (command, description) in actions.items():
            yield SystemCommand(
                title,
                command + " · " + description,
                lambda cmd=command: self.run_worker(self.open_provider_menu(cmd)),
            )
        yield SystemCommand(
            "Nova conversa e tarefa",
            "/new · Revogar escopo e começar outra atividade",
            self.action_new_conversation,
        )
        yield SystemCommand(
            "Limpar mensagens",
            "/clear · Manter tarefa, escopo e propostas; permite desfazer",
            lambda: self.run_worker(self.action_clear_chat()),
        )
        yield SystemCommand(
            "Mostrar/ocultar raciocínio",
            "/reasoning · Alternar prévias do modelo",
            self.toggle_reasoning,
        )
        # Localize the framework actions while retaining theme and keyboard functionality.
        labels = {
            "Theme": ("Tema", "Escolher tema da interface"),
            "Quit": ("Sair", "Encerrar o Codaro"),
            "Keys": ("Atalhos", "Mostrar teclas disponíveis"),
            "Maximize": ("Ampliar painel", "Ampliar elemento atual"),
            "Screenshot": ("Capturar tela", "Salvar captura SVG"),
        }
        for item in super().get_system_commands(screen):
            if item.title in labels:
                title, help_text = labels[item.title]
                yield SystemCommand(title, help_text, item.callback)

    def toggle_reasoning(self):
        self.show_reasoning = not self.show_reasoning
        if self.reasoning_preview is not None:
            self.reasoning_preview.collapsed = not self.show_reasoning
        self.query_one("#status", Static).update(
            "Raciocínio expandido" if self.show_reasoning else "Raciocínio recolhido"
        )

    async def open_provider_menu(self, command: str):
        if (self.busy and command not in {"/help", "/status"}) or isinstance(
            self.screen, ModalScreen
        ):
            return
        prompt = self.query_one(Prompt)
        draft = prompt.value
        try:
            await self.local_command(command)
        finally:
            prompt.value = draft

    async def action_register_provider(self):
        await self.open_provider_menu("/providers")

    async def action_select_provider_model(self):
        await self.open_provider_menu("/models")

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
        self.answer_preview: GenerationPreview | None = None
        self.reasoning_preview: GenerationPreview | None = None
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
        self.session = agent.sessions.store(
            agent.session_id, getattr(getattr(agent.provider, "settings", None), "api_key", "")
        )
        self.session_turns: list[list[dict]] = list(agent.turns)
        self.approval_dialog: CommandReview | None = None
        if not agent.legacy or agent.allow_edits:
            agent.approve_command = self.approve_command
        if not agent.legacy:
            agent.approve_edit = self.approve_changes
        agent.approve_external = self.approve_external
        self.plan_card: Collapsible | None = None
        self.clear_backup = None
        self.show_reasoning = True
        self.provider_available = True

    def shortcut_display(self, key: str) -> str:
        key = key.upper()
        return f"⌘{key} / Ctrl+{key}" if self.is_macos else f"Ctrl+{key}"

    def option_display(self, key: str) -> str:
        return f"⌥{key}" if self.is_macos else f"Alt+{key}"

    def get_key_display(self, binding: Binding) -> str:
        if self.size.width < 80 and binding.key.startswith("ctrl+"):
            return ("⌘" if self.is_macos else "^") + binding.key.removeprefix("ctrl+").upper()
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
            Button("Configurar provedor", id="configure-start"),
            id="welcome",
        )

    def compose(self) -> ComposeResult:
        yield Static("◈ Codaro", id="brand", markup=False)
        yield Static("", id="session", markup=False)
        with VerticalScroll(id="conversation"):
            yield self.welcome()
        yield Button("Novas mensagens ↓", id="new-messages")
        yield Static(
            "Contexto: aguardando primeira chamada · /status", id="context-meter", markup=False
        )
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

    def on_resize(self):
        if self.query("#session"):
            self.update_session_header()

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
        width = max(20, self.size.width - 4)
        identity = f"{root} · {settings.model}"
        if len(identity) > width:
            identity = identity[: width - 1] + "…"
        summary = (
            f"{identity} · {mode}" if len(identity + mode) + 6 < width else f"{identity}\n{mode}"
        )
        summary = f"{tls} · {summary}" if settings.tls_insecure else f"{summary} · {tls}"
        session = self.query_one("#session", Static)
        session.update(safe_preview(summary))
        session.set_class(settings.tls_insecure, "insecure")
        session.tooltip = safe_preview(str(self.agent.repository.root) + " · " + settings.model)
        self.query_one("#prompt-hint", Static).update(
            f"Enter envia · {self.option_display('Enter')} linha · / ajuda · @ arquivo"
            if width < 80
            else f"Enter envia · {self.option_display('Enter')} nova linha · "
            "/ comandos · @ arquivo · ↑ histórico"
        )

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
            "/session",
            "/features",
        }:
            self.mount_message(Static("Este comando não recebe argumentos.", classes="notice"))
            return
        if (
            name
            in {
                "/resume",
                "/recalibrate",
                "/compact",
                "/model",
                "/models",
                "/providers",
                "/provider-manage",
                "/undo",
            }
            and self.agent.edits.pending
        ):
            self.mount_message(Static("Revise as edições pendentes primeiro.", classes="notice"))
            return
        self.query_one(Prompt).value = ""
        if name == "/integrations":
            if self.busy or self.agent.edits.pending:
                self.mount_message(
                    Static(
                        "Aguarde a atividade e revise propostas primeiro.",
                        classes="notice",
                        markup=False,
                    )
                )
                return
            from codaro.extension_screens import IntegrationRegistration

            def configured(saved):
                if saved:
                    from codaro.features import FeatureStore

                    self.agent.features = FeatureStore(self.agent.repository.root).load()
                    self.agent.context.features = self.agent.features
                    self.mount_message(
                        Static(
                            "Integração cadastrada; disponível na próxima atividade.",
                            classes="notice",
                            markup=False,
                        )
                    )

            self.push_screen(IntegrationRegistration(self.agent.repository.root), configured)
            return
        if name in {"/undo-turn", "/redo"}:
            if self.busy or self.agent.edits.pending:
                self.mount_message(
                    Static(
                        "Aguarde a atividade e revise propostas primeiro.",
                        classes="notice",
                        markup=False,
                    )
                )
                return
            self.busy = True
            self.cancelled.clear()
            self.reverse_interaction(name == "/redo")
            return
        if name in {"/sessions", "/session", "/features"}:
            try:
                if self.busy or self.agent.edits.pending:
                    raise ValueError("Aguarde a atividade e revise propostas primeiro.")
                if name == "/features":
                    from codaro.features import FeatureStore

                    store = FeatureStore(self.agent.repository.root)
                    if argument:
                        feature, _, action = argument.partition(" ")
                        if action not in {"on", "off"}:
                            raise ValueError("Use /features nome on|off.")
                        self.agent.features = store.toggle(feature, action == "on")
                        self.agent.context.features = self.agent.features
                    content = json.dumps(store.load(), ensure_ascii=False, indent=2)
                    self.mount_message(
                        Static(
                            "Funcionalidades (on/off) e integrações:\n" + content,
                            classes="notice",
                            markup=False,
                        )
                    )
                    return
                if name == "/sessions" or not argument:
                    data = self.agent.sessions.load()
                    content = "\n".join(
                        ("● " if item["id"] == self.agent.session_id else "  ")
                        + item["id"]
                        + " · "
                        + item["title"]
                        for item in data["items"]
                    )
                    self.mount_message(
                        Static(
                            content + "\n/session new título ou /session identificador",
                            classes="notice",
                            markup=False,
                        )
                    )
                    return
                self.save_session()
                if argument == "new" or argument.startswith("new "):
                    identifier = self.agent.sessions.create(argument[4:].strip() or "Nova conversa")
                else:
                    identifier = argument
                self.session = self.agent.activate_session(identifier)
                self.session.secret = getattr(self.agent.provider.settings, "api_key", "")
                try:
                    self.session_turns = self.session.load()
                except FileNotFoundError:
                    self.session_turns = []
                    self.session.save([], self.agent.provider.settings.model)
                if self.session_turns:
                    await self.restore_session()
                else:
                    await self.query_one("#conversation", VerticalScroll).remove_children()
                self.mount_message(
                    Static(
                        "Sessão ativa: " + identifier + ". Permissões anteriores revogadas.",
                        classes="notice",
                        markup=False,
                    )
                )
                self.update_session_header()
            except (OSError, ValueError) as exc:
                self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
            return
        if name == "/new":
            await self.action_new_conversation()
            return
        if name == "/restore-clear":
            await self.restore_clear()
            return
        if name == "/reasoning":
            self.toggle_reasoning()
            return
        if name == "/provider-manage":
            from codaro.providers import ProviderStore
            from codaro.ux_screens import ProviderManager

            try:
                self.push_screen(ProviderManager(ProviderStore()), self.manage_provider_decision)
            except (ValueError, OSError) as exc:
                self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
            return
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
                policy_label = "por tarefa" if self.agent.policy.kind == "task" else "por ação"
                text = f"Modo: {self.agent.mode.label}. Permissões: {policy_label}."
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
        elif name in {"/providers", "/models", "/model"}:
            from codaro.provider_ui import ModelPicker, ProviderSetup
            from codaro.providers import ProviderStore

            store = ProviderStore()
            try:
                configured = store.load()
            except (ValueError, OSError) as exc:
                self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
                return
            if name == "/providers":
                self.push_screen(
                    ProviderSetup(store), lambda name: self.provider_registered(name, store)
                )
                return
            if not argument and configured["profiles"]:
                self.push_screen(ModelPicker(store), self.activate_provider)
                return
            if name == "/models":
                self.push_screen(
                    ProviderSetup(store), lambda name: self.provider_registered(name, store)
                )
                return
            if argument:
                try:
                    current = self.agent.provider.settings
                    settings = (
                        await asyncio.to_thread(store.select, current.provider_id, argument)
                        if current.provider_id
                        else replace(current, model=argument)
                    )
                    self.activate_provider(settings)
                except (ValueError, OSError, ModelError) as exc:
                    self.mount_message(
                        Static(safe_preview(str(exc)), classes="notice", markup=False)
                    )
                    return
            text = f"Modelo: {self.agent.provider.settings.model}\nTroque com /model nome"
        elif name == "/recalibrate":
            self.agent.reset_calibration()
            text = "Calibração removida. A próxima chamada verificará os limites novamente."
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
                f"origem: {self.agent.provider.settings.context_source}\n"
                f"reserva de saída: {self.agent.max_output_tokens} · margem: 512\n"
                f"Contagem: {self.agent.counter.method}\n"
                f"Tokens informados pelo servidor: {self.reported_tokens}\n"
                f"Calibração: {self.agent.counter.scale:.2f} · "
                f"amostras: {len(self.agent.counter.samples)}\n"
                f"Sessão: {self.session.path}\nDebug: .codaro/prompt.json"
            )
        self.mount_message(Static(safe_preview(text), classes="question", markup=False))

    def manage_provider_decision(self, result):
        if not result:
            return
        from codaro.provider_ui import ProviderSetup
        from codaro.providers import ProviderStore

        store = ProviderStore()
        if result.startswith("removed:"):
            if self.agent.provider.settings.provider_id == result.removeprefix("removed:"):
                self.provider_available = False
                self.mount_message(
                    Static("Provedor ativo removido · selecione outro modelo.", classes="notice")
                )
            return
        profile = result.removeprefix("edit:") if result.startswith("edit:") else None
        self.push_screen(
            ProviderSetup(store, profile=profile),
            lambda name: self.provider_edited(profile, name, store),
        )

    def provider_edited(self, previous, name, store):
        if name and previous == self.agent.provider.settings.provider_id:
            try:
                self.activate_provider(store.active_settings(name), preserve_session=False)
            except (ValueError, OSError) as exc:
                self.provider_available = False
                self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
        self.provider_registered(name, store)

    def provider_registered(self, name, store=None):
        if name is not None:
            from codaro.provider_ui import ModelPicker

            self.push_screen(ModelPicker(store, profile=name), self.activate_provider)

    def activate_provider(self, settings: Settings | None, *, preserve_session=True):
        if settings is None:
            return
        try:
            current = self.agent.provider.settings
            if (
                preserve_session
                and current.provider_id == settings.provider_id
                and current.base_url == settings.base_url
            ):
                settings = replace(
                    settings,
                    tls_insecure=current.tls_insecure,
                    timeout=current.timeout,
                    token_encoding=current.token_encoding,
                )
            self.agent.set_provider(create_provider(settings))
            self.provider_available = True
            self.session.secret = settings.api_key
            self.session.redact = self.agent.memory.redact
            self.session_turns = self.session.redact(self.session_turns)
            self.context_limit = self.agent.adaptive_input_limit
            self.context_chars = self.context_tokens = 0
            self.reported_tokens = None
            self.update_session_header()
            self.mount_message(
                Static(
                    safe_preview(
                        f"Modelo selecionado: {settings.provider_id or 'ambiente'} / "
                        f"{settings.model}\n"
                        f"Contexto: {settings.context_window} tokens · {settings.context_source}"
                    ),
                    classes="notice",
                    markup=False,
                )
            )
        except (ValueError, OSError) as exc:
            self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))

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

    @work(thread=True, exclusive=True, group="reversal")
    def reverse_interaction(self, redo):
        from codaro.storage import private_lock
        from codaro.undo_history import UndoHistory

        try:
            with private_lock(self.agent.repository.root / ".codaro/agent.lock"):
                history = UndoHistory(self.agent.edits, self.agent.session_id)
                record, proposals = history.preview(redo=redo)
                if not self.approve_changes(proposals, self.cancelled):
                    message = "Reversão cancelada; nenhum arquivo alterado."
                elif self.cancelled.is_set():
                    message = "Reversão cancelada; nenhum arquivo alterado."
                else:
                    history.apply(record, proposals, redo=redo)
                    self.agent.turns.clear()
                    self.agent.context.summary = None
                    self.agent.sessions.summary_path(self.agent.session_id).unlink(missing_ok=True)
                    self.agent.edits.observed.clear()
                    self.agent.policy.reset()
                    with CodeIndex(self.agent.repository) as index:
                        self.agent.sync_workspace(index)
                    message = (
                        "Interação refeita." if redo else "Interação desfeita."
                    ) + " Contexto e permissões reiniciados; histórico mantido para consulta."
            self.deliver(self.reversal_finished, message)
        except (OSError, ValueError) as exc:
            self.deliver(self.reversal_finished, safe_preview(str(exc)))

    def reversal_finished(self, message):
        self.busy = False
        self.session_turns.append(
            [
                {"role": "user", "content": "Reversão de interação"},
                {"role": "assistant", "content": message},
            ]
        )
        self.save_session()
        self.mount_message(Static(message, classes="notice", markup=False))
        self.query_one("#status", Static).update("Pronto · revisão concluída")
        self.update_session_header()

    def approve_external(self, source, name, arguments, cancelled):
        decision, ready = [], threading.Event()

        def show():
            dialog = ExternalToolReview(self.agent.repository.root, source, name, arguments)
            self.approval_dialog = dialog
            self.query_one("#status", Static).update("Aguardando aprovação da ferramenta externa")

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
        follow = conversation.is_vertical_scroll_end
        conversation.mount(widget)
        if follow:
            self.call_after_refresh(conversation.scroll_end, animate=False)
        else:
            self.query_one("#new-messages", Button).display = True

    async def on_prompt_submitted(self, event: Prompt.Submitted):
        question = event.value.strip()
        if not question:
            return
        if self.busy:
            self.query_one("#status", Static).update(
                "Agente trabalhando · rascunho preservado · aguarde para enviar"
            )
            return
        if not self.provider_available and not question.startswith("/"):
            self.mount_message(
                Static("Selecione outro provedor e modelo antes de enviar.", classes="notice")
            )
            await self.local_command("/models")
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
        for button in self.query(".recovery-actions Button"):
            button.disabled = True
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
        self.answer_preview = None
        self.busy = True
        self.cancelled.clear()
        self.response_text = self.rendered_text = ""
        self.reply = None
        self.speaker = None
        event.input.value = ""
        event.input.disabled = False
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
                on_reasoning=lambda delta: self.deliver(self.append_reasoning, delta),
            )
            successful = True
        except InvestigationCancelled:
            answer = "Investigação cancelada."
        except ContextCapacityError as exc:
            answer = str(exc)
        except (ModelError, ValueError, OSError) as exc:
            message = str(exc)
            if self.agent.provider.settings.provider_id:
                message = message.replace(
                    "Confira CODARO_BASE_URL", "Confira a URL em Ctrl+P → Gerenciar provedores"
                )
            answer = f"Não foi possível concluir: {message}"
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
            self.query_one("#context-meter", Static).update(
                f"Contexto ≈ {event.context_tokens or 0:,}/"
                f"{event.context_limit or self.agent.adaptive_input_limit:,} · "
                f"{self.agent.provider.settings.context_source} · /status"
            )
        elif event.kind == "compaction" or event.title in {
            "Contexto liberado",
            "Contexto reduzido",
        }:
            self.query_one("#context-meter", Static).update(
                "Contexto compactado automaticamente · /status"
            )
        if event.kind == "plan":
            self.show_plan()
        elif event.kind == "model_start":
            self.update_session_header()
            self.finish_preview("retry")
            if self.answer_preview is not None:
                self.answer_preview.finish("retry")
                self.answer_preview = None
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
        self.draft_text = (self.draft_text + safe_preview(delta))[:MAX_MESSAGE_CHARS]
        self.query_one("#status", Static).update(
            f"Gerando resposta… · {self.shortcut_display('x')} para cancelar"
        )
        # Render the first fragment immediately; subsequent fragments are coalesced by the timer.
        if self.generation_preview is None:
            self.flush_preview()

    def append_reasoning(self, delta: str):
        if self.reasoning_preview is None:
            self.reasoning_preview = GenerationPreview(reasoning=True)
            self.reasoning_preview.collapsed = not self.show_reasoning
            self.mount_message(self.reasoning_preview)
        self.reasoning_preview.update_text(
            (self.reasoning_preview.text + safe_preview(delta))[:4001]
        )
        self.query_one("#status", Static).update("Modelo raciocinando…")

    def flush_preview(self):
        if not self.draft_text or self.draft_text == self.draft_rendered:
            return
        conversation = self.query_one("#conversation", VerticalScroll)
        follow = conversation.is_vertical_scroll_end
        if self.generation_preview is None:
            self.generation_preview = GenerationPreview()
            if self.activity_group is None:
                self.activity_group = ActivityGroup()
                self.mount_message(self.activity_group)
            self.activity_group.add_preview(self.generation_preview)
            self.mount_message(self.generation_preview)
        self.generation_preview.update_text(self.draft_text)
        self.draft_rendered = self.draft_text
        if follow:
            self.call_after_refresh(conversation.scroll_end, animate=False)

    def finish_preview(self, state: str):
        self.flush_preview()
        if self.generation_preview is not None:
            self.generation_preview.finish(state)
            if state == "answer":
                self.answer_preview = self.generation_preview
        if self.reasoning_preview is not None:
            self.reasoning_preview.finish(state)
            self.reasoning_preview = None
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
            if self.answer_preview is not None:
                self.reply = self.answer_preview.accept(self.response_text)
                self.rendered_text = self.response_text
                self.answer_preview = None
            else:
                self.flush_response()
        else:
            if self.answer_preview is not None:
                self.answer_preview.finish("cancelled")
                self.answer_preview = None
            self.discard_response()
            settings = self.agent.provider.settings
            self.mount_message(
                Static(
                    safe_preview(
                        f"{settings.provider_id or 'Local/ambiente'} / {settings.model}\n" + answer
                    ),
                    classes="notice",
                    markup=False,
                )
            )
            self.mount_message(
                Horizontal(
                    Button("Repetir", id="retry-question"),
                    Button("Configurar", id="recover-provider"),
                    Button("Outro modelo", id="recover-model"),
                    classes="recovery-actions",
                )
            )
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
        if self.busy or isinstance(self.screen, ModalScreen):
            return
        self.clear_backup = (copy.deepcopy(self.agent.turns), copy.deepcopy(self.session_turns))
        self.agent.turns.clear()
        self.session_turns.clear()
        self.response_text = self.rendered_text = ""
        self.reply = self.speaker = None
        self.activity_group = self.plan_card = None
        self.answer_preview = self.generation_preview = self.reasoning_preview = None
        conversation = self.query_one("#conversation", VerticalScroll)
        await conversation.remove_children()
        await conversation.mount(self.welcome())
        self.proposal_cards.clear()
        for proposal in self.agent.edits.pending:
            card = ProposalCard(proposal)
            self.proposal_cards[proposal.id] = card
            self.mount_message(card)
        self.show_plan()
        self.save_session()
        self.query_one(Prompt).focus()
        self.query_one("#new-messages").display = False
        self.query_one("#status", Static).update(
            "Mensagens limpas · tarefa, escopo e propostas mantidos · /restore-clear desfaz"
        )

    async def restore_clear(self):
        if self.clear_backup is None:
            self.mount_message(Static("Não há limpeza para desfazer.", classes="notice"))
            return
        self.agent.turns, self.session_turns = self.clear_backup
        self.clear_backup = None
        self.save_session()
        await self.restore_session()
        self.proposal_cards.clear()
        for proposal in self.agent.edits.pending:
            card = ProposalCard(proposal)
            self.proposal_cards[proposal.id] = card
            self.mount_message(card)
        self.plan_card = None
        self.show_plan()

    async def action_new_conversation(self):
        if self.busy or isinstance(self.screen, ModalScreen):
            return
        from codaro.ux_screens import NewConversation

        self.push_screen(NewConversation(), self.begin_conversation)

    def begin_conversation(self, objective):
        if objective is None:
            return
        self.run_worker(self.reset_conversation(objective))

    async def reset_conversation(self, objective):
        try:
            for proposal in list(self.agent.edits.pending):
                self.agent.edits.reject(proposal.id)
            self.agent.policy.reset()
            self.agent.tasks.mutate(lambda data: data.update(active=None))
            self.agent.memory.clear_task()
            await self.action_clear_chat()
            self.clear_backup = None
            self.history = InputHistory()
            if objective:
                self.agent.tasks.start(objective, new=True)
            self.update_session_header()
            self.query_one("#status", Static).update(
                "Nova conversa · permissões por ação · propostas anteriores descartadas"
            )
        except (ValueError, OSError, ModelError) as exc:
            self.mount_message(
                Static(
                    safe_preview(f"Não foi possível iniciar outra conversa: {exc}"),
                    classes="notice",
                    markup=False,
                )
            )

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "new-messages":
            self.query_one("#conversation", VerticalScroll).scroll_end(animate=False)
            event.button.display = False
            return
        if event.button.id == "retry-question" and not self.busy:
            prompt = self.query_one(Prompt)
            prompt.value = self.active_question
            self.post_message(Prompt.Submitted(prompt))
            return
        if (
            event.button.id in {"recover-provider", "recover-model", "configure-start"}
            and not self.busy
        ):
            self.run_worker(
                self.open_provider_menu(
                    "/providers" if event.button.id != "recover-model" else "/models"
                )
            )
            return
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
