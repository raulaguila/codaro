"""Project features with explicit drafts and bounded, read-only advanced JSON."""

import json

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Collapsible, Input, Static

from codaro.features import FeatureStore
from codaro.index import safe_preview
from codaro.ui.screen import DialogScreen

FEATURE_LABELS = {
    "artifacts": "Recuperar resultados extensos",
    "semantic_compaction": "Resumir contexto antigo",
    "exploration": "Exploração assistida",
    "lsp": "Navegação por símbolos (LSP)",
}
DESCRIPTIONS = {
    "artifacts": "Guarda resultados para consultar em partes.",
    "semantic_compaction": "Pode gerar chamadas adicionais ao provedor para resumir o histórico.",
    "exploration": "Permite investigações adicionais com o modelo.",
    "lsp": "Usa os servidores de linguagem configurados no projeto.",
}
LIMITS = {
    "context_reserve_ratio": "Reserva de contexto (0.05–0.4)",
    "recent_ratio": "Proporção de histórico recente (0.05–0.4)",
    "max_run_requests": "Máximo de chamadas por atividade (4–256)",
    "max_run_tokens": "Orçamento de tokens por atividade (16384–10000000)",
}


class FeaturesDialog(DialogScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, root):
        super().__init__()
        self.store = FeatureStore(root)
        self.values = self.store.load()

    def compose(self):
        with Vertical(id="features-form"):
            yield Static("Funcionalidades do projeto", classes="dialog-title", markup=False)
            with VerticalScroll(id="features-fields"):
                for key, label in FEATURE_LABELS.items():
                    enabled = self.values["lsp"]["enabled"] if key == "lsp" else self.values[key]
                    yield Checkbox(label, enabled, id="feature-" + key, compact=True)
                    yield Static(DESCRIPTIONS[key], classes="feature-description", markup=False)
                yield Static("Integrações", classes="feature-heading", markup=False)
                summary = " · ".join(
                    f"{label}: {sum(item['enabled'] for item in self.values[key].values())} ativas"
                    for key, label in (("mcp", "MCP"), ("plugins", "Plugins"))
                )
                yield Static(summary + "\nCadastro disponível em /integrations.", markup=False)
                with Collapsible(title="Avançado", collapsed=True, id="features-advanced"):
                    for key, label in LIMITS.items():
                        yield Static(label, markup=False)
                        yield Input(str(self.values[key]), id="feature-" + key, compact=True)
                    yield Static("Configuração salva · JSON somente leitura", markup=False)
                    preview = self.app.agent.memory.redact(
                        json.dumps(self.values, ensure_ascii=False, indent=2)
                    )
                    yield Static(safe_preview(preview), id="features-json", markup=False)
            yield Static("", id="features-error", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancelar", id="features-cancel")
                yield Button("Salvar", id="features-save", variant="primary")

    def on_mount(self):
        if self.query(Checkbox):
            self.query_one(Checkbox).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id != "features-save":
            self.action_cancel()
            return
        try:
            changes = {}
            for key in FEATURE_LABELS:
                value = self.query_one("#feature-" + key, Checkbox).value
                old = self.values["lsp"]["enabled"] if key == "lsp" else self.values[key]
                if value != old:
                    changes[key] = value
            for key in LIMITS:
                raw = self.query_one("#feature-" + key, Input).value.strip()
                value = float(raw) if key.endswith("ratio") else int(raw)
                if value != self.values[key]:
                    changes[key] = value
            if changes:
                self.store.update_options(changes)
            self.dismiss(bool(changes))
        except (OSError, ValueError, OverflowError) as exc:
            self.query_one("#features-error", Static).update(safe_preview(str(exc)))
