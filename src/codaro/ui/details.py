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
    BINDINGS = [("escape", "close", "Fechar")]

    def __init__(self, title="Conversa anterior", activity="", preview=""):
        super().__init__()
        self.title_text, self.activity, self.preview = title, activity, preview

    def compose(self):
        with Vertical(id="resume-dialog"):
            yield Static("Retomar a última conversa?", classes="dialog-title", markup=False)
            yield Static(safe_preview(self.title_text), id="resume-title", markup=False)
            yield Static(safe_preview(self.activity), classes="muted", markup=False)
            yield Static(safe_preview(self.preview), id="resume-preview", markup=False)
            yield Static(
                "Retomar recupera mensagens e respostas, sem iniciar ações.\n"
                "Nova conversa encerra a tarefa atual; histórico e arquivos ficam salvos.",
                markup=False,
            )
            with Horizontal(classes="dialog-actions"):
                yield Button("Começar nova", id="resume-back")
                yield Button("Retomar conversa", id="resume-use", variant="primary")

    def on_mount(self):
        self.query_one("#resume-use").focus()

    def on_button_pressed(self, event):
        event.stop()
        self.dismiss("resume" if event.button.id == "resume-use" else "new")

    def action_close(self):
        self.dismiss(None)
