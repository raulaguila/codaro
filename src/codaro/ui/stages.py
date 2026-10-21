"""One quiet progress area per turn, with details opened only by the user."""

from rich.text import Text
from textual.containers import Vertical
from textual.widgets import Collapsible, Static

from codaro.index import safe_preview
from codaro.ui.widgets import ActivityGroup


class TurnStages(Vertical):
    def __init__(self, details):
        super().__init__(details, Static("", classes="live-steps"), classes="turn-stages")
        self.details = details
        self.completed = ""
        self.current = ""
        self.running = True

    def progress(self, current="", completed=""):
        self.current = safe_preview(current.split("\n", 1)[0])[:120]
        if completed:
            self.completed = safe_preview(completed.split("\n", 1)[0])[:120]
        self.refresh_summary()

    def refresh_summary(self):
        if not self.is_attached:
            return
        summary = self.query_one(".live-steps", Static)
        summary.display = (
            self.running and self.details.collapsed and bool(self.current or self.completed)
        )
        colors = self.app.get_css_variables()
        content = Text()
        if self.completed:
            content.append("✓ ", style=colors["success"])
            content.append(self.completed)
        if self.current:
            if content:
                content.append("\n")
            content.append("◌ ", style=colors["accent"])
            content.append(self.current)
        summary.update(content)

    def on_mount(self):
        self.refresh_summary()

    def on_collapsible_expanded(self, event: Collapsible.Expanded):
        if event.collapsible is self.details:
            self.refresh_summary()

    def on_collapsible_collapsed(self, event: Collapsible.Collapsed):
        if event.collapsible is self.details:
            self.refresh_summary()

    def finish(self):
        self.running = False
        self.refresh_summary()


def ensure_stages(app):
    if app.activity_group is None:
        app.activity_group = ActivityGroup()
        app.mount_message(TurnStages(app.activity_group))
    return app.activity_group


def progress(app, current="", completed=""):
    details = ensure_stages(app)

    def update():
        if details.is_attached and isinstance(details.parent, TurnStages):
            details.parent.progress(current, completed)

    if details.is_attached:
        update()
    else:
        app.call_after_refresh(update)
