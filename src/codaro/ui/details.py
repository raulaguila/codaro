"""Small read-only dialogs and explicit plan decisions."""

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Collapsible, Static

from codaro.index import safe_preview
from codaro.ui.screen import DialogScreen


class DetailsDialog(DialogScreen):
    BINDINGS = [("escape", "close", "Fechar")]

    def __init__(self, title, rows, *, advanced=""):
        super().__init__()
        self.heading, self.rows, self.advanced = title, rows, advanced

    def compose(self):
        with Vertical(id="details-dialog"):
            yield Static(self.heading, classes="dialog-title", markup=False)
            with VerticalScroll(id="details-body"):
                for label, value in self.rows:
                    with Horizontal(classes="detail-row"):
                        yield Static(safe_preview(label), classes="detail-key", markup=False)
                        yield Static(safe_preview(str(value)), classes="detail-value", markup=False)
                if self.advanced:
                    with Collapsible(title="Avançado", collapsed=True):
                        yield Static(safe_preview(self.advanced), markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Fechar", id="details-close")

    def on_button_pressed(self, event):
        event.stop()
        self.action_close()

    def action_close(self):
        self.dismiss(None)


class ResumeDialog(DialogScreen):
    BINDINGS = [("escape", "close", "Agora não")]

    def compose(self):
        with Vertical(id="resume-dialog"):
            yield Static("Retomar a última conversa?", classes="dialog-title", markup=False)
            yield Static(
                "O histórico e as decisões serão recuperados. Nenhuma ação será executada.",
                markup=False,
            )
            with Horizontal(classes="dialog-actions"):
                yield Button("Agora não", id="resume-back")
                yield Button("Retomar", id="resume-use", variant="primary")

    def on_mount(self):
        self.query_one("#resume-back").focus()

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss(event.button.id == "resume-use")

    def action_close(self):
        self.dismiss(False)
