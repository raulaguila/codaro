"""Compact mode selection; navigation never changes the running agent."""

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, OptionList, RadioButton, RadioSet, Static

from codaro.policies import Mode
from codaro.ui.screen import DialogScreen as ModalScreen

DESCRIPTIONS = {
    Mode.ASK: "Entender e tirar dúvidas",
    Mode.PLAN: "Investigar e preparar um plano",
    Mode.EXECUTE: "Implementar e validar",
}


class ModePicker(ModalScreen[tuple[Mode, bool] | None]):
    DEFAULT_CSS = """
    ModePicker { align: right top; background: transparent; }
    #mode-picker { width: 62; max-width: 96%; height: 9; max-height: 100%;
        margin: 3 2 0 0; padding: 0 1; border: round $primary 35%; background: $surface; }
    #mode-fields { height: 1fr; }
    #mode-picker Static { height: auto; }
    #mode-title { margin-bottom: 1; color: $text; text-style: bold; }
    #mode-options { height: 3; border: none; padding: 0; background: $surface; }
    #mode-options > .option-list--option { padding: 0 1; }
    #mode-options > .option-list--option-highlighted { background: $primary 15%;
        color: $text; text-style: none; }
    #edit-policy { border-top: solid $primary 30%; padding: 0; height: auto; margin-top: 1; }
    #edit-policy-label { color: $text-muted; }
    #edit-policy RadioSet { height: auto; border: none; padding: 0; }
    #mode-policy-help { color: $text-muted; max-height: 2; }
    #mode-actions { height: 2; border-top: solid $primary 30%; }
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
                yield Static("Modo de trabalho", id="mode-title", markup=False)
                yield OptionList(
                    *[
                        Text(
                            ("✓ " if m == self.current else "  ")
                            + f"{m.label:9}  {DESCRIPTIONS[m]}"
                        )
                        for m in self.modes
                    ],
                    id="mode-options",
                )
                with Vertical(id="edit-policy"):
                    yield Static("Edições", id="edit-policy-label", markup=False)
                    with RadioSet(id="edit-options"):
                        yield RadioButton(
                            "Pedir autorização",
                            value=not self.auto_edits,
                            id="edit-ask",
                            compact=True,
                        )
                        yield RadioButton(
                            "Aprovar automaticamente",
                            value=self.auto_edits,
                            id="edit-auto",
                            compact=True,
                        )
                    yield Static(
                        "Criar e editar nesta conversa.\nRemoções e comandos pedem autorização.",
                        id="mode-policy-help",
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
        self.query_one("#mode-picker").styles.height = (
            16 if self.query_one("#edit-policy").display else 9
        )

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
