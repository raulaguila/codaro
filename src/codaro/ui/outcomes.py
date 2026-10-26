"""Outcome actions use recorded task data rather than claims in model text."""

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static


class TaskReceipt(Vertical):
    @staticmethod
    def has_evidence(task):
        return bool(
            any(v["revision"] == task["revision"] for v in task["validations"])
            or any(e["kind"] == "change" for e in task["events"])
        )

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
            f"{'✓' if passed == len(checks) else '!'} {passed}/{len(checks)} validações aprovadas"
            if checks
            else "Validação não registrada"
        )
        self.set_class(bool(checks) and passed == len(checks), "verified")
        self.set_class(bool(checks) and passed != len(checks), "failed")
        if files:
            label += f" · {len(files)} arquivo(s) alterado(s)"
        yield Static(label, markup=False)
        with Horizontal(classes="outcome-actions"):
            if files:
                yield Button("Ver alterações", id="recover-changes")
            yield Button("Nova conversa", id="outcome-new")


class EmptySearchActions(Horizontal):
    def compose(self):
        yield Button("Refinar a busca", id="refine-search")
        yield Button("Indicar arquivo", id="indicate-file")
