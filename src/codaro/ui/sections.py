"""Process disclosures preserve the reader's viewport across phase transitions."""

from textual.widgets import Collapsible


class ProcessSection(Collapsible):
    def _watch_collapsed(self, collapsed):
        # Textual 6's default watcher also queues scroll_visible. That jumps the
        # transcript when a model/tool transition folds a section during reading.
        # Keep its state/events, without changing the user's scroll position.
        self._update_collapsed(collapsed)
        self.post_message(self.Collapsed(self) if collapsed else self.Expanded(self))
