"""Trusted discovery and explicit per-tool read permissions before registration."""

import asyncio
import hashlib
import json
import shlex
import threading
from pathlib import Path

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Collapsible, Input, Select, SelectionList, Static
from textual.widgets.selection_list import Selection

from codaro.features import DEFAULTS, FeatureStore
from codaro.index import safe_preview
from codaro.storage import private_read
from codaro.ui.screen import DialogScreen


class IntegrationRegistration(DialogScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, root):
        super().__init__()
        self.root = root
        self.cancelled = threading.Event()
        self.testing = False
        self.generation = 0
        self.tested_signature = None
        self.tools = []

    def compose(self):
        with Vertical(id="integration-form"):
            yield Static("Conectar integração", classes="dialog-title", markup=False)
            with VerticalScroll(id="integration-fields"):
                yield Static("Tipo de integração", markup=False)
                yield Select(
                    [
                        ("MCP · processo local", "stdio"),
                        ("MCP · HTTP", "http"),
                        ("Plugin · Python", "plugin"),
                    ],
                    value="stdio",
                    allow_blank=False,
                    id="integration-kind",
                    compact=True,
                )
                yield Static("Nome", markup=False)
                yield Input(placeholder="Ex.: meu_servidor", id="integration-name", compact=True)
                for key, label, placeholder in (
                    ("command", "Comando do servidor", "Ex.: npx -y servidor-mcp"),
                    ("url", "URL MCP HTTP", "https://servidor/mcp"),
                    ("plugin", "Arquivo do plugin Python", "plugins/exemplo.py"),
                ):
                    with Vertical(id="integration-field-" + key):
                        yield Static(label, markup=False)
                        yield Input(placeholder=placeholder, id="integration-" + key, compact=True)
                with Collapsible(title="Avançado", collapsed=True, id="integration-advanced"):
                    for key, label, placeholder in (
                        (
                            "env",
                            "Variáveis de ambiente permitidas",
                            "Nomes separados por vírgula; ex.: PATH, TOKEN_API",
                        ),
                        (
                            "token",
                            "Variável do token HTTP (opcional)",
                            "Nome da variável; ex.: TOKEN_API",
                        ),
                    ):
                        with Vertical(id="integration-field-" + key):
                            yield Static(label, markup=False)
                            yield Input(
                                placeholder=placeholder, id="integration-" + key, compact=True
                            )
                    yield Checkbox(
                        "TLS Insecure · não verificar certificado",
                        id="integration-tls",
                        compact=True,
                    )
                yield Checkbox(
                    "Confio e autorizo iniciar esta integração",
                    id="integration-trust",
                    compact=True,
                )
                with Vertical(id="integration-tools-group"):
                    yield Static("Ferramentas confiáveis de leitura", markup=False)
                    yield SelectionList(id="integration-tools")
                    yield Static("", id="integration-tool-description", markup=False)
                yield Static(
                    "Ferramentas não marcadas exigem aprovação por ação.",
                    classes="muted",
                    markup=False,
                )
            yield Static(
                "Autorize a inicialização e teste para descobrir as ferramentas.",
                id="integration-status",
                markup=False,
            )
            with Horizontal(classes="dialog-actions"):
                yield Button("Testar", id="integration-test")
                yield Button("Cancelar", id="integration-cancel")
                yield Button("Salvar", id="integration-save", variant="primary")

    def on_mount(self):
        self.update_fields()
        self.update_controls()
        self.query_one("#integration-name").focus()
        self.query_one(
            "#integration-trust"
        ).tooltip = "O teste inicia o plugin ou conecta/inicia o servidor."

    def on_collapsible_expanded(self):
        self.fit()

    def on_collapsible_collapsed(self):
        self.fit()

    def fit(self):
        self.query_one("#integration-form").styles.height = (
            30 if not self.query_one(Collapsible).collapsed else 28 if self.tools else 22
        )

    def on_select_changed(self, event):
        if event.select.id == "integration-kind":
            self.update_fields()
            self.invalidate_test()

    def on_input_changed(self):
        self.invalidate_test()

    def on_checkbox_changed(self, event):
        if event.checkbox.id in {"integration-trust", "integration-tls"}:
            self.invalidate_test()

    def update_fields(self):
        kind = self.query_one("#integration-kind", Select).value
        for field, visible in {
            "command": kind == "stdio",
            "url": kind == "http",
            "plugin": kind == "plugin",
            "env": kind != "http",
            "token": kind == "http",
        }.items():
            self.query_one("#integration-field-" + field).display = visible
        self.query_one("#integration-tls").display = kind == "http"

    def invalidate_test(self):
        if not self.is_mounted:
            return
        if self.tested_signature is not None or self.testing:
            self.query_one("#integration-status", Static).update(
                "Configuração alterada · teste novamente antes de salvar."
            )
        if self.testing:
            self.cancelled.set()
            self.generation += 1
            self.testing = False
        self.tested_signature = None
        self.tools = []
        self.query_one(SelectionList).clear_options()
        self.query_one("#integration-tool-description", Static).update("")
        self.query_one("#integration-status").remove_class("error", "success")
        self.update_controls()

    def update_controls(self):
        for control in self.query("Input, Select, Checkbox, Button, SelectionList"):
            control.disabled = self.testing
        self.query_one("#integration-cancel", Button).disabled = False
        self.query_one("#integration-cancel", Button).label = (
            "Cancelar teste" if self.testing else "Cancelar"
        )
        self.query_one("#integration-save", Button).disabled = (
            self.testing or self.tested_signature is None
        )
        self.query_one("#integration-test", Button).disabled = (
            self.testing or not self.query_one("#integration-trust", Checkbox).value
        )
        self.query_one("#integration-tools-group").display = bool(self.tools)
        self.fit()

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
            "read_only_tools": list(self.query_one(SelectionList).selected),
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
        FeatureStore.validate({**DEFAULTS, section: {name: config}})
        return section, name, config

    @staticmethod
    def signature(section, name, config):
        endpoint = {key: value for key, value in config.items() if key != "read_only_tools"}
        return json.dumps([section, name, endpoint], sort_keys=True)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "integration-cancel":
            self.action_cancel()
            return
        if self.testing:
            return
        try:
            section, name, config = self.config()
            signature = self.signature(section, name, config)
            if event.button.id == "integration-test":
                self.testing = True
                self.generation += 1
                self.cancelled = threading.Event()
                self.tested_signature = None
                self.tools = []
                self.update_controls()
                self.query_one("#integration-status").remove_class("error", "success")
                self.query_one("#integration-status", Static).update(
                    "Iniciando serviço e consultando ferramentas…"
                )
                self.run_worker(
                    self.test_connection(
                        section, name, config, signature, self.generation, self.cancelled
                    )
                )
            elif event.button.id == "integration-save":
                if signature != self.tested_signature:
                    self.invalidate_test()
                    raise ValueError("Teste esta configuração antes de salvar.")
                FeatureStore(self.root).register_integration(section, name, config)
                self.dismiss(True)
        except (OSError, ValueError) as exc:
            self.query_one("#integration-status", Static).update(safe_preview(str(exc)))
            self.query_one("#integration-status").set_class(True, "error")

    def probe(self, section, name, config, cancelled):
        from codaro.agent import Agent
        from codaro.llm import OpenAICompatible, Settings
        from codaro.repository import Repository

        agent = Agent(
            Repository(self.root),
            OpenAICompatible(Settings("http://localhost:11434/v1", "unused")),
            features={**DEFAULTS, "mcp": {}, "plugins": {}, section: {name: config}},
            persist_memory=False,
        )
        agent._cancelled = cancelled
        try:
            agent.integrations.discover()
            if agent.integrations.errors:
                raise ValueError(agent.memory.redact("\n".join(agent.integrations.errors.values())))
            return [
                {
                    "name": tool.external_name,
                    "label": safe_preview(agent.memory.redact(tool.external_name)),
                    "description": safe_preview(
                        agent.memory.redact(tool.schema["function"]["description"])
                    ),
                }
                for tool in agent.registry.tools.values()
                if tool.external_name is not None and tool.source.startswith(("mcp:", "plugin:"))
            ]
        finally:
            agent.integrations.close()

    async def test_connection(self, section, name, config, signature, generation, cancelled):
        from codaro.llm import RequestCancelled

        try:
            tools = await asyncio.to_thread(self.probe, section, name, config, cancelled)
            if not self.is_mounted or generation != self.generation or cancelled.is_set():
                return
            if self.signature(*self.config()) != signature:
                raise ValueError(
                    "A integração mudou durante o teste. Teste novamente antes de salvar."
                )
            self.tools, self.tested_signature = tools, signature
            choices = self.query_one(SelectionList)
            choices.clear_options()
            choices.styles.height = max(1, min(4, len(tools)))
            choices.add_options(
                [
                    Selection(tool["label"], tool["name"], False, disabled=len(tool["name"]) > 80)
                    for tool in tools
                ]
            )
            self.query_one("#integration-status", Static).update(
                f"Conectado · {len(tools)} ferramentas. Escolha as confiáveis de leitura."
            )
            self.query_one("#integration-status").set_class(True, "success")
            if tools:
                choices.highlighted = 0
                choices.focus()
                self.call_after_refresh(
                    self.query_one("#integration-tools-group").scroll_visible,
                    animate=False,
                    immediate=True,
                )
        except (OSError, ValueError, RequestCancelled) as exc:
            if self.is_mounted and generation == self.generation:
                self.query_one("#integration-status", Static).update(safe_preview(str(exc)))
                self.query_one("#integration-status").set_class(True, "error")
        finally:
            if self.is_mounted and generation == self.generation:
                self.testing = False
                self.update_controls()

    def on_selection_list_selection_highlighted(self, event):
        event.stop()
        name = event.selection.value
        tool = next((item for item in self.tools if item["name"] == name), None)
        if tool:
            self.query_one("#integration-tool-description", Static).update(tool["description"])

    def action_cancel(self):
        self.cancelled.set()
        self.generation += 1
        if self.testing:
            self.testing = False
            self.tested_signature = None
            self.update_controls()
            self.query_one("#integration-status", Static).update(
                "Teste cancelado · nenhum cadastro salvo."
            )
        else:
            self.dismiss(False)

    def on_unmount(self):
        self.cancelled.set()
        self.generation += 1
