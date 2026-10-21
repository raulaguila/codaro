"""Visible, searchable model rows; highlighting is separate from activation."""

from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from codaro.index import safe_preview


class ModelList(OptionList):
    BINDINGS = [Binding("enter", "choose", "Usar modelo", priority=True)]

    class Changed(Message):
        def __init__(self, value):
            super().__init__()
            self.value = value

    class Chosen(Message):
        pass

    @property
    def value(self):
        return (
            self.get_option_at_index(self.highlighted).id if self.highlighted is not None else None
        )

    @value.setter
    def value(self, value):
        try:
            self.highlighted = self.get_option_index(value)
        except Exception as exc:
            raise ValueError("Modelo não disponível nesta lista.") from exc

    def set_models(self, models, query=""):
        previous = self.value
        self.clear_options()
        self.add_options(
            [
                Option(
                    safe_preview(f"{model['id']} · {model['context_window'] or '?'} tokens"),
                    id=model["id"],
                )
                for model in models
                if query.casefold() in (model["id"] + " " + model["name"]).casefold()
            ]
        )
        if self.option_count:
            try:
                self.value = previous
            except ValueError:
                self.highlighted = 0

    def on_option_list_option_highlighted(self, event):
        event.stop()
        self.post_message(self.Changed(self.value))

    def action_choose(self):
        if self.value:
            self.post_message(self.Chosen())
