from __future__ import annotations

import asyncio
import copy
import json
import logging
import sqlite3
import sys
import threading
import time
from dataclasses import replace

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import CommandPalette
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static, TextArea

from codaro.agent import Agent, AgentEvent, InvestigationCancelled
from codaro.index import CodeIndex, safe_preview
from codaro.interaction import COMMANDS, InputHistory, completions
from codaro.llm import (
    ContextCapacityError,
    ModelError,
    Settings,
)
from codaro.policies import Mode
from codaro.storage import private_lock
from codaro.ui import provider_actions
from codaro.ui import streaming as stream_ui
from codaro.ui.composer import Composer, Prompt
from codaro.ui.formatting import short_path
from codaro.ui.palette import system_commands
from codaro.ui.plan import ExecutionPlan
from codaro.ui.recovery import RecoveryCard, failure_kind
from codaro.ui.reviews import (
    ChangesReview,
    CommandReview,
    EditReview,
    ExternalToolReview,
    ScopeReview,
)
from codaro.ui.theme import CODARO_THEME
from codaro.ui.widgets import ActivityGroup, GenerationPreview, ProposalCard, ReasoningGroup

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class CodaroApp(App):
    TITLE = "Codaro · explore seu código"
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Sair", priority=True, key_display="Ctrl+Q"),
        Binding("ctrl+l", "clear_chat", "Mensagens", priority=True, key_display="Ctrl+L"),
        Binding("ctrl+x", "cancel", "Cancelar", priority=True, key_display="Ctrl+X"),
        Binding("escape", "escape", "Voltar", show=False),
        Binding("ctrl+p", "command_palette", "Comandos", priority=True, key_display="Ctrl+P"),
    ]

    def action_command_palette(self):
        if self.use_command_palette and not CommandPalette.is_open(self):
            self.push_screen(CommandPalette(id="--command-palette", placeholder="Buscar comandos…"))

    def get_system_commands(self, screen):
        if isinstance(screen, ModalScreen):
            return
        yield from system_commands(self, super().get_system_commands(screen))

    def toggle_reasoning(self):
        self.show_reasoning = not self.show_reasoning
        if self.reasoning_group is not None:
            self.reasoning_group.collapsed = not self.show_reasoning
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
        selection = prompt.selection
        try:
            await self.local_command(command)
        finally:
            prompt.value = draft
            prompt.selection = selection

    async def action_register_provider(self):
        await self.open_provider_menu("/providers")

    async def action_select_provider_model(self):
        await self.open_provider_menu("/models")

    def __init__(
        self,
        agent: Agent,
        *,
        resume: bool = False,
        setup_required: bool = False,
        setup_profile: str | None = None,
    ):
        super().__init__()
        self.register_theme(CODARO_THEME)
        self.theme = CODARO_THEME.name
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
        self.cancel_plan_id: str | None = None
        self.clear_backup = None
        self.show_reasoning = False
        self.reasoning_group: ReasoningGroup | None = None
        self.provider_available = not setup_required
        self.setup_required = setup_required
        self.setup_profile = setup_profile
        self.completions_dismissed = False
        self.pending_recovery = None
        self.retry_recovery = None
        self.generation_started = 0.0
        self.generation_phase = "Investigando…"

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
        children = []
        if not self.provider_available:
            children = [
                Static("Conecte um provedor para começar.", markup=False),
                Button("Configurar provedor", id="configure-start"),
            ]
        return Vertical(*children, id="welcome")

    def compose(self) -> ComposeResult:
        yield Static("◈ Codaro", id="brand", markup=False)
        with Horizontal(id="session-header"):
            yield Static("", id="session", markup=False)
            yield Button(self.agent.mode.label + " ▾", id="select-mode")
        with VerticalScroll(id="conversation"):
            yield self.welcome()
        yield Button("Novas mensagens ↓", id="new-messages")
        yield Composer(id="composer")

    async def on_mount(self):
        self.update_session_header()
        self.set_interval(0.08, self.flush_preview)
        self.set_interval(1, self.update_generation_status)
        self.query_one(Prompt).focus()
        self.refresh_reference_paths()
        if self.resume_requested:
            await self.restore_session()
        try:
            self.show_plan()
        except (ValueError, OSError) as exc:
            self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
        if self.setup_required:
            self.call_after_refresh(self.start_setup)

    def start_setup(self):
        from codaro.llm.profiles import ProviderStore
        from codaro.ui.providers import ModelPicker, ProviderSetup

        store = ProviderStore()
        try:
            if store.load()["profiles"]:
                self.push_screen(
                    ModelPicker(store, profile=self.setup_profile, onboarding=True),
                    self.activate_provider,
                )
            else:
                self.push_screen(
                    ProviderSetup(store, onboarding=True),
                    lambda name: self.provider_registered(name, store),
                )
        except (ValueError, OSError) as exc:
            self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))

    def on_resize(self):
        if self.query("#session"):
            self.update_session_header()
            self.update_completions()

    def update_session_header(self):
        settings = self.agent.provider.settings
        tls = "TLS sem verificação" if settings.tls_insecure else "TLS verificação ativa"
        if settings.base_url.startswith("http://"):
            tls = "HTTP"
        root = short_path(self.agent.repository.root)
        width = max(10, self.size.width - 20)
        identity = (
            f"{root} · {settings.model if self.provider_available else 'selecione um modelo'}"
        )
        if len(identity) > width:
            identity = identity[: width - 1] + "…"
        summary = f"{tls} · {identity}" if settings.tls_insecure else identity
        if len(summary) > width:
            summary = summary[: width - 1] + "…"
        session = self.query_one("#session", Static)
        session.update(safe_preview(summary))
        session.set_class(settings.tls_insecure, "insecure")
        session.tooltip = safe_preview(
            str(self.agent.repository.root)
            + " · "
            + settings.model
            + " · "
            + tls
            + (" · Aprovação por tarefa" if self.agent.policy.kind == "task" else "")
        )
        mode = self.query_one("#select-mode", Button)
        mode.label = (
            self.agent.mode.label + (" · auto" if self.agent.policy.auto_edits else "") + " ▾"
        )
        mode.disabled = self.busy
        mode.tooltip = (
            "Edições automáticas nesta conversa; comandos e remoções exigem aprovação"
            if self.agent.policy.auto_edits
            else "Trocar modo e revisar a aprovação de edições"
        )
        self.query_one("#prompt-hint", Static).update(
            f"Enter envia · {self.option_display('Enter')} linha · / comandos"
            if width < 80
            else f"Enter envia · {self.option_display('Enter')} nova linha · "
            "/ comandos · Ctrl+P menu"
        )
        self.query_one(ExecutionPlan).fit(self.size.height)

    def open_mode_picker(self):
        if self.busy or isinstance(self.screen, ModalScreen):
            return
        from codaro.ui.modes import ModePicker

        self.push_screen(
            ModePicker(self.agent.mode, auto_edits=self.agent.policy.auto_edits),
            self.mode_selected,
        )

    def mode_selected(self, selection):
        if selection is None:
            self.query_one(Prompt).focus()
            return
        mode, automatic = selection
        try:
            self.agent.set_mode(mode)
            self.agent.policy.auto_edits = automatic
            self.update_session_header()
            self.query_one("#status", Static).update(
                "Edições automáticas · comandos e remoções com autorização"
                if automatic
                else "Modo: " + mode.label + " · edições com autorização"
            )
        except (ValueError, OSError) as exc:
            self.query_one("#status", Static).update(safe_preview(str(exc)))
        self.query_one(Prompt).focus()

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
        prompt = self.query_one(Prompt)
        unread_row = int(self.query_one("#new-messages").display)
        max_height = max(3, min(8, self.size.height - 16 - unread_row))
        prompt.styles.height = min(max_height, max(3, prompt.wrapped_document.height + 2))
        menu.display = bool(options) and not self.completions_dismissed
        self.query_one(Prompt).suggesting = menu.display
        if options and menu.display:
            self.completion_index %= len(options)
            # Reserve at least four rows of chat, even when the composer grows.
            fixed = (
                6
                + unread_row
                + self.query_one("#session").region.height
                + int(prompt.styles.height.value)
                + self.query_one(ExecutionPlan).region.height
            )
            count = max(1, min(5, self.size.height - fixed - 5))
            first = max(0, min(self.completion_index - count // 2, len(options) - count))
            rows = []
            width = max(10, self.size.width - 4)
            for i in range(first, min(first + count, len(options))):
                label = ("› " if i == self.completion_index else "  ") + safe_preview(
                    options[i].label
                )
                row = Text(label, no_wrap=True, overflow="ellipsis")
                row.truncate(width, overflow="ellipsis")
                rows.append(row)
            menu.styles.height = len(rows) + 1
            menu.update(
                Text("\n").join(
                    [*rows, Text("↑↓ escolher · Enter abrir · Tab completa · Esc fecha")]
                )
            )

    def action_escape(self):
        if self.query_one("#completion").display:
            self.completions_dismissed = True
            self.update_completions()
        else:
            self.action_cancel()

    def set_generation_status(self, phase):
        self.generation_phase = phase
        if self.busy:
            if not self.generation_started:
                self.generation_started = time.monotonic()
            self.update_generation_status()
        else:
            self.query_one("#status", Static).update(phase)

    def update_generation_status(self):
        if self.busy and self.generation_started:
            elapsed = max(0, int(time.monotonic() - self.generation_started))
            self.query_one("#status", Static).update(
                f"{self.generation_phase} · {elapsed}s · {self.shortcut_display('x')} cancelar"
            )

    def on_prompt_complete(self, event: Prompt.Complete):
        options = self.completion_options()
        if not options or not self.query_one(Prompt).suggesting:
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
        if (
            self.query_one(Prompt).suggesting
            and self.completion_options()
            and not event.history_only
        ):
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
        if name == "/mode" and not argument:
            self.open_mode_picker()
            return
        if name == "/settings":
            from codaro.ui.preferences import PreferencesDialog

            try:
                self.push_screen(PreferencesDialog(), self.preferences_saved)
            except (ValueError, OSError) as exc:
                self.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
            return
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
            from codaro.ui.integrations import IntegrationRegistration

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
            from codaro.llm.profiles import ProviderStore
            from codaro.ui.dialogs import ProviderManager

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
            from codaro.llm.profiles import ProviderStore
            from codaro.ui.providers import ModelPicker, ProviderSetup

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
                f"Edições automáticas nesta conversa: {self.agent.policy.auto_edits}\n"
                f"Turnos no contexto: {len(self.agent.turns)}\n"
                f"Turnos da conversa: {len(self.session_turns)}\n"
                f"Último contexto enviado: {self.context_chars} "
                f"caracteres / limite {self.agent.context_budget}\n"
                f"Entrada estimada: {self.context_tokens} / {self.context_limit} tokens\n"
                f"Janela configurada: {self.agent.context_window} tokens · "
                f"origem: {self.agent.provider.settings.context_source}\n"
                "saída: "
                f"{self.agent.provider.settings.max_output_tokens or 'Automático'}\n"
                f"reserva de contexto: {self.agent.max_output_tokens} · margem: 512\n"
                f"Contagem: {self.agent.counter.method}\n"
                f"Tokens informados pelo servidor: {self.reported_tokens}\n"
                f"Calibração: {self.agent.counter.scale:.2f} · "
                f"amostras: {len(self.agent.counter.samples)}\n"
                f"Sessão: {self.session.path}\nDebug: .codaro/prompt.json"
            )
        self.mount_message(Static(safe_preview(text), classes="question", markup=False))

    def manage_provider_decision(self, result):
        return provider_actions.manage_provider_decision(self, result)

    def provider_edited(self, previous, name, store):
        return provider_actions.provider_edited(self, previous, name, store)

    def provider_registered(self, name, store=None):
        return provider_actions.provider_registered(self, name, store)

    def preferences_saved(self, saved):
        if saved:
            self.query_one("#status", Static).update(
                "Preferências salvas · usadas na próxima inicialização"
            )

    def activate_provider(self, settings: Settings | None, *, preserve_session=True):
        return provider_actions.activate_provider(self, settings, preserve_session=preserve_session)

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
        self.reasoning_group = None
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
        self.completions_dismissed = False
        prompt.styles.height = min(8, max(3, prompt.wrapped_document.height + 2))
        self.update_completions()
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
            button = self.query_one("#new-messages", Button)
            if not button.display:
                button.display = True
                self.update_completions()

    async def on_prompt_submitted(self, event: Prompt.Submitted):
        if self.query_one(Prompt).suggesting and event.value.startswith("/"):
            options = self.completion_options()
            if options:
                command = options[self.completion_index % len(options)].value.strip()
                self.completions_dismissed = True
                self.query_one(Prompt).value = ""
                await self.open_provider_menu(command)
                self.query_one(Prompt).focus()
                return
        await self.submit_question(event.value)

    async def submit_question(self, value: str, *, preserve_draft=False):
        question = value.strip()
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
            await self.open_provider_menu("/models")
            return
        if len(value) > 8000:
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
        if self.pending_recovery is not None:
            if preserve_draft:
                self.retry_recovery = self.pending_recovery
            self.pending_recovery.retire()
            self.pending_recovery = None
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
        self.reasoning_group = None
        self.answer_preview = None
        self.busy = True
        self.update_session_header()
        self.generation_started = time.monotonic()
        self.cancelled.clear()
        self.response_text = self.rendered_text = ""
        self.reply = None
        self.speaker = None
        prompt = self.query_one(Prompt)
        if not preserve_draft:
            prompt.value = ""
        prompt.disabled = False
        self.update_completions()
        self.mount_message(Static(f"› {safe_preview(question)}", classes="question", markup=False))
        self.set_generation_status("Investigando…")
        self.investigate(question)

    def deliver(self, callback, *args):
        if self.is_running:
            try:
                self.call_from_thread(self.deliver_if_mounted, callback, *args)
            except RuntimeError:
                return

    def deliver_if_mounted(self, callback, *args):
        if self.is_running and self.query("#status"):
            callback(*args)

    @work(thread=True, exclusive=True)
    def investigate(self, question: str):
        successful = False
        outcome = "error"
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
            outcome = "cancelled"
        except ContextCapacityError as exc:
            answer = str(exc)
            outcome = "context"
        except (ModelError, ValueError, OSError) as exc:
            message = str(exc)
            if self.agent.provider.settings.provider_id:
                message = message.replace(
                    "Confira CODARO_BASE_URL", "Confira a URL em Ctrl+P → Gerenciar provedores"
                )
            answer = f"Não foi possível concluir: {message}"
            if isinstance(exc, ModelError):
                outcome = failure_kind(exc)
        except sqlite3.Error:
            answer = (
                "Falha no índice SQLite. Confira permissões, espaço livre e integridade do índice."
            )
        except Exception:
            logger.exception("Unexpected investigation failure")
            answer = "Falha inesperada. Execute codaro ask para diagnosticar o fluxo."
        self.deliver(self.finish, answer, successful, outcome)

    def activity(self, event: AgentEvent):
        return stream_ui.activity(self, event)

    def show_plan(self):
        task = self.agent.tasks.current()
        self.query_one(ExecutionPlan).set_task(task, busy=self.busy)
        self.query_one(ExecutionPlan).fit(self.size.height)
        self.call_after_refresh(self.update_completions)

    def cancel_execution_plan(self):
        task = self.agent.tasks.current()
        if not task or task["state"] in {"completed", "cancelled"}:
            return
        if self.busy:
            self.cancel_plan_id = task["id"]
            self.action_cancel()
            self.query_one("#status", Static).update("Cancelando plano · preservando alterações…")
        else:
            self.agent.tasks.state("cancelled", "Plano cancelado pelo usuário.")
            self.agent.policy.reset()
            self.show_plan()
            self.update_session_header()
            self.query_one("#status", Static).update("Plano cancelado · alterações preservadas")

    def append_delta(self, delta: str):
        return stream_ui.append_delta(self, delta)

    def append_reasoning(self, delta: str):
        return stream_ui.append_reasoning(self, delta)

    def flush_preview(self):
        return stream_ui.flush_preview(self)

    def finish_preview(self, state: str, reason: str = ""):
        return stream_ui.finish_preview(self, state, reason)

    def flush_response(self):
        return stream_ui.flush_response(self)

    def discard_response(self):
        return stream_ui.discard_response(self)

    def finish(self, answer: str, successful: bool = True, outcome: str = "provider"):
        if self.cancel_plan_id:
            task = self.agent.tasks.current()
            if task and task["id"] == self.cancel_plan_id:
                self.agent.tasks.state("cancelled", "Plano cancelado pelo usuário.")
                self.agent.policy.reset()
            self.cancel_plan_id = None
        self.finish_preview("answer" if successful else "cancelled")
        if self.activity_group is not None:
            self.activity_group.finish(successful)
        if successful:
            if self.retry_recovery is not None and self.retry_recovery.is_attached:
                self.retry_recovery.resolve()
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
            self.pending_recovery = RecoveryCard(
                safe_preview(
                    f"{settings.provider_id or 'Local/ambiente'} / {settings.model}\n" + answer
                ),
                outcome,
            )
            self.mount_message(self.pending_recovery)
        if successful:
            for proposal in self.agent.edits.pending:
                card = ProposalCard(proposal)
                self.proposal_cards[proposal.id] = card
                self.mount_message(card)
        self.busy = False
        self.show_plan()
        self.retry_recovery = None
        self.generation_started = 0.0
        self.update_session_header()
        prompt = self.query_one(Prompt)
        prompt.disabled = False
        prompt.focus()
        self.query_one("#status", Static).update(
            "Aguardando revisão de edições" if self.agent.edits.pending else "Pronto"
        )

        if not self.agent.legacy and self.agent.last_run_intent != "consultation":
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
        self.activity_group = None
        self.reasoning_group = None
        self.pending_recovery = self.retry_recovery = None
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
        self.update_session_header()
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
        self.show_plan()

    async def action_new_conversation(self):
        if self.busy or isinstance(self.screen, ModalScreen):
            return
        from codaro.ui.dialogs import NewConversation

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
            previous = self.agent.tasks.current()
            if previous and previous["state"] not in {"completed", "cancelled"}:
                self.agent.tasks.state("cancelled", "Encerrado ao começar outra conversa.")
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
        if event.button.id == "select-mode":
            self.open_mode_picker()
            return
        if event.button.id == "toggle-plan":
            self.query_one(ExecutionPlan).toggle()
            self.show_plan()
            return
        if event.button.id == "cancel-plan":
            self.cancel_execution_plan()
            return
        if event.button.id == "new-messages":
            self.query_one("#conversation", VerticalScroll).scroll_end(animate=False)
            event.button.display = False
            self.update_completions()
            return
        if event.button.id == "retry-question" and not self.busy:
            self.run_worker(self.submit_question(self.active_question, preserve_draft=True))
            return
        if (
            event.button.id in {"recover-provider", "recover-model", "configure-start"}
            and not self.busy
        ):
            command = "/models" if event.button.id == "recover-model" else "/providers"
            if event.button.id == "recover-provider" and self.agent.provider.settings.provider_id:
                command = "/provider-manage"
            self.run_worker(self.open_provider_menu(command))
            return
        if event.button.id == "execute-plan":
            if self.busy:
                return
            self.agent.set_mode(Mode.EXECUTE)
            self.update_session_header()
            self.query_one(Prompt).value = "Execute o plano da tarefa ativa e valide o resultado."
            self.query_one(Prompt).focus()
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
            self.set_generation_status("Cancelamento solicitado · aguardando resposta ativa…")

    def on_unmount(self):
        self.cancelled.set()
