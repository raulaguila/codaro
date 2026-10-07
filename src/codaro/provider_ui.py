"""Local provider registration and model selection, outside the model conversation."""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static

from codaro.provider import ModelError, Settings
from codaro.providers import PRESETS, ProviderStore


class ProviderSetup(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ProviderSetup { align: center middle; background: #000000 65%; }
    #provider-form { width: 85%; max-width: 90; height: auto; max-height: 90%;
        overflow-y: auto; padding: 1 2;
        background: #111827; border: round #38bdf8; }
    #provider-form Input, #provider-form Select { margin-bottom: 1; }
    #provider-form Static { height: auto; }
    #provider-actions, .provider-test-actions { height: auto; }
    """
    BINDINGS = [Binding("escape", "back", "Voltar")]

    def __init__(self, store=None):
        super().__init__()
        self.store = store or ProviderStore()
        self.saving = False

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-form"):
            yield Static("Cadastrar provedor · BYOK", markup=False)
            yield Select(
                [(kind, kind) for kind in PRESETS if kind != "custom"],
                value="openai",
                allow_blank=False,
                id="provider-kind",
            )
            yield Input(placeholder="Nome do perfil (opcional)", id="provider-name")
            yield Input(PRESETS["openai"], placeholder="URL base da API", id="provider-url")
            yield Input(
                placeholder="API key (não necessária para Ollama local)",
                password=True,
                id="provider-key",
            )
            yield Checkbox("TLS Insecure · ignorar validação do certificado", id="provider-tls")
            yield Select([], id="provider-test-model", prompt="Modelo para testar (opcional)")
            yield Static(
                "Sem modelo: testa o catálogo. Com modelo: testa ferramentas e resposta "
                "com duas chamadas curtas de IA (pode haver custo).",
                markup=False,
            )
            yield Static(
                "Credencial local fora do projeto, com permissões privadas.",
                markup=False,
            )
            yield Static("", id="provider-error", markup=False)
            with Horizontal(id="provider-actions"):
                yield Button("Cadastrar e listar modelos", id="provider-save", variant="primary")
            with Horizontal(classes="provider-test-actions"):
                yield Button("Testar conexão", id="provider-test")
                yield Button("Voltar", id="provider-back")

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "provider-kind":
            kind = event.value
            if isinstance(kind, str):
                self.query_one("#provider-url", Input).value = PRESETS[kind]
                self.query_one("#provider-test-model", Select).set_options([])

    async def test_connection(self):
        self.saving = True
        for control in self.query("Input, Select, Checkbox, Button"):
            control.disabled = True
        status = self.query_one("#provider-error", Static)
        status.update("Testando conexão…")
        model = self.query_one("#provider-test-model", Select).value
        try:
            models = await asyncio.to_thread(
                self.store.test_connection,
                str(self.query_one("#provider-kind", Select).value),
                self.query_one("#provider-key", Input).value.strip(),
                base_url=self.query_one("#provider-url", Input).value.strip(),
                tls_insecure=self.query_one("#provider-tls", Checkbox).value,
                model_id=model if isinstance(model, str) else None,
            )
            if not self.is_attached:
                return
            self.query_one("#provider-test-model", Select).set_options(
                [(item["id"], item["id"]) for item in models if item["tools"] is not False]
            )
            if isinstance(model, str):
                self.query_one("#provider-test-model", Select).value = model
            status.update(
                "Conexão, ferramentas e resposta do modelo verificadas. Nenhum perfil salvo."
                if isinstance(model, str)
                else f"Catálogo acessível: {len(models)} modelos. Selecione um modelo e teste "
                "novamente para verificar ferramentas e resposta. Nenhum perfil salvo."
            )
        except (ValueError, OSError, ModelError) as exc:
            status.update(str(exc))
        finally:
            self.saving = False
            for control in self.query("Input, Select, Checkbox, Button"):
                control.disabled = False

    def action_back(self):
        if not self.saving:
            self.dismiss(None)

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "provider-test" and not self.saving:
            await self.test_connection()
            return
        if event.button.id == "provider-back":
            self.action_back()
        elif event.button.id == "provider-save" and not self.saving:
            self.saving = True
            event.button.disabled = True
            self.query_one("#provider-error", Static).update("Consultando o catálogo da API…")
            try:
                kind = str(self.query_one("#provider-kind", Select).value)
                name = self.query_one("#provider-name", Input).value.strip() or kind
                await asyncio.to_thread(
                    self.store.register,
                    kind,
                    self.query_one("#provider-key", Input).value.strip(),
                    name=name,
                    base_url=self.query_one("#provider-url", Input).value.strip(),
                    tls_insecure=self.query_one("#provider-tls", Checkbox).value,
                )
                self.query_one("#provider-key", Input).value = ""
                self.dismiss(name)
            except (ValueError, OSError, ModelError) as exc:
                self.query_one("#provider-error", Static).update(str(exc))
            finally:
                self.saving = False
                event.button.disabled = False


class ModelPicker(ModalScreen[Settings | None]):
    DEFAULT_CSS = """
    ModelPicker { align: center middle; background: #000000 65%; }
    #model-form { width: 90%; max-width: 105; height: auto; padding: 1 2;
        background: #111827; border: round #38bdf8; }
    #model-form Select { margin-bottom: 1; }
    #model-form Static { height: auto; }
    #model-actions { height: auto; }
    """
    BINDINGS = [Binding("escape", "back", "Voltar")]

    def __init__(self, store=None, *, profile=None):
        super().__init__()
        self.store, self.initial_profile = store or ProviderStore(), profile
        self.profiles = self.store.load()
        if not self.profiles["profiles"]:
            raise ValueError("Cadastre um provedor antes de selecionar modelos.")
        self.loading = False

    def compose(self) -> ComposeResult:
        value = self.profiles
        selected = self.initial_profile or value["active"] or next(iter(value["profiles"]), None)
        with Vertical(id="model-form"):
            yield Static("Selecionar provedor e modelo · catálogo da API", markup=False)
            yield Select(
                [(name, name) for name in value["profiles"]],
                value=selected or Select.BLANK,
                id="model-provider",
                allow_blank=False,
            )
            yield Select([], id="model-id", prompt="Modelo")
            yield Static("", id="model-info", markup=False)
            yield Static("", id="model-error", markup=False)
            with Horizontal(id="model-actions"):
                yield Button("Usar modelo", id="model-use", variant="primary")
                yield Button("Atualizar API", id="model-refresh")
                yield Button("Voltar", id="model-back")

    def populate(self, name):
        models = self.store.models(name)
        self.query_one("#model-id", Select).set_options(
            [
                (f"{model['id']} · {model['context_window'] or '?'} tokens", model["id"])
                for model in models
            ]
        )
        profile = self.store.profile(name)[1]
        if any(model["id"] == profile["model"] for model in models):
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
                    f"Saída máxima: {model['max_output_tokens'] or 'não informada'}\n"
                    "Limites aprendidos após rejeição ajustam o orçamento automaticamente."
                )

    def action_back(self):
        if not self.loading:
            self.dismiss(None)

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "model-back":
            self.action_back()
            return
        if self.loading or event.button.id not in {"model-use", "model-refresh"}:
            return
        name = self.query_one("#model-provider", Select).value
        model = self.query_one("#model-id", Select).value
        if not isinstance(name, str):
            return
        self.loading = True
        event.button.disabled = True
        try:
            if event.button.id == "model-refresh":
                await asyncio.to_thread(self.store.models, name, refresh=True)
                self.populate(name)
                self.query_one("#model-error", Static).update("Catálogo atualizado pela API.")
            elif isinstance(model, str):
                settings = await asyncio.to_thread(self.store.select, name, model)
                self.dismiss(settings)
            else:
                self.query_one("#model-error", Static).update("Selecione um modelo.")
        except (ValueError, OSError, ModelError) as exc:
            self.query_one("#model-error", Static).update(str(exc))
        finally:
            self.loading = False
            event.button.disabled = False
