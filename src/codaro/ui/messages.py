"""Transcript elements shared by live, restored and diagnostic conversations."""

from rich.text import Text
from textual.widgets import Markdown, Static

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


class AssistantMessage(Markdown):
    can_focus = True

    def __init__(self, text):
        self.message_text = text
        super().__init__(text, classes="assistant", open_links=False)

    def on_mouse_down(self, event):
        if event.button == 3:
            event.stop()
            self.show_actions()

    def show_actions(self):
        from textual.screen import ModalScreen

        from codaro.ui.message_actions import MessageActions

        if not isinstance(self.app.screen, ModalScreen):
            self.app.push_screen(MessageActions(self.message_text))


class DecisionMessage(Static):
    def __init__(self, question, answer):
        super().__init__(classes="decision", markup=False)
        self.question, self.answer = question, answer

    def on_mount(self):
        colors = self.app.get_css_variables()
        self.update(
            Text.assemble(
                ("Pergunta respondida\n", colors["text-muted"]),
                "Codaro: " + safe_preview(self.question) + "\n",
                ("Você: ", colors["accent"]),
                safe_preview(self.answer),
            )
        )
