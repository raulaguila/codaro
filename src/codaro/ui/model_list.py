"""Visible, searchable model rows; highlighting is separate from activation."""

from rich.cells import cell_len
from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from codaro.index import safe_preview
from codaro.ui.session_details import compact_tokens


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
        if self.highlighted is None:
            return None
        option = self.get_option_at_index(self.highlighted)
        return None if option.disabled else option.id

    @value.setter
    def value(self, value):
        try:
            if self.get_option(value).disabled:
                raise ValueError("Modelo indisponível para conversa.")
            self.highlighted = self.get_option_index(value)
        except Exception as exc:
            raise ValueError("Modelo não disponível nesta lista.") from exc

    def set_models(self, models, query=""):
        self.models, self.search_query = models, query
        previous = self.value
        self.clear_options()
        self.add_options(
            [
                Option(
                    self.row(model),
                    id=model["id"],
                    disabled=model["tools"] is False,
                )
                for model in models
                if query.casefold() in (model["id"] + " " + model["name"]).casefold()
            ]
        )
        if self.option_count:
            try:
                self.value = previous
            except ValueError:
                self.highlighted = next(
                    (
                        i
                        for i in range(self.option_count)
                        if not self.get_option_at_index(i).disabled
                    ),
                    None,
                )

    def row(self, model):
        width = max(20, self.content_size.width or 60)
        right = (
            "sem ferramentas"
            if model["tools"] is False
            else compact_tokens(model["context_window"])
            if model["context_window"]
            else "Não informado"
        )
        left_width = max(4, width - cell_len(right) - 3)
        left = Text(safe_preview(model["id"]), no_wrap=True, overflow="ellipsis")
        left.truncate(left_width, overflow="ellipsis", pad=True)
        left.append("  " + right)
        return left

    def on_resize(self):
        if hasattr(self, "models"):
            self.set_models(self.models, self.search_query)

    def on_option_list_option_highlighted(self, event):
        event.stop()
        self.post_message(self.Changed(self.value))

    def action_choose(self):
        if self.value:
            self.post_message(self.Chosen())
