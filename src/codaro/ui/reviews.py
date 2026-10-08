from __future__ import annotations

import json
import shlex
from pathlib import Path
from types import SimpleNamespace

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Static, TextArea

from codaro.edits import EditProposal
from codaro.index import safe_preview
from codaro.policies import ApprovalPolicy


class EditReview(ModalScreen[str]):
    DEFAULT_CSS = """
    EditReview { align: center middle; background: #000000 65%; }
    #review { width: 95%; height: 90%; border: round $accent; background: $surface; }
    #review-title { height: auto; padding: 1 2; color: $accent; text-style: bold; }
    #review-diff { height: 1fr; padding: 0 1; }
    #review-actions { height: auto; align-horizontal: center; padding: 1; }
    #review-actions Button { margin: 0 1; min-width: 10; }
    """
    BINDINGS = [Binding("escape", "back", "Voltar")]

    def __init__(self, proposal: EditProposal):
        super().__init__()
        self.proposal = proposal

    def compose(self) -> ComposeResult:
        with Vertical(id="review"):
            yield Static(
                safe_preview(
                    f"Revisar · {getattr(self.proposal, 'operation', 'Conjunto')} · "
                    f"{self.proposal.path}\n{self.proposal.reason}"
                ),
                id="review-title",
                markup=False,
            )
            with VerticalScroll(id="review-diff"):
                yield Static(
                    Syntax(
                        safe_preview(self.proposal.diff), "diff", theme="monokai", word_wrap=True
                    )
                )
            with Horizontal(id="review-actions"):
                yield Button("Aplicar", variant="success", id="apply-edit")
                yield Button("Rejeitar", variant="error", id="reject-edit")
                yield Button("Voltar", id="back-edit")

    def on_mount(self):
        # Enter must never approve a patch just because the modal appeared.
        self.query_one("#back-edit", Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss({"apply-edit": "apply", "reject-edit": "reject"}.get(event.button.id, "back"))

    def action_back(self):
        self.dismiss("back")


class ChangesReview(EditReview):
    def __init__(self, proposals):
        combined = SimpleNamespace(
            path=f"{len(proposals)} arquivo(s)",
            reason="Aprovar aplica este conjunto; o agente continuará para validar.",
            diff="\n".join(
                f"{item.operation} · {item.path} · {item.reason}\n{item.diff}" for item in proposals
            ),
        )
        super().__init__(combined)


class ScopeReview(ModalScreen[dict | None]):
    DEFAULT_CSS = """
    ScopeReview { align: center middle; }
    #scope-review { width: 95%; height: 90%; border: round $warning;
                    background: $surface; padding: 0 1; }
    #scope-fields { height: 1fr; }
    #scope-task { max-height: 2; }
    #scope-review Static { height: auto; }
    #scope-review TextArea { height: 4; }
    #scope-review Horizontal { height: 3; }
    #scope-review Button { width: 1fr; min-width: 8; padding: 0; }
    #scope-json { display: none; }
    """
    BINDINGS = [Binding("escape", "reject", "Cancelar")]

    def __init__(self, task):
        super().__init__()
        self.scope_task = task

    def compose(self):
        with Vertical(id="scope-review"):
            yield Static("Autorizar escopo desta tarefa/sessão", markup=False)
            yield Static(
                safe_preview(self.scope_task["id"] + " · " + self.scope_task["objective"]),
                id="scope-task",
                markup=False,
            )
            yield Static(
                "Permite criar/alterar/remover/renomear nos caminhos e executar os "
                "comandos exatos. Fora do escopo exige nova aprovação.",
                markup=False,
            )
            with VerticalScroll(id="scope-fields"):
                yield Static("Caminhos autorizados · um por linha; inclui subpastas", markup=False)
                yield TextArea("src\ntests", id="scope-paths")
                yield Static(
                    "Comandos exatos · um por linha; aspas preservam argumentos", markup=False
                )
                yield TextArea("", id="scope-commands")
                yield Static("", id="scope-preview", markup=False)
                yield Checkbox("Editar JSON avançado", id="scope-advanced")
                yield TextArea('{"paths": ["src", "tests"], "commands": []}', id="scope-json")
            yield Static(
                "Revogue com /permissions action. Fora do escopo exige nova aprovação.",
                markup=False,
            )
            yield Static("", id="scope-error", markup=False)
            with Horizontal():
                yield Button("Autorizar escopo", id="grant-scope", variant="warning")
                yield Button("Cancelar", id="cancel-scope")

    def on_mount(self):
        self.query_one("#cancel-scope", Button).focus()

    def on_checkbox_changed(self, event):
        if event.checkbox.id == "scope-advanced":
            editor = self.query_one("#scope-json", TextArea)
            editor.display = event.value
            if event.value:
                from codaro.ui.dialogs import ScopeFields

                try:
                    value = ScopeFields.parse(
                        self.query_one("#scope-paths", TextArea).text,
                        self.query_one("#scope-commands", TextArea).text,
                    )
                    editor.text = json.dumps(value, ensure_ascii=False, indent=2)
                except ValueError as exc:
                    self.query_one("#scope-error", Static).update(str(exc))

    def on_text_area_changed(self, event):
        if event.text_area.id in {"scope-paths", "scope-commands"}:
            paths = self.query_one("#scope-paths", TextArea).text
            commands = self.query_one("#scope-commands", TextArea).text
            self.query_one("#scope-preview", Static).update(
                "Escopo: "
                + ", ".join(paths.splitlines())
                + " · comandos: "
                + str(len(commands.splitlines()))
            )

    def action_reject(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        if event.button.id != "grant-scope":
            self.dismiss(None)
            return
        try:
            text = self.query_one("#scope-json", TextArea).text
            if len(text) > 8000:
                raise ValueError("Escopo grande demais.")
            if self.query_one("#scope-advanced", Checkbox).value:
                value = json.loads(text)
            else:
                from codaro.ui.dialogs import ScopeFields

                value = ScopeFields.parse(
                    self.query_one("#scope-paths", TextArea).text,
                    self.query_one("#scope-commands", TextArea).text,
                )
            if not isinstance(value, dict) or set(value) != {"paths", "commands"}:
                raise ValueError("Use paths e commands.")
            ApprovalPolicy().grant("preview", value["paths"], value["commands"])
        except (ValueError, TypeError, RecursionError) as exc:
            self.query_one("#scope-error", Static).update(safe_preview(str(exc)))
            return
        self.dismiss(value)


class CommandReview(ModalScreen[bool]):
    DEFAULT_CSS = """
    CommandReview { align: center middle; background: #000000 65%; }
    #command-review { width: 95%; max-width: 100; height: 18; max-height: 90%; padding: 0 1;
                      border: round $warning; background: $surface; }
    #command-fields { height: 1fr; }
    #command-review Static { height: auto; margin-bottom: 1; }
    #command-review Horizontal { height: 3; }
    #command-review Button { width: 1fr; min-width: 8; padding: 0; }
    """
    BINDINGS = [Binding("escape", "reject", "Rejeitar", priority=True)]

    def __init__(self, root: Path, argv: list[str], timeout: int):
        super().__init__()
        self.root, self.argv, self.timeout = root, argv, timeout

    def compose(self) -> ComposeResult:
        with Vertical(id="command-review"):
            yield Static("Autorizar comando", markup=False)
            with VerticalScroll(id="command-fields"):
                yield Static(
                    safe_preview(
                        f"Diretório: {self.root}\nTimeout: {self.timeout}s\n\n"
                        + shlex.join(self.argv)
                    ),
                    markup=False,
                )
                yield Static(
                    "O comando pode alterar arquivos. A aprovação vale só para esta execução.",
                    markup=False,
                )
            with Horizontal():
                yield Button("Executar", id="approve-command", variant="warning")
                yield Button("Rejeitar", id="reject-command")

    def on_mount(self):
        self.query_one("#reject-command", Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "approve-command")

    def action_reject(self):
        self.dismiss(False)


class ExternalToolReview(CommandReview):
    """Dedicated approval text: an external call is not a shell command."""

    def __init__(self, root, source, name, arguments):
        super().__init__(root, [], 30)
        self.source, self.tool_name, self.arguments = source, name, arguments

    def compose(self) -> ComposeResult:
        with Vertical(id="command-review"):
            yield Static("Autorizar ferramenta externa", markup=False)
            with VerticalScroll(id="command-fields"):
                yield Static(
                    safe_preview(
                        self.source
                        + " / "
                        + self.tool_name
                        + "\n"
                        + json.dumps(self.arguments, ensure_ascii=False, indent=2)
                    ),
                    markup=False,
                )
                yield Static(
                    "Pode produzir efeitos externos. Aprovação válida para esta chamada.",
                    markup=False,
                )
            with Horizontal():
                yield Button("Autorizar", id="approve-command", variant="warning")
                yield Button("Rejeitar", id="reject-command")
