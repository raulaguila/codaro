"""Local provider registration and model selection, outside the model conversation."""

from __future__ import annotations

import asyncio
import threading

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Collapsible, Input, Select, Static

from codaro.llm import ModelError, RequestCancelled, Settings
from codaro.llm.endpoints import PRESETS
from codaro.llm.profiles import ProviderStore


class ProviderSetup(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ProviderSetup { align: center middle; background: #000000 65%; }
    #provider-form { width: 95%; max-width: 90; height: 36; max-height: 90%; padding: 0 1;
        background: $surface; border: round $accent; }
    #provider-fields { height: 1fr; }
    #provider-fields Input, #provider-fields Select { margin-bottom: 0; }
    #provider-form Static { height: auto; }
    #provider-fields Checkbox { width: 100%; height: auto; }
    #provider-actions, .provider-test-actions { height: 3; }
    #provider-form Button { width: 1fr; min-width: 8; padding: 0; }
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
                "Primeiro acesso · Configure um provedor"
                if self.onboarding
                else "Editar provedor"
                if profile
                else "Cadastrar provedor · BYOK",
                markup=False,
            )
            with VerticalScroll(id="provider-fields"):
                yield Static("Provedor", markup=False)
                yield Select(
                    [(kind, kind) for kind in PRESETS],
                    value=self.last_kind,
                    allow_blank=False,
                    id="provider-kind",
                )
                yield Static(
                    "Nova API key · vazio mantém a atual"
                    if profile
                    else "API key · opcional para Ollama local",
                    markup=False,
                )
                yield Input(password=True, id="provider-key")
                yield Static("Nome do perfil (opcional)", markup=False)
                yield Input(self.profile_name or "", id="provider-name")
                with Collapsible(
                    title="Conexão e teste de ferramentas",
                    collapsed=not bool(profile),
                    id="provider-advanced",
                ):
                    yield Static("URL base da API", markup=False)
                    yield Input(profile.get("base_url", PRESETS["openai"]), id="provider-url")
                    yield Checkbox(
                        "TLS Insecure · não verificar certificado",
                        profile.get("tls_insecure", False),
                        id="provider-tls",
                    )
                    yield Static("Modelo para testar (opcional)", markup=False)
                    yield Select(
                        [], id="provider-test-model", prompt="Selecione após testar o catálogo"
                    )
                    yield Static(
                        "Sem modelo: catálogo. Com modelo: ferramentas e resposta "
                        "em duas chamadas curtas (pode haver custo).",
                        markup=False,
                    )
                yield Static(
                    "Credencial privada, fora do projeto. Role ou use Tab para outros campos.",
                    markup=False,
                )
            yield Static("", id="provider-error", markup=False)
            with Horizontal(id="provider-actions"):
                yield Button(
                    "Testar e continuar"
                    if self.onboarding
                    else "Salvar e listar"
                    if profile
                    else "Cadastrar e listar",
                    id="provider-save",
                    variant="primary",
                )
            with Horizontal(classes="provider-test-actions"):
                yield Button("Testar conexão", id="provider-test")
                yield Button("Voltar", id="provider-back")

    def on_unmount(self):
        self.cancel_event.set()
        self.generation += 1

    def form_key(self):
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
                self.query_one("#provider-advanced", Collapsible).collapsed = kind not in {
                    "custom",
                    "openai-compatible",
                    "ollama",
                }

    async def test_connection(self):
        self.saving = True
        self.testing = True
        self.generation += 1
        generation = self.generation
        self.cancel_event = threading.Event()
        focus = self.focused
        for control in self.query("Input, Select, Checkbox, Button"):
            control.disabled = True
        self.query_one("#provider-back", Button).disabled = False
        self.query_one("#provider-back", Button).label = "Cancelar teste"
        status = self.query_one("#provider-error", Static)
        status.update("Testando conexão…")
        model = self.query_one("#provider-test-model", Select).value
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
            self.query_one("#provider-advanced", Collapsible).collapsed = False
            if isinstance(model, str):
                self.query_one("#provider-test-model", Select).value = model
            status.update(
                "Conexão, ferramentas e resposta do modelo verificadas. Nenhum perfil salvo."
                if isinstance(model, str)
                else f"Catálogo acessível: {len(models)} modelos. Selecione um modelo e teste "
                "novamente para verificar ferramentas e resposta. Nenhum perfil salvo."
            )
        except (ValueError, OSError, ModelError) as exc:
            if generation == self.generation and self.is_attached:
                status.update(str(exc))
                if isinstance(exc, ValueError) and "API key" in str(exc):
                    focus = self.query_one("#provider-key")
                elif isinstance(exc, ValueError) and "URL" in str(exc):
                    focus = self.query_one("#provider-url")
        except (asyncio.CancelledError, RequestCancelled):
            return
        finally:
            if generation == self.generation and self.is_attached:
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
        if event.button.id == "provider-test" and not self.saving:
            self.saving = True
            self.test_worker = self.run_worker(self.test_connection())
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
        back.disabled, back.label = False, "Cancelar consulta"
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
            if self.is_attached and generation == self.generation:
                self.restore_controls(focus)


