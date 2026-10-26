"""Searchable project conversations; navigation never grants permissions."""

import stat
from datetime import datetime

from rich.cells import cell_len
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, OptionList, Static
from textual.widgets.option_list import Option

from codaro.index import safe_preview
from codaro.ui.screen import DialogScreen


class SessionPicker(DialogScreen[str | None]):
    BINDINGS = [
        Binding("escape", "back", "Voltar"),
        Binding("down", "next", "Próxima"),
        Binding("up", "previous", "Anterior"),
    ]

    def __init__(self, catalog, active):
        super().__init__()
        self.catalog, self.active = catalog, active
        self.items = []

    def compose(self):
        with Vertical(id="session-picker"):
            yield Static("Conversas deste projeto", classes="dialog-title", markup=False)
            yield Input(placeholder="Buscar pelo título…", id="session-search", compact=True)
            yield Checkbox("Mostrar arquivadas", id="session-archived", compact=True)
            yield OptionList(id="session-list")
            yield Static("", id="session-picker-info", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Retomar", id="session-use", variant="primary")
                yield Button("Nova", id="session-new")
                yield Button("Arquivar", id="session-archive")
                yield Button("Voltar", id="session-back")

    def on_mount(self):
        self.refresh_items()
        self.call_after_refresh(self.refresh_items)
        self.query_one(Input).focus()

    def on_resize(self):
        self.app.call_after_refresh(self.app.sync_layout)
        if self.query("#session-list"):
            self.call_after_refresh(self.refresh_items)

    def selected(self):
        options = self.query_one(OptionList)
        if options.highlighted is None:
            return None
        identifier = options.get_option_at_index(options.highlighted).id
        return next((item for item in self.items if item["id"] == identifier), None)

    def refresh_items(self):
        previous = self.selected()
        self.items = self.catalog.load()["items"]
        for item in self.items:
            item["activity"] = item.get("created") if isinstance(item.get("created"), str) else ""
            try:
                info = self.catalog.store(item["id"]).path.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    item["activity"] = (
                        datetime.fromtimestamp(info.st_mtime).astimezone().isoformat()
                    )
            except OSError:
                pass
        self.items.sort(key=lambda item: item["activity"], reverse=True)
        query = self.query_one(Input).value.casefold()
        archived = self.query_one(Checkbox).value
        visible = [
            item
            for item in self.items
            if (archived or not item.get("archived"))
            and query in (item["title"] + " " + item["id"]).casefold()
        ]
        options = self.query_one(OptionList)
        options.clear_options()
        for item in visible:
            try:
                date = datetime.fromisoformat(item["activity"]).astimezone().strftime("%d/%m %H:%M")
            except ValueError:
                date = "Sem data"
            suffix = (
                " · atual"
                if item["id"] == self.active
                else " · arquivada"
                if item.get("archived")
                else ""
            )
            right = date + suffix
            label = Text(safe_preview(item["title"]).replace("\n", " "))
            label.truncate(
                max(4, options.content_size.width - cell_len(right) - 3),
                overflow="ellipsis",
                pad=True,
            )
            label.append("  " + right)
            options.add_option(Option(label, id=item["id"]))
        options.highlighted = next(
            (i for i, item in enumerate(visible) if previous and item["id"] == previous["id"]),
            0 if visible else None,
        )
        self.update_selection()

    def update_selection(self):
        item = self.selected()
        current = bool(item and item["id"] == self.active)
        self.query_one("#session-use", Button).disabled = (
            not item or current or bool(item.get("archived"))
        )
        archive = self.query_one("#session-archive", Button)
        archive.disabled = not item or current
        archive.label = "Restaurar" if item and item.get("archived") else "Arquivar"
        self.query_one("#session-picker-info", Static).update(
            safe_preview(item["title"] + (" · conversa atual" if current else ""))
            if item
            else "Nenhuma conversa encontrada."
        )

    def on_input_changed(self):
        self.refresh_items()

    def on_checkbox_changed(self):
        self.refresh_items()

    def on_option_list_option_highlighted(self, event):
        event.stop()
        self.update_selection()

    def action_next(self):
        self.navigate(1)

    def action_previous(self):
        self.navigate(-1)

    def navigate(self, offset):
        options = self.query_one(OptionList)
        if options.option_count:
            options.highlighted = ((options.highlighted or 0) + offset) % options.option_count

    def on_input_submitted(self, event):
        event.stop()
        self.use()

    def on_option_list_option_selected(self, event):
        event.stop()
        self.use()

    def use(self):
        item = self.selected()
        if item and item["id"] != self.active and not item.get("archived"):
            self.dismiss(item["id"])

    def on_button_pressed(self, event):
        event.stop()
        action = event.button.id
        if action == "session-use":
            self.use()
        elif action == "session-new":
            self.dismiss("new")
        elif action == "session-back":
            self.action_back()
        elif action == "session-archive":
            item = self.selected()
            if item and item["id"] != self.active:
                try:
                    self.catalog.archive(item["id"], archived=not item.get("archived"))
                    self.refresh_items()
                except (OSError, ValueError) as exc:
                    self.query_one("#session-picker-info", Static).update(safe_preview(str(exc)))

    def action_back(self):
        self.dismiss(None)
