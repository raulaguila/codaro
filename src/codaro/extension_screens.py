"""Explicit integration registration, with connection testing before saving."""

import hashlib
import shlex
import threading
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Select, Static

from codaro.features import DEFAULTS, FeatureStore
from codaro.index import safe_preview
from codaro.storage import private_read


class IntegrationRegistration(ModalScreen[bool]):
    DEFAULT_CSS = """
    IntegrationRegistration { align: center middle; background: #000000 65%; }
    #integration-form { width: 95%; max-width: 100; height: 90%; border: round $accent;
                        background: $surface; padding: 1; }
    #integration-fields { height: 1fr; }
    #integration-form Input, #integration-form Select { margin-bottom: 1; }
    #integration-actions { height: auto; }
    #integration-actions Button { width: 1fr; min-width: 8; }
    #integration-status { height: auto; color: $warning; }
    """
    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, root):
        super().__init__()
        self.root = root
        self.cancelled = threading.Event()

    def compose(self) -> ComposeResult:
        with Vertical(id="integration-form"):
            yield Static("Cadastrar MCP ou plugin", markup=False)
            with VerticalScroll(id="integration-fields"):
                yield Select(
                    [
                        ("MCP · processo local", "stdio"),
                        ("MCP · HTTP", "http"),
                        ("Plugin · Python", "plugin"),
                    ],
                    value="stdio",
                    allow_blank=False,
                    id="integration-kind",
                )
                yield Input(placeholder="Nome (letras, números e _)", id="integration-name")
                yield Input(
                    placeholder="Comando e argumentos do servidor", id="integration-command"
                )
                yield Input(placeholder="URL MCP HTTP", id="integration-url")
                yield Input(placeholder="Caminho do plugin Python", id="integration-plugin")
                yield Input(
                    placeholder="Variável de ambiente do token (opcional)", id="integration-token"
                )
                yield Input(
                    placeholder="Variáveis autorizadas ao processo, separadas por vírgula",
                    id="integration-env",
                )
                yield Input(
                    placeholder="Ferramentas de leitura confiáveis, separadas por vírgula",
                    id="integration-readonly",
                )
                yield Checkbox("TLS Insecure", id="integration-tls")
                yield Checkbox(
                    "Confio neste código/serviço e autorizo sua inicialização",
                    id="integration-trust",
                )
                yield Static(
                    "Ferramentas não marcadas como leitura exigirão aprovação por ação.",
                    markup=False,
                )
            yield Static("", id="integration-status", markup=False)
            with Horizontal(id="integration-actions"):
                yield Button("Testar", id="integration-test")
                yield Button("Salvar", id="integration-save", variant="primary")
                yield Button("Cancelar", id="integration-cancel")

    def on_mount(self):
        self.update_fields()

    def on_select_changed(self, event):
        if event.select.id == "integration-kind":
            self.update_fields()

    def update_fields(self):
        kind = self.query_one("#integration-kind", Select).value
        for field, visible in {
            "command": kind == "stdio",
            "url": kind == "http",
            "token": kind == "http",
            "tls": kind == "http",
            "plugin": kind == "plugin",
            "env": kind != "http",
        }.items():
            self.query_one("#integration-" + field).display = visible

    def config(self):
        if not self.query_one("#integration-trust", Checkbox).value:
            raise ValueError("Marque a confiança explícita antes de testar ou salvar.")

        def value(name):
            return self.query_one("#integration-" + name, Input).value.strip()

        name = value("name")
        kind = self.query_one("#integration-kind", Select).value
        config = {
            "trusted": True,
            "enabled": True,
            "read_only_tools": [
                item.strip() for item in value("readonly").split(",") if item.strip()
            ],
            "env": [item.strip() for item in value("env").split(",") if item.strip()]
            if kind != "http"
            else [],
        }
        section = "mcp"
        if kind == "plugin":
            section = "plugins"
            path = Path(value("plugin")).expanduser()
            path = path if path.is_absolute() else self.root / path
            raw = private_read(path, 1_000_000)
            config.update(path=str(path.absolute()), sha256=hashlib.sha256(raw).hexdigest())
        elif kind == "stdio":
            config.update(transport="stdio", command=shlex.split(value("command")))
        else:
            config.update(
                transport="http",
                url=value("url"),
                token_env=value("token"),
                tls_insecure=self.query_one("#integration-tls", Checkbox).value,
            )
        data = {**DEFAULTS, section: {name: config}}
        FeatureStore.validate(data)
        return section, name, config

    def on_button_pressed(self, event):
        if event.button.id == "integration-cancel":
            self.action_cancel()
            return
        try:
            section, name, config = self.config()
            if event.button.id == "integration-test":
                for button in self.query(Button):
                    if button.id != "integration-cancel":
                        button.disabled = True
                self.query_one("#integration-status", Static).update("Testando conexão…")
                self.test_connection(section, name, config)
            else:
                store = FeatureStore(self.root)
                data = store.load()
                if name in data[section]:
                    raise ValueError("Nome já cadastrado. Remova antes de renovar a confiança.")
                data[section][name] = config
                store.save(data)
                self.dismiss(True)
        except (OSError, ValueError) as exc:
            self.query_one("#integration-status", Static).update(safe_preview(str(exc)))

    @work(thread=True, exclusive=True)
    def test_connection(self, section, name, config):
        from codaro.agent import Agent
        from codaro.provider import OpenAICompatible, RequestCancelled, Settings
        from codaro.repository import Repository

        agent = Agent(
            Repository(self.root),
            OpenAICompatible(Settings("http://localhost:11434/v1", "unused")),
            features={**DEFAULTS, "mcp": {}, "plugins": {}, section: {name: config}},
            persist_memory=False,
        )
        agent._cancelled = self.cancelled
        try:
            agent.integrations.discover()
            errors = agent.integrations.errors
            message = (
                "Conectado · "
                + str(
                    sum(
                        tool.source.startswith(("mcp:", "plugin:"))
                        for tool in agent.registry.tools.values()
                    )
                )
                + " ferramentas."
                if not errors
                else "\n".join(errors.values())
            )
        except (OSError, ValueError, RequestCancelled) as exc:
            message = str(exc)
        finally:
            agent.integrations.close()
        if self.is_mounted:
            self.app.call_from_thread(self.show_result, message)

    def show_result(self, message):
        if not self.is_mounted:
            return
        self.query_one("#integration-status", Static).update(safe_preview(message))
        for button in self.query(Button):
            button.disabled = False

    def action_cancel(self):
        self.cancelled.set()
        self.dismiss(False)

    def on_unmount(self):
        self.cancelled.set()