class ModelPicker(ModalScreen[Settings | None]):
    DEFAULT_CSS = """
    ModelPicker { align: center middle; background: #000000 65%; }
    #model-form { width: 95%; max-width: 105; height: 20; max-height: 90%; padding: 0 1;
        background: $surface; border: round $accent; }
    #model-fields { height: 1fr; }
    #model-form Select { margin-bottom: 0; }
    #model-form Static { height: auto; }
    #model-actions { height: 3; }
    #model-actions Button { width: 1fr; min-width: 8; padding: 0; }
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
                "Primeiro acesso · Escolha o modelo"
                if self.onboarding
                else "Selecionar provedor e modelo · catálogo da API",
                markup=False,
            )
            with VerticalScroll(id="model-fields"):
                yield Static("Provedor", markup=False)
                yield Select(
                    [(name, name) for name in value["profiles"]],
                    value=selected or Select.BLANK,
                    id="model-provider",
                    allow_blank=False,
                )
                yield Static("Modelo · digite na lista para buscar", markup=False)
                yield Select([], id="model-id", prompt="Modelo")
                yield Static("", id="model-info", markup=False)
            yield Static("", id="model-error", markup=False)
            with Horizontal(id="model-actions"):
                yield Button(
                    "Começar" if self.onboarding else "Usar modelo",
                    id="model-use",
                    variant="primary",
                )
                yield Button("Atualizar API", id="model-refresh")
                yield Button("Voltar", id="model-back")

    def populate(self, name):
        models = self.store.models(name)
        compatible = [model for model in models if model["tools"] is not False]
        self.query_one("#model-id", Select).set_options(
            [
                (f"{model['id']} · {model['context_window'] or '?'} tokens", model["id"])
                for model in compatible
            ]
        )
        self.query_one("#model-error", Static).update(
            f"{len(models) - len(compatible)} modelos sem ferramentas/chat ocultos."
            if len(models) != len(compatible)
            else ""
        )
        profile = self.store.profile(name)[1]
        if any(model["id"] == profile["model"] for model in compatible):
            self.query_one("#model-id", Select).value = profile["model"]

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
        elif event.select.id == "model-id":
            name = str(self.query_one("#model-provider", Select).value)
            model = next(
                (item for item in self.store.models(name) if item["id"] == event.value), None
            )
            if model:
                profile = self.store.profile(name)[1]
                settings = self.store.settings_for(name, profile, model)
                self.query_one("#model-info", Static).update(
                    f"{model['name']}\nContexto usado: {settings.context_window} · "
                    f"{settings.context_source}\n"
                    f"Saída máxima: {model['max_output_tokens'] or 'não informada'} · "
                    "Ferramentas: "
                    + (
                        "confirmadas pela API\n"
                        if model["tools"] is True
                        else "não informado; use Testar conexão\n"
                    )
                    + "Limites aprendidos após rejeição ajustam o orçamento automaticamente."
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
        for control in self.query("Select, Button"):
            control.disabled = False
        self.query_one("#model-back", Button).label = "Voltar"
        (focus or self.query_one("#model-refresh")).focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "model-back":
            self.action_back()
            return
        if self.fetching or event.button.id not in {"model-use", "model-refresh"}:
            return
        self.fetching = True
        self.operation_worker = self.run_worker(self.lookup(event.button))

    async def lookup(self, button):
        name = self.query_one("#model-provider", Select).value
        model = self.query_one("#model-id", Select).value
        if not isinstance(name, str):
            self.fetching = False
            return
        self.fetching = True
        self.generation += 1
        generation = self.generation
        self.cancel_event = threading.Event()
        for control in self.query("Select, Button"):
            control.disabled = True
        back = self.query_one("#model-back", Button)
        back.disabled, back.label = False, "Cancelar consulta"
        self.query_one("#model-error", Static).update("Consultando catálogo…")
        try:
            refresh = button.id == "model-refresh"
            if not refresh and not isinstance(model, str):
                self.query_one("#model-error", Static).update("Selecione um modelo.")
                return
            change = await asyncio.to_thread(
                self.store.prepare_refresh if refresh else self.store.prepare_select,
                *([name] if refresh else [name, model]),
                cancelled=self.cancel_event,
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
            if self.is_attached and generation == self.generation:
                self.restore_controls(button)
