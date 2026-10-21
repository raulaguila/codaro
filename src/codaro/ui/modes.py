"""Compact mode selection; navigation never changes the running agent."""

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, OptionList, RadioButton, RadioSet, Static

from codaro.policies import Mode

DESCRIPTIONS = {
    Mode.ASK: "Entender e tirar dúvidas",
    Mode.PLAN: "Investigar e preparar um plano",
    Mode.EXECUTE: "Implementar e validar",
}


class ModePicker(ModalScreen[tuple[Mode, bool] | None]):
    DEFAULT_CSS = """
    ModePicker { align: right top; background: transparent; }
    #mode-picker { width: 62; max-width: 96%; height: 11; max-height: 80%;
        margin: 3 2 0 0; padding: 0 1; border: round $primary 35%; background: $surface; }
    #mode-fields { height: 1fr; }
    #mode-picker Static { height: auto; }
    #mode-options { height: 3; border: none; padding: 0; background: $surface; }
    #mode-options > .option-list--option { padding: 0 1; }
    #mode-options > .option-list--option-highlighted { background: $primary 15%;
        color: $text; text-style: none; }
    #edit-policy { border: none; padding: 0; height: auto; }
    #edit-policy > Contents { padding: 0; }
    #edit-policy RadioSet { height: auto; border: none; padding: 0; }
    #mode-actions { height: 3; padding-top: 1; border-top: solid $primary 30%; }
    #mode-help { width: 1fr; height: 1; color: $text-muted; }
    #mode-actions Button { height: 1; min-height: 1; border: none;
        width: 12; min-width: 8; padding: 0; background: $accent;
        color: $background; text-style: bold; }
    #mode-apply:hover, #mode-apply:focus { background: $accent-lighten-1; color: $background; }
    """
    BINDINGS = [
        Binding("escape", "back", "Voltar", priority=True),
        Binding("enter", "apply_mode", "Aplicar", priority=True),
    ]

    def __init__(self, current, *, auto_edits=False):
        super().__init__()
        self.current = Mode(current)
        self.auto_edits = auto_edits
        self.modes = list(Mode)

    def compose(self):
        with Vertical(id="mode-picker"):
            with VerticalScroll(id="mode-fields"):
                yield Static("Modo de trabalho", markup=False)
                yield OptionList(
                    *[
                        Text(
                            f"{m.label:9}  {DESCRIPTIONS[m]}"
                            + (" · Atual" if m == self.current else "")
                        )
                        for m in self.modes
                    ],
                    id="mode-options",
                )
                with Collapsible(title="Edições · permissões", collapsed=True, id="edit-policy"):
                    with RadioSet(id="edit-options"):
                        yield RadioButton(
                            "Pedir autorização", value=not self.auto_edits, id="edit-ask"
                        )
                        yield RadioButton(
                            "Aprovar automaticamente", value=self.auto_edits, id="edit-auto"
                        )
                    yield Static(
                        "Criar e editar arquivos permitidos nesta conversa. "
                        "Remoções e comandos continuam exigindo autorização.",
                        markup=False,
                    )
            with Horizontal(id="mode-actions"):
                yield Static(
                    "↑↓ navegar · Enter aplicar · Esc fechar", id="mode-help", markup=False
                )
                yield Button("Aplicar", id="mode-apply", variant="primary")

    def on_mount(self):
        options = self.query_one(OptionList)
        options.highlighted = self.modes.index(self.current)
        options.focus()
        self.update_policy()

    def update_policy(self):
        index = self.query_one(OptionList).highlighted
        self.query_one("#edit-policy").display = index == self.modes.index(Mode.EXECUTE)
        self.fit()

    def fit(self):
        policy = self.query_one("#edit-policy", Collapsible)
        self.query_one("#mode-picker").styles.height = (
            17 if policy.display and not policy.collapsed else 10
        )
        automatic = self.query_one("#edit-auto", RadioButton).value
        policy.title = "Edições: " + (
            "aprovar automaticamente" if automatic else "pedir autorização"
        )

    def on_radio_set_changed(self):
        self.fit()

    def on_collapsible_expanded(self):
        self.fit()

    def on_collapsible_collapsed(self):
        self.fit()

    def on_option_list_option_highlighted(self):
        self.update_policy()

    def action_apply_mode(self):
        index = self.query_one(OptionList).highlighted
        if index is not None:
            mode = self.modes[index]
            automatic = mode == Mode.EXECUTE and self.query_one("#edit-auto", RadioButton).value
            self.dismiss((mode, automatic))

    def action_back(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "mode-apply":
            self.action_apply_mode()

    def on_click(self, event):
        if event.widget is self:
            self.action_back()
