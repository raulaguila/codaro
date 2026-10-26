"""Local provider registration and model selection, outside the model conversation."""

from __future__ import annotations

import asyncio
import threading

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Collapsible, Input, Select, Static

from codaro.llm import ModelError, RequestCancelled, Settings
from codaro.llm.endpoints import PRESETS, provider_base_url
from codaro.llm.profiles import ProviderStore
from codaro.ui.model_list import ModelList
from codaro.ui.screen import DialogScreen as ModalScreen
from codaro.ui.session_details import compact_tokens

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "groq": "Groq",
    "ollama": "Ollama",
    "openai-compatible": "OpenAI Compatible",
    "custom": "Personalizado",
}


class ProviderSetup(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ProviderSetup { align: center middle; background: #000000 65%; }
    #provider-form { width: 90%; max-width: 76; height: 28; max-height: 90%; padding: 0 2;
        background: $surface; border: round $primary 35%; }
    #provider-fields { height: 1fr; }
    #provider-fields Input, #provider-fields Select { margin-bottom: 0; }
    #provider-form Static { height: auto; }
    #provider-fields Checkbox { width: 100%; height: auto; }
    #provider-actions { height: 2; border-top: solid $primary 30%; }
    #provider-form Button { width: 1fr; min-width: 8; padding: 0; height: 1;
        min-height: 1; border: none; background: $surface; color: $text-muted; text-style: none; }
    #provider-actions #provider-save { background: $accent; color: $background; text-style: bold; }
    #provider-actions #provider-save:hover { background: $accent-lighten-1; color: $background; }
    #provider-error { max-height: 3; }
    #provider-advanced { height: auto; border: none; padding: 0; }
    #provider-advanced > Contents { padding: 0; }
    #provider-advanced CollapsibleTitle { color: $text-muted; }
    """
    BINDINGS = [Binding("escape", "back", "Voltar")]

    def __init__(self, store=None, *, profile=None, onboarding=False):
        super().__init__()
        self.store = store or ProviderStore()
        self.profile_name = profile
        self.original = self.store.profile(profile)[1] if profile else None
        self.saving = False
        self.testing = False
        self.test_worker = None
        self.generation = 0
        self.cancel_event = threading.Event()
        self.last_kind = self.original["kind"] if self.original else "openai"
        self.onboarding = onboarding

    def compose(self) -> ComposeResult:
        profile = self.original or {}
        with Vertical(id="provider-form"):
            yield Static(
                "Conectar provedor"
                if self.onboarding
                else "Editar provedor"
                if profile
                else "Conectar provedor",
                classes="dialog-title",
                markup=False,
            )
            with VerticalScroll(id="provider-fields"):
                yield Static("Provedor", markup=False)
                yield Select(
                    [(PROVIDER_LABELS[kind], kind) for kind in PRESETS],
                    value=self.last_kind,
                    allow_blank=False,
                    id="provider-kind",
                    compact=True,
                )
                with Vertical(id="provider-key-field"):
                    yield Static(
                        "Nova API key · vazio mantém a atual" if profile else "API key",
                        markup=False,
                    )
                    yield Input(
                        placeholder="Cole sua API key…",
                        password=True,
                        id="provider-key",
                        compact=True,
                    )
                with Vertical(id="provider-url-field"):
                    yield Static("URL do provedor", markup=False)
                    yield Input(
                        profile.get("base_url", PRESETS[self.last_kind]),
                        id="provider-url",
                        compact=True,
                    )
                with Collapsible(
                    title="Avançado",
                    collapsed=True,
                    id="provider-advanced",
                ):
                    yield Static("Nome do perfil (opcional)", markup=False)
                    yield Input(self.profile_name or "", id="provider-name", compact=True)
                    yield Checkbox(
                        "Usar autenticação no Ollama",
                        bool(profile.get("api_key")),
                        id="provider-auth",
                        compact=True,
                    )
                    yield Checkbox(
                        "TLS Insecure · não verificar certificado",
                        profile.get("tls_insecure", False),
                        id="provider-tls",
                        compact=True,
                    )
                    yield Static(
                        "A identidade do servidor não será verificada.",
                        id="provider-tls-warning",
                        markup=False,
                    )
                    yield Static("Modelo para testar (opcional)", markup=False)
                    yield Select(
                        [],
                        id="provider-test-model",
                        prompt="Selecione após testar o catálogo",
                        compact=True,
                    )
                    yield Static(
                        "Teste completo: duas chamadas curtas. Pode consumir créditos.",
                        markup=False,
                    )
                    yield Button("Testar ferramentas e resposta", id="provider-test-inference")
                yield Static(
                    "A credencial fica fora da conversa e do projeto.",
                    markup=False,
                )
            yield Static(
                "Teste a conexão para conferir os modelos disponíveis.",
                id="provider-error",
                markup=False,
            )
            with Horizontal(id="provider-actions"):
                yield Button("Testar conexão", id="provider-test")
                yield Button("Cancelar", id="provider-back")
                yield Button(
                    "Salvar e escolher",
                    id="provider-save",
                    variant="primary",
                )

    def on_mount(self):
        self.update_fields()
        self.fit_actions()
        self.query_one("#provider-kind").focus()

    def on_resize(self):
        self.app.call_after_refresh(self.app.sync_layout)
        if self.query("#provider-save"):
            self.call_after_refresh(self.fit_actions)

    def fit_actions(self):
        button = self.query_one("#provider-save", Button)
        button.label = "Salvar e escolher modelo" if self.size.width >= 70 else "Salvar e escolher"
        button.styles.width = 26 if self.size.width >= 70 else 19
        button.tooltip = "Salvar o provedor e escolher um modelo para conversar"

    def update_fields(self):
        kind = str(self.query_one("#provider-kind", Select).value)
        advanced = self.query_one("#provider-advanced", Collapsible)
        self.query_one("#provider-url-field").display = (
            kind in {"custom", "openai-compatible", "ollama"} or not advanced.collapsed
        )
        self.query_one("#provider-key-field").display = (
            kind != "ollama" or self.query_one("#provider-auth", Checkbox).value
        )
        self.query_one("#provider-auth").display = kind == "ollama"
        self.query_one("#provider-tls-warning").display = self.query_one(
            "#provider-tls", Checkbox
        ).value
        self.query_one("#provider-test-inference", Button).disabled = self.saving or not isinstance(
            self.query_one("#provider-test-model", Select).value, str
        )
        ready = True
        try:
            Settings(
                provider_base_url(kind, self.query_one("#provider-url", Input).value),
                "unselected",
                self.form_key(),
            )
        except ValueError:
            ready = False
        if kind not in {"ollama", "custom", "openai-compatible"}:
            ready = ready and bool(self.form_key())
        if kind == "ollama" and self.query_one("#provider-auth", Checkbox).value:
            ready = ready and bool(self.form_key())
        for identifier in ("#provider-test", "#provider-save"):
            self.query_one(identifier, Button).disabled = self.saving or not ready
        if not ready:
            self.query_one("#provider-test-inference", Button).disabled = True
        self.query_one("#provider-form").styles.height = (
            30
            if not advanced.collapsed
            else 16
            + 3 * int(self.query_one("#provider-url-field").display)
            + 2 * int(self.query_one("#provider-key-field").display)
        )

    def on_collapsible_expanded(self):
        self.update_fields()

    def on_collapsible_collapsed(self):
        self.update_fields()

    def on_checkbox_changed(self):
        self.update_fields()

    def on_input_changed(self):
        self.update_fields()

    def on_unmount(self):
        self.cancel_event.set()
        self.generation += 1

    def form_key(self):
        if self.last_kind == "ollama" and not self.query_one("#provider-auth", Checkbox).value:
            return ""
        return self.query_one("#provider-key", Input).value.strip() or (self.original or {}).get(
            "api_key", ""
        )

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "provider-kind":
            kind = event.value
            if isinstance(kind, str) and kind != self.last_kind:
                self.last_kind = kind
                self.query_one("#provider-url", Input).value = PRESETS[kind]
                self.query_one("#provider-test-model", Select).set_options([])
                self.update_fields()
        elif event.select.id == "provider-test-model":
            self.update_fields()

    async def test_connection(self, *, inference=False):
        self.saving = True
        self.testing = True
        self.generation += 1
        generation = self.generation
        self.cancel_event = threading.Event()
        focus = self.focused
        for control in self.query("Input, Select, Checkbox, Button"):
            control.disabled = True
        self.query_one("#provider-back", Button).disabled = False
        self.query_one("#provider-back", Button).label = "Cancelar"
        status = self.query_one("#provider-error", Static)
        status.update("Testando conexão…")
        model = self.query_one("#provider-test-model", Select).value if inference else None
        try:
            models = await asyncio.to_thread(
                self.store.test_connection,
                str(self.query_one("#provider-kind", Select).value),
                self.form_key(),
                base_url=self.query_one("#provider-url", Input).value.strip(),
                tls_insecure=self.query_one("#provider-tls", Checkbox).value,
                model_id=model if isinstance(model, str) else None,
                cancelled=self.cancel_event,
                on_stage=lambda stage: self.stage(generation, stage),
            )
            if not self.is_attached or generation != self.generation:
                return
            self.query_one("#provider-test-model", Select).set_options(
                [(item["id"], item["id"]) for item in models if item["tools"] is not False]
            )
            if isinstance(model, str):
                self.query_one("#provider-test-model", Select).value = model
            status.update(
                "✓ Ferramentas e resposta verificadas. Nenhum perfil salvo."
                if isinstance(model, str)
                else f"✓ Conexão válida · {len(models)} modelos encontrados. "
                "Geração ainda não testada."
            )
            status.set_class(False, "error")
            status.set_class(True, "success")
        except (ValueError, OSError, ModelError) as exc:
            if generation == self.generation and self.is_attached:
                status.update(str(exc))
                status.set_class(True, "error")
                status.set_class(False, "success")
                if isinstance(exc, ValueError) and "API key" in str(exc):
                    focus = self.query_one("#provider-key")
                elif isinstance(exc, ValueError) and "URL" in str(exc):
                    focus = self.query_one("#provider-url")
        except (asyncio.CancelledError, RequestCancelled):
            return
        finally:
            if generation == self.generation and self.is_attached and self.query("#provider-back"):
                self.restore_controls(focus)

    def stage(self, generation, stage):
        def update():
            if self.is_attached and generation == self.generation:
                self.query_one("#provider-error", Static).update("Testando: " + stage)

        try:
            self.app.call_from_thread(update)
        except RuntimeError:
            pass

    def restore_controls(self, focus=None):
        self.saving = self.testing = False
        for control in self.query("Input, Select, Checkbox, Button"):
            control.disabled = False
        self.query_one("#provider-back", Button).label = "Voltar"
        self.update_fields()
        (
            focus if focus is not None and focus.is_attached else self.query_one("#provider-test")
        ).focus()

    def action_back(self):
        if self.saving:
            testing = self.testing
            self.cancel_event.set()
            self.generation += 1
            if self.test_worker:
                self.test_worker.cancel()
            self.restore_controls()
            self.query_one("#provider-error", Static).update(
                "Teste cancelado · resultados tardios serão ignorados."
                if testing
                else "Consulta cancelada · nenhum perfil alterado."
            )
            return
        if not self.saving:
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id in {"provider-test", "provider-test-inference"} and not self.saving:
            self.saving = True
            self.test_worker = self.run_worker(
                self.test_connection(inference=event.button.id == "provider-test-inference")
            )
            return
        if event.button.id == "provider-back":
            self.action_back()
        elif event.button.id == "provider-save" and not self.saving:
            self.saving = True
            self.test_worker = self.run_worker(self.save_profile())

    async def save_profile(self):
        self.saving = True
        self.generation += 1
        generation = self.generation
        self.cancel_event = threading.Event()
        focus = self.focused
        for control in self.query("Input, Select, Checkbox, Button"):
            control.disabled = True
        back = self.query_one("#provider-back", Button)
        back.disabled, back.label = False, "Cancelar"
        self.query_one("#provider-error", Static).update("Consultando catálogo…")
        try:
            kind = str(self.query_one("#provider-kind", Select).value)
            name = (
                self.query_one("#provider-name", Input).value.strip() or self.profile_name or kind
            )
            change = await asyncio.to_thread(
                self.store.prepare_update if self.original else self.store.prepare_register,
                kind,
                self.form_key(),
                name=name,
                base_url=self.query_one("#provider-url", Input).value.strip(),
                tls_insecure=self.query_one("#provider-tls", Checkbox).value,
                cancelled=self.cancel_event,
                **({"previous_name": self.profile_name} if self.original else {}),
            )
            if not self.is_attached or generation != self.generation:
                return
            # No await between the last cancellation check and the atomic local commit.
            change.commit()
            self.query_one("#provider-key", Input).value = ""
            self.dismiss(name)
        except (asyncio.CancelledError, RequestCancelled):
            return
        except (ValueError, OSError, ModelError) as exc:
            if self.is_attached and generation == self.generation:
                self.query_one("#provider-error", Static).update(str(exc))
        finally:
            if self.is_attached and generation == self.generation and self.query("#provider-back"):
                self.restore_controls(focus)


class ModelPicker(ModalScreen[Settings | None]):
    DEFAULT_CSS = """
    ModelPicker { align: center middle; background: #000000 65%; }
    #model-form { width: 90%; max-width: 86; height: 25; max-height: 90%; padding: 0 2;
        background: $surface; border: round $primary 35%; }
    #model-fields { height: 1fr; }
    #model-form Select { margin-bottom: 0; }
    #model-form Static { height: auto; }
    #model-id { height: 6; border: none; padding: 0; background: $surface; }
    #model-id > .option-list--option-highlighted { background: $primary 15%;
        color: $text; text-style: none; }
    #model-actions { height: 2; border-top: solid $primary 30%; }
    #model-actions Button { width: 1fr; min-width: 8; padding: 0; height: 1;
        min-height: 1; border: none; background: $surface; color: $text-muted; text-style: none; }
    #model-actions #model-use { background: $accent; color: $background; text-style: bold; }
    #model-actions #model-use:hover { background: $accent-lighten-1; color: $background; }
    #model-info { max-height: 4; }
    """
    BINDINGS = [Binding("escape", "back", "Voltar")]

    def __init__(self, store=None, *, profile=None, onboarding=False):
        super().__init__()
        self.store, self.initial_profile = store or ProviderStore(), profile
        self.profiles = self.store.load()
        if not self.profiles["profiles"]:
            raise ValueError("Cadastre um provedor antes de selecionar modelos.")
        self.fetching = False
        self.generation = 0
        self.cancel_event = threading.Event()
        self.operation_worker = None
        self.onboarding = onboarding

    def compose(self) -> ComposeResult:
        value = self.profiles
        selected = self.initial_profile or value["active"] or next(iter(value["profiles"]), None)
        with Vertical(id="model-form"):
            yield Static(
                "Primeiro acesso · Escolha o modelo" if self.onboarding else "Escolher modelo",
                classes="dialog-title",
                markup=False,
            )
            with VerticalScroll(id="model-fields"):
                yield Static("Provedor", markup=False)
                yield Select(
                    [(name, name) for name in value["profiles"]],
                    value=selected or Select.BLANK,
                    id="model-provider",
                    allow_blank=False,
                    compact=True,
                )
                yield Input(placeholder="Buscar modelo…", id="model-search", compact=True)
                yield Static("", id="model-list-state", markup=False)
                yield ModelList(id="model-id")
                yield Static("", id="model-info", markup=False)
                with Collapsible(title="Detalhes do modelo", collapsed=True, id="model-details"):
                    yield Static("", id="model-metadata", markup=False)
                    yield Static("Janela confirmada em tokens (opcional)", markup=False)
                    yield Input(
                        placeholder="Vazio mantém a configuração atual",
                        id="model-context-override",
                        compact=True,
                    )
            yield Static(
                "Teste: 2 chamadas · pode consumir créditos.",
                id="model-test-warning",
                classes="muted",
                markup=False,
            )
            yield Static("", id="model-error", markup=False)
            with Horizontal(id="model-actions"):
                yield Button(
                    "Começar" if self.onboarding else "Usar modelo",
                    id="model-use",
                    variant="primary",
                )
                yield Button("Testar modelo", id="model-test")
                yield Button("Atualizar", id="model-refresh")
                yield Button("Voltar", id="model-back")

    def populate(self, name):
        models = self.store.models(name)
        self.catalogue = models
        self.query_one(ModelList).set_models(models, self.query_one("#model-search", Input).value)
        self.query_one("#model-error", Static).update("")
        profile = self.store.profile(name)[1]
        if any(model["id"] == profile["model"] and model["tools"] is not False for model in models):
            if not self.query_one("#model-search", Input).value:
                self.query_one(ModelList).value = profile["model"]
        self.update_model(self.query_one(ModelList).value)

    def on_input_changed(self, event: Input.Changed):
        if event.input.id == "model-search":
            self.query_one(ModelList).set_models(getattr(self, "catalogue", []), event.value)
            self.update_model(self.query_one(ModelList).value)

    def on_model_list_changed(self, event: ModelList.Changed):
        try:
            # Resize/search can enqueue old highlights. Read the current selection.
            self.update_model(self.query_one(ModelList).value)
        except (ValueError, OSError) as exc:
            self.query_one("#model-error", Static).update(str(exc))

    def on_model_list_chosen(self):
        self.on_button_pressed(Button.Pressed(self.query_one("#model-use", Button)))

    def on_select_changed(self, event: Select.Changed):
        try:
            self.update_selection(event)
        except (ValueError, OSError) as exc:
            self.query_one("#model-error", Static).update(str(exc))

    def update_selection(self, event: Select.Changed):
        if not isinstance(event.value, str):
            return
        if event.select.id == "model-provider":
            self.populate(event.value)

    def update_model(self, value):
        name = str(self.query_one("#model-provider", Select).value)
        model = next((item for item in self.store.models(name) if item["id"] == value), None)
        info = self.query_one("#model-info", Static)
        self.query_one("#model-use", Button).disabled = self.fetching or model is None
        self.query_one("#model-test", Button).disabled = self.fetching or model is None
        empty = not self.query_one(ModelList).option_count
        self.query_one(ModelList).display = not empty and not self.fetching
        state = self.query_one("#model-list-state", Static)
        state.display = empty or self.fetching
        state.update(
            "◌ Atualizando catálogo… · seleção preservada"
            if self.fetching
            else "Nenhum modelo encontrado. Ajuste a busca ou atualize o catálogo."
        )
        if not model:
            info.update("Nenhum modelo disponível para conversa." if not empty else "")
            self.query_one("#model-metadata", Static).update("")
            return
        settings = self.store.settings_for(name, self.store.profile(name)[1], model)
        selection = (name, value)
        if getattr(self, "details_model", None) != selection:
            self.details_model = selection
            self.query_one("#model-context-override", Input).value = ""
        capability = (
            "Ferramentas confirmadas pela API"
            if model["tools"] is True
            else "Ferramentas: suporte não informado"
        )
        provisional = settings.context_source.startswith("fallback")
        info.update(
            "Contexto não informado pela API · orçamento provisório: "
            f"{compact_tokens(settings.context_window)}"
            if provisional
            else f"Contexto: {compact_tokens(settings.context_window)} · {capability}"
        )
        self.query_one("#model-metadata", Static).update(
            f"Modelo: {model['id']}\nOrigem: {settings.context_source}\n{capability}\n"
            f"Saída máxima informada: {model['max_output_tokens'] or 'Não informada'}\n"
            "— na lista: contexto não informado pelo catálogo."
        )
        info.tooltip = (
            settings.context_source + f" · Saída: {model['max_output_tokens'] or 'não informada'}"
        )

    def action_back(self):
        if self.fetching:
            self.cancel_event.set()
            self.generation += 1
            if self.operation_worker:
                self.operation_worker.cancel()
            self.restore_controls()
            self.query_one("#model-error", Static).update(
                "Consulta cancelada · seleção preservada."
            )
            return
        self.dismiss(None)

    def on_unmount(self):
        self.cancel_event.set()
        self.generation += 1

    def restore_controls(self, focus=None):
        self.fetching = False
        for control in self.query("Select, ModelList, Input, Button"):
            control.disabled = False
        self.query_one("#model-back", Button).label = "Voltar"
        self.query_one("#model-use", Button).disabled = not bool(self.query_one(ModelList).value)
        self.update_model(self.query_one(ModelList).value)
        (focus or self.query_one("#model-refresh")).focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "model-back":
            self.action_back()
            return
        if self.fetching or event.button.id not in {"model-use", "model-refresh", "model-test"}:
            return
        self.fetching = True
        self.operation_worker = self.run_worker(self.lookup(event.button))

    async def lookup(self, button):
        name = self.query_one("#model-provider", Select).value
        model = self.query_one(ModelList).value
        if not isinstance(name, str):
            self.fetching = False
            return
        self.fetching = True
        self.generation += 1
        generation = self.generation
        self.cancel_event = threading.Event()
        for control in self.query("Select, ModelList, Input, Button"):
            control.disabled = True
        back = self.query_one("#model-back", Button)
        back.disabled, back.label = False, "Cancelar consulta"
        self.query_one("#model-error", Static).update("Consultando catálogo…")
        self.update_model(model)
        try:
            refresh = button.id == "model-refresh"
            if not refresh and not isinstance(model, str):
                self.query_one("#model-error", Static).update("Selecione um modelo.")
                return
            if button.id == "model-test":
                profile = self.store.profile(str(name))[1]
                await asyncio.to_thread(
                    self.store.test_connection,
                    profile["kind"],
                    profile["api_key"],
                    base_url=profile["base_url"],
                    tls_insecure=profile["tls_insecure"],
                    model_id=model,
                    cancelled=self.cancel_event,
                )
                if (
                    self.is_attached
                    and generation == self.generation
                    and self.query("#model-error")
                ):
                    self.query_one("#model-error", Static).update(
                        "✓ Chamada, resultado da ferramenta e resposta final verificados."
                    )
                return
            change = await asyncio.to_thread(
                self.store.prepare_refresh if refresh else self.store.prepare_select,
                *([name] if refresh else [name, model]),
                cancelled=self.cancel_event,
                **({} if refresh else self.context_override()),
            )
            if not self.is_attached or generation != self.generation:
                return
            result = change.commit()
            if refresh:
                self.populate(name)
                self.query_one("#model-error", Static).update("Catálogo atualizado pela API.")
            else:
                self.dismiss(result)
        except (asyncio.CancelledError, RequestCancelled):
            return
        except (ValueError, OSError, ModelError) as exc:
            if self.is_attached and generation == self.generation:
                self.query_one("#model-error", Static).update(str(exc))
        finally:
            if self.is_attached and generation == self.generation and self.query("#model-back"):
                self.restore_controls(button)

    def context_override(self):
        raw = self.query_one("#model-context-override", Input).value.strip()
        if not raw:
            return {}
        if not raw.isascii() or not raw.isdigit() or not 4096 <= int(raw) <= 2_000_000:
            raise ValueError("Informe uma janela confirmada entre 4.096 e 2.000.000 tokens.")
        return {"context_window": int(raw)}
