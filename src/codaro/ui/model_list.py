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
            if self.is_attached:
                self.call_after_refresh(self.scroll_to_highlight, top=True)
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
        padding = self.get_component_styles("option-list--option").padding.width
        width = max(1, self.scrollable_content_region.width - padding - 1)
        right = (
            "sem ferramentas"
            if model["tools"] is False
            else compact_tokens(model["context_window"])
            if model["context_window"]
            else "—"
        )
        left_width = max(1, width - cell_len(right) - 2)
        left = Text(safe_preview(model["id"]), no_wrap=True, overflow="ellipsis")
        left.truncate(left_width, overflow="ellipsis", pad=True)
        left.append("  " + right)
        left.truncate(width, overflow="ellipsis")
        return left

    def on_resize(self):
        if hasattr(self, "models"):
            self.set_models(self.models, self.search_query)

    def watch_show_vertical_scrollbar(self, value):
        if hasattr(self, "models"):
            self.call_after_refresh(self.refresh_rows)

    def refresh_rows(self):
        models = {model["id"]: model for model in self.models}
        for index in range(self.option_count):
            model = models.get(self.get_option_at_index(index).id)
            if model:
                self.replace_option_prompt_at_index(index, self.row(model))

    def on_option_list_option_highlighted(self, event):
        event.stop()
        self.post_message(self.Changed(self.value))

    def action_choose(self):
        if self.value:
            self.post_message(self.Chosen())
