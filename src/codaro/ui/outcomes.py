"""Outcome actions use recorded task data rather than claims in model text."""

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static


class TaskReceipt(Vertical):
    def __init__(self, task):
        super().__init__(classes="task-receipt")
        self.record = task

    def compose(self):
        checks = [
            result
            for result in self.record["validations"]
            if result["revision"] == self.record["revision"]
        ]
        passed = sum(result["exit_code"] == 0 and not result["timed_out"] for result in checks)
        files = {
            event["path"]
            for event in self.record["events"]
            if event["kind"] == "change" and event.get("path")
        }
        label = (
            f"✓ {passed}/{len(checks)} validações aprovadas"
            if checks
            else "Validação não registrada"
        )
        yield Static(label + f" · {len(files)} arquivo(s) alterado(s)", markup=False)
        with Horizontal(classes="outcome-actions"):
            yield Button("Ver alterações", id="recover-changes")
            yield Button("Nova conversa", id="outcome-new")


class EmptySearchActions(Horizontal):
    def compose(self):
        yield Button("Refinar a busca", id="refine-search")
        yield Button("Indicar arquivo", id="indicate-file")
