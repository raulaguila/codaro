"""Transcript elements shared by live, restored and diagnostic conversations."""

from rich.text import Text
from textual.widgets import Static

from codaro.index import safe_preview


class UserMessage(Static):
    def __init__(self, text):
        self.message = text
        super().__init__(classes="question", markup=False)

    def on_mount(self):
        colors = self.app.get_css_variables()
        self.update(Text.assemble(("Você\n", colors["text-muted"]), safe_preview(self.message)))
