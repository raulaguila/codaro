"""Visible, session-owned commands and non-blocking cancellation."""

import shlex

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from codaro.index import safe_preview
from codaro.ui.screen import DialogScreen

STATES = {
    "running": "Em execução",
    "completed": "Concluído",
    "failed": "Falhou",
    "cancelled": "Interrompido",
}


class JobStrip(Horizontal):
    def compose(self):
        yield Button("", id="jobs-open")
        yield Button("Parar", id="jobs-stop")


class JobsDialog(DialogScreen):
    BINDINGS = [("escape", "close", "Fechar")]

    def __init__(self, manager):
        super().__init__()
        self.manager, self.identifiers, self.output_offset = manager, [], 0
        self.selected = None

    def compose(self):
        with Vertical(id="jobs-dialog"):
            yield Static("Comandos desta conversa", classes="dialog-title", markup=False)
            yield OptionList(id="jobs-list")
            yield Static("", id="job-state", markup=False)
            yield TextArea("", id="job-output", read_only=True, soft_wrap=False)
            with Horizontal(classes="dialog-actions"):
                yield Button("Anterior", id="job-previous")
                yield Button("Mais saída", id="job-next")
                yield Button("Parar comando", id="job-stop")
                yield Button("Fechar", id="jobs-close")

    def on_mount(self):
        self.refresh_jobs()
        self.call_after_refresh(self.refresh_jobs)
        self.query_one(OptionList).focus()
        self.set_interval(0.5, self.refresh_jobs)

    def refresh_jobs(self):
        if self.manager is not self.app.agent.jobs:
            self.dismiss(None)
            return
        identifiers = list(reversed(list(self.manager.jobs)))
        options = self.query_one(OptionList)
        if identifiers != self.identifiers:
            self.identifiers = identifiers
            options.clear_options()
            options.add_options([Option("", id=key) for key in identifiers])
            options.highlighted = 0 if identifiers else None
        for index, key in enumerate(identifiers):
            job = self.manager.jobs.get(key)
            if job is None:
                continue
            state = (
                "Parando"
                if job.cancelled.is_set() and job.state == "running"
                else STATES[job.state]
            )
            label = Text(
                safe_preview(state + " · " + shlex.join([job.mask(part) for part in job.argv]))
            )
            label.truncate(max(10, options.content_size.width - 2), overflow="ellipsis")
            options.replace_option_prompt_at_index(index, label)
        self.refresh_output()

    def refresh_output(self):
        options = self.query_one(OptionList)
        if options.highlighted is None:
            self.query_one("#job-state", Static).update("Nenhum comando iniciado nesta conversa.")
            for identifier in ("job-previous", "job-next", "job-stop"):
                self.query_one("#" + identifier, Button).disabled = True
            return
        key = options.get_option_at_index(options.highlighted).id
        if key not in self.manager.jobs:
            return
        if key != self.selected:
            self.selected, self.output_offset = key, 0
        result = self.manager.poll(key, offset=self.output_offset)
        job = self.manager.jobs[key]
        stopping = job.cancelled.is_set() and not job.done.is_set()
        state = "Parando…" if stopping else STATES[result["state"]]
        directory = result["cwd"] if result["cwd"] != "." else "raiz do projeto"
        self.query_one("#job-state", Static).update(
            safe_preview(
                f"{state} · diretório: {directory}"
                f" · página {self.output_offset // 4000 + 1}"
                + (f" · código {result['exit_code']}" if result["exit_code"] is not None else "")
            )
        )
        self.query_one("#job-state").tooltip = safe_preview(shlex.join(result["argv"]))
        output = self.query_one(TextArea)
        text = safe_preview(result["output"] or result.get("error", ""))
        if output.text != text:
            scroll = output.scroll_offset
            output.load_text(text)
            output.scroll_to(x=scroll.x, y=scroll.y, animate=False, immediate=True)
        self.query_one("#job-previous", Button).disabled = not self.output_offset
        self.query_one("#job-next", Button).disabled = (
            not result["truncated"] or not result["output"]
        )
        self.query_one("#job-stop", Button).disabled = job.done.is_set() or stopping

    def on_option_list_option_highlighted(self, event):
        event.stop()
        self.refresh_output()

    def on_button_pressed(self, event):
        event.stop()
        identifier = event.button.id
        if identifier == "jobs-close":
            self.action_close()
        elif self.selected:
            if identifier == "job-stop":
                self.manager.request_cancel([self.selected])
            elif identifier == "job-next":
                result = self.manager.poll(self.selected, offset=self.output_offset)
                self.output_offset = result["next_offset"] or self.output_offset
            elif identifier == "job-previous":
                self.output_offset = max(0, self.output_offset - 4000)
            self.refresh_jobs()

    def action_close(self):
        self.dismiss(None)


def sync_jobs(app):
    if not app.query("#job-strip"):
        return
    identifiers = app.agent.jobs.active()
    strip = app.query_one(JobStrip)
    strip.display = bool(identifiers)
    if identifiers:
        stopping = all(app.agent.jobs.jobs[key].cancelled.is_set() for key in identifiers)
        count = len(identifiers)
        label = (
            "Parando comandos…"
            if stopping
            else f"{count} {'comando' if count == 1 else 'comandos'} em execução ▸"
        )
        app.query_one("#jobs-open", Button).label = label
        app.query_one("#jobs-stop", Button).disabled = stopping
        if stopping:
            app.query_one("#status", Static).update("Parando comandos · aguardando término…")
    pending = getattr(app, "stopping_jobs", None)
    if pending:
        manager, keys = pending
        if manager is not app.agent.jobs:
            app.stopping_jobs = None
        elif all(manager.jobs[key].done.is_set() for key in keys if key in manager.jobs):
            app.stopping_jobs = None
            if not app.busy:
                app.query_one("#status", Static).update(
                    "Execução interrompida · comandos encerrados"
                )


def cancel_jobs(app):
    identifiers = app.agent.jobs.request_cancel()
    if identifiers:
        app.stopping_jobs = (app.agent.jobs, identifiers)
    sync_jobs(app)
