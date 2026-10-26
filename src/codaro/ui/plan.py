"""Persistent task projection next to the composer, independent of chat turns."""

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static

from codaro.index import safe_preview


class ExecutionPlan(Vertical):
    def __init__(self):
        super().__init__(id="execution-plan")
        self.task_id = None
        self.expanded = True

    def compose(self):
        with Horizontal(id="plan-heading"):
            yield Button("Plano", id="toggle-plan")
            yield Button("Cancelar plano", id="cancel-plan")
        with VerticalScroll(id="plan-steps"):
            yield Static("", id="plan-list", markup=False)
        yield Button("Executar plano", id="execute-plan")

    def set_task(self, task, *, busy=False):
        active = bool(task and task["plan"] and task["state"] not in {"completed", "cancelled"})
        self.display = active
        if not active:
            return
        if task["id"] != self.task_id:
            self.task_id, self.expanded = task["id"], True
        steps = task["plan"]
        done = sum(step["state"] == "done" for step in steps)
        current = next((step["title"] for step in steps if step["state"] == "doing"), "")
        if not current:
            current = next((step["title"] for step in steps if step["state"] == "todo"), "")
        arrow = "▾" if self.expanded else "▸"
        label = f"{arrow} Plano · {done}/{len(steps)} concluídas"
        if current:
            label += " · " + current
        self.query_one("#toggle-plan", Button).label = safe_preview(label)
        symbols = {"todo": "○", "doing": "◌", "done": "✓"}
        from rich.text import Text

        colors = self.app.get_css_variables()
        content = Text()
        for index, step in enumerate(steps):
            color = colors[
                {"done": "success", "doing": "accent", "todo": "text-muted"}[step["state"]]
            ]
            if index:
                content.append("\n")
            content.append(symbols[step["state"]] + " ", style=color)
            content.append(
                safe_preview(step["title"]),
                style=colors["foreground"] if step["state"] == "doing" else colors["text-muted"],
            )
        self.query_one("#plan-list", Static).update(content)
        self.query_one("#plan-steps").display = self.expanded
        execute = self.query_one("#execute-plan", Button)
        execute.display = task["state"] == "planned"
        execute.disabled = busy

    def toggle(self):
        self.expanded = not self.expanded

    def fit(self, height, *, answering=False):
        self.query_one("#plan-steps").styles.max_height = (
            1 if answering and height < 25 else 3 if height < 25 or answering else 5
        )


class PlanProposal(Vertical):
    """Review in the chat, while the persistent progress list stays by the input."""

    def __init__(self, task):
        super().__init__(classes="plan-proposal")
        self.plan_task = task

    def compose(self):
        yield Static("Codaro", classes="speaker", markup=False)
        yield Static(safe_preview(self.plan_task["objective"]), markup=False)
        for index, step in enumerate(self.plan_task["plan"], 1):
            yield Static(f"{index}. {safe_preview(step['title'])}", markup=False)
        yield Static(
            "Planejar não aplica mudanças. A execução segue as permissões da conversa.",
            classes="muted",
            markup=False,
        )
        with Horizontal(classes="outcome-actions"):
            yield Button("Executar com revisão", id="plan-start", variant="primary")
            yield Button("Ajustar o plano", id="plan-adjust")

    def on_button_pressed(self, event):
        event.stop()
        current = self.app.agent.tasks.current()
        if (
            self.app.busy
            or not current
            or (
                current["id"] != self.plan_task["id"]
                or current["plan_revision"] != self.plan_task["plan_revision"]
                or current["state"] in {"completed", "cancelled"}
            )
        ):
            self.app.query_one("#status", Static).update("Revise o plano atual com /plan")
            return
        self.app.plan_reviewed("execute" if event.button.id == "plan-start" else "adjust")
