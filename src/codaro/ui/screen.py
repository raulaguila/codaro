"""Resize notifications for foreground and background workspace layout."""

from typing import TypeVar

from textual.screen import ModalScreen, Screen

Result = TypeVar("Result")


class WorkspaceScreen(Screen):
    def on_resize(self):
        self.app.call_after_refresh(self.app.sync_layout)


class DialogScreen(ModalScreen[Result]):
    def on_resize(self):
        self.app.call_after_refresh(self.app.sync_layout)
