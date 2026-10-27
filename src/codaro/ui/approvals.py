"""Thread-to-UI approval requests; cancellation never implies consent."""

import threading

from textual.widgets import Static

from codaro.ui.reviews import ChangesReview, CommandReview, ExternalToolReview


def approve_command(app, argv, timeout, cancelled, *, cwd="."):
    decision, ready = [], threading.Event()

    def show():
        dialog = CommandReview(app.agent.repository.root / cwd, argv, timeout)
        app.approval_dialog = dialog
        app.query_one("#status", Static).update("Aguardando aprovação do comando")

        def resolved(value):
            decision.append(value)
            app.approval_dialog = None
            ready.set()

        app.push_screen(dialog, resolved)

    app.deliver(show)
    while not ready.wait(0.05):
        if not app.is_running or cancelled is not None and cancelled.is_set():
            app.deliver(app.dismiss_command)
            return False
    return bool(decision and decision[0])


def approve_external(app, source, name, arguments, cancelled):
    decision, ready = [], threading.Event()

    def show():
        dialog = ExternalToolReview(app.agent.repository.root, source, name, arguments)
        app.approval_dialog = dialog
        app.query_one("#status", Static).update("Aguardando aprovação da ferramenta externa")

        def resolved(value):
            decision.append(value)
            app.approval_dialog = None
            ready.set()

        app.push_screen(dialog, resolved)

    app.deliver(show)
    while not ready.wait(0.05):
        if not app.is_running or cancelled is not None and cancelled.is_set():
            app.deliver(app.dismiss_command)
            return False
    return bool(decision and decision[0])


def approve_changes(app, proposals, cancelled):
    decision, ready = [], threading.Event()

    def show():
        dialog = ChangesReview(proposals)
        app.approval_dialog = dialog
        app.query_one("#status", Static).update("Aguardando revisão das alterações")

        def resolved(value):
            decision.append(value == "apply")
            app.approval_dialog = None
            ready.set()

        app.push_screen(dialog, resolved)

    app.deliver(show)
    while not ready.wait(0.05):
        if not app.is_running or cancelled is not None and cancelled.is_set():
            app.deliver(app.dismiss_command)
            return False
    return bool(decision and decision[0])
