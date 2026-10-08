from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea


class Prompt(TextArea):
    """A wrapping composer with explicit submission and portable newline shortcuts."""

    suggesting = False

    BINDINGS = [
        Binding("enter", "submit", "Enviar", show=False, priority=True),
        Binding("alt+enter,shift+enter", "newline", "Nova linha", show=False, priority=True),
        Binding("tab", "complete", "Completar", show=False, priority=True),
        Binding("up", "previous", "Anterior", show=False, priority=True),
        Binding("down", "next", "Próxima", show=False, priority=True),
        Binding("alt+up", "history_previous", "Histórico", show=False, priority=True),
        Binding("alt+down", "history_next", "Histórico", show=False, priority=True),
    ]

    class Submitted(Message):
        def __init__(self, input: Prompt):
            super().__init__()
            self.input = input
            self.value = input.text

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, value: str):
        self.load_text(value)

    def action_submit(self):
        if not self.disabled:
            self.post_message(self.Submitted(self))

    def action_newline(self):
        if not self.disabled:
            self.replace(
                "\n", self.selection.start, self.selection.end, maintain_selection_offset=False
            )

    class Complete(Message):
        pass

    class Navigate(Message):
        def __init__(self, direction: int, history_only=False):
            super().__init__()
            self.direction, self.history_only = direction, history_only

    def action_complete(self):
        self.post_message(self.Complete())

    def action_previous(self):
        row = self.wrapped_document.location_to_offset(self.cursor_location).y
        if (self.suggesting or row == 0) and self.selection.is_empty:
            self.post_message(self.Navigate(-1))
        else:
            self.action_cursor_up()

    def action_next(self):
        row = self.wrapped_document.location_to_offset(self.cursor_location).y
        if (self.suggesting or row == self.wrapped_document.height - 1) and self.selection.is_empty:
            self.post_message(self.Navigate(1))
        else:
            self.action_cursor_down()

    def action_history_previous(self):
        self.post_message(self.Navigate(-1, True))

    def action_history_next(self):
        self.post_message(self.Navigate(1, True))
