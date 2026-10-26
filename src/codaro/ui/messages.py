"""Transcript elements shared by live, restored and diagnostic conversations."""

from rich.text import Text
from textual.widgets import Static

from codaro.index import safe_preview


class UserMessage(Static):
    def __init__(self, text, *, decision=False):
        self.message = text
        self.decision = decision
        super().__init__(classes="question", markup=False)

    def on_mount(self):
        colors = self.app.get_css_variables()
        label = "Você · decisão\n" if self.decision else "Você\n"
        self.update(Text.assemble((label, colors["text-muted"]), safe_preview(self.message)))
