"""Startup preferences: ordinary choices first, bounded execution defaults below."""

from dataclasses import asdict

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Collapsible, Input, Select, Static

from codaro.preferences import PreferencesStore, preference_value


class PreferencesDialog(ModalScreen[bool]):
    DEFAULT_CSS = """
    PreferencesDialog { align: center middle; background: #000000 65%; }
    #preferences-form { width: 95%; max-width: 85; height: 85%; border: round $accent;
                        background: $surface; padding: 0 1; }
    #preferences-fields { height: 1fr; }
    #preferences-form Static { height: auto; }
    #preferences-actions { height: 3; }
    #preferences-actions Button { width: 1fr; min-width: 8; padding: 0; }
    #preferences-advanced { height: auto; border: none; padding: 0; }
    #preferences-advanced > Contents { padding: 0; }
    #preferences-error { max-height: 3; color: $warning; }
    """
    BINDINGS = [("escape", "cancel", "Cancelar")]

    def __init__(self, store=None):
        super().__init__()
        self.store = store or PreferencesStore()
        self.preferences = self.store.load()

    def compose(self):
        values = asdict(self.preferences)
        with Vertical(id="preferences-form"):
            yield Static("Preferências de inicialização", markup=False)
            with VerticalScroll(id="preferences-fields"):
                yield Static("Modo inicial", markup=False)
                yield Select(
                    [("Perguntar", "ask"), ("Planejar", "plan"), ("Executar", "execute")],
                    value=values["mode"],
                    allow_blank=False,
                    id="preference-mode",
                )
                yield Checkbox(
                    "Retomar a última conversa ao abrir",
                    value=values["resume"],
                    id="preference-resume",
                )
                with Collapsible(
                    title="Limites avançados", collapsed=True, id="preferences-advanced"
                ):
                    for name, label in {
                        "max_steps": "Etapas por atividade · vazio usa o padrão do modo",
                        "max_seconds": "Duração da atividade em segundos (1–7200)",
                        "max_corrections": "Tentativas de correção (1–10)",
                        "timeout": "Timeout HTTP em segundos (1–300)",
                    }.items():
                        yield Static(label, markup=False)
                        yield Input(
                            "" if values[name] is None else str(values[name]),
                            id="preference-" + name,
                        )
                yield Static(
                    "Usadas em novas inicializações. Não altera permissões da tarefa atual.\n"
                    "Provedor, modelo e TLS são configurados no menu de provedores.",
                    markup=False,
                )
            yield Static("", id="preferences-error", markup=False)
            with Horizontal(id="preferences-actions"):
                yield Button("Salvar", variant="primary", id="preferences-save")
                yield Button("Cancelar", id="preferences-cancel")

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event):
        if event.button.id != "preferences-save":
            self.action_cancel()
            return
        try:
            changes = {
                "mode": str(self.query_one("#preference-mode", Select).value),
                "resume": self.query_one("#preference-resume", Checkbox).value,
            }
            for name in ("max_steps", "max_seconds", "max_corrections", "timeout"):
                raw = self.query_one("#preference-" + name, Input).value.strip()
                changes[name] = preference_value(
                    name, raw or "auto" if name == "max_steps" else raw
                )
            self.store.update(changes)
            self.dismiss(True)
        except (ValueError, OSError) as exc:
            self.query_one("#preferences-error", Static).update(str(exc))
