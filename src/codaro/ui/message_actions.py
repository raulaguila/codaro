"""Local transcript actions; file previews never grant edit evidence to the agent."""

import asyncio
import re

from textual import work
from textual.containers import Vertical
from textual.widgets import Button, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from codaro.clipboard import copy_native
from codaro.index import safe_preview
from codaro.ui.screen import DialogScreen


def cited_files(text, repository):
    candidates = re.findall(r"`([^`\n]+)`", text)
    candidates.extend(
        match[0] + ":" + match[1] for match in re.findall(r"([\w./-]+\.[\w]+):(\d+)", text)
    )
    if not candidates:
        return []
    allowed = {path.relative_to(repository.root).as_posix() for path in repository.files()}
    found = []
    for candidate in candidates[:200]:
        match = re.fullmatch(r"(.+?)(?::(\d+)(?:[-–]\d+)?)?", candidate)
        if match and match[1] in allowed:
            item = (match[1], int(match[2] or 1))
            if item not in found:
                found.append(item)
    return found[:12]


class FilePreview(DialogScreen):
    BINDINGS = [("escape", "close", "Fechar")]

    def __init__(self, path, line=1):
        super().__init__()
        self.path, self.line = path, line

    def compose(self):
        with Vertical(id="file-preview"):
            yield Static(
                safe_preview(self.path) + " · leitura local", classes="dialog-title", markup=False
            )
            try:
                content = self.app.agent.repository.read_text(self.path)
                content = self.app.agent.memory.redact(content)
            except (OSError, ValueError) as exc:
                content = str(exc)
            yield TextArea(
                content,
                read_only=True,
                show_line_numbers=True,
                soft_wrap=False,
                id="file-preview-code",
            )
            yield Button("Fechar", id="file-preview-close")

    def on_mount(self):
        code = self.query_one(TextArea)
        code.focus()
        code.move_cursor((min(max(0, self.line - 1), code.document.line_count - 1), 0))
        code.scroll_cursor_visible(animate=False)

    def action_close(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        self.action_close()


class MessageActions(DialogScreen):
    BINDINGS = [("escape", "close", "Voltar")]

    def __init__(self, text):
        super().__init__()
        self.text = text
        self.codes = re.findall(r"```([^\n]*)\n(.*?)```", text, flags=re.S)[:10]
        self.files = []
        self.copy_text = text

    def compose(self):
        try:
            self.files = cited_files(self.text, self.app.agent.repository)
        except (ValueError, OSError):
            self.files = []
        with Vertical(id="message-actions"):
            yield Static("Ações da resposta", classes="dialog-title", markup=False)
            yield OptionList(
                Option("Copiar resposta", id="answer"),
                *[
                    Option(f"Copiar código {i + 1} · {language.strip() or 'texto'}", id=f"code-{i}")
                    for i, (language, _) in enumerate(self.codes)
                ],
                *[
                    Option(safe_preview(f"Ver arquivo · {path}:{line}"), id=f"file-{i}")
                    for i, (path, line) in enumerate(self.files)
                ],
                id="message-action-list",
            )
            yield Static("", id="message-copy-status", markup=False)
            yield Button("Selecionar texto", id="message-copy-manual")
            yield Button("Voltar", id="message-actions-close")

    def on_mount(self):
        self.query_one(OptionList).focus()
        self.query_one(OptionList).highlighted = 0
        self.query_one("#message-copy-manual").display = False
        self.query_one("#message-copy-status").display = False
        self.fit()

    def on_resize(self):
        self.call_after_refresh(self.fit)

    def fit(self):
        extra = 3 if self.query_one("#message-copy-manual").display else 0
        self.query_one(OptionList).styles.max_height = max(
            1, min(14, int(self.size.height * 0.85) - 6 - extra)
        )

    def action_close(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == "message-copy-manual":
            self.app.push_screen(CopyPreview(self.copy_text))
            return
        self.action_close()

    def on_option_list_option_selected(self, event):
        event.stop()
        value = event.option.id
        if value.startswith("file-"):
            path, line = self.files[int(value[5:])]
            self.app.push_screen(FilePreview(path, line))
            return
        self.copy_response(self.text if value == "answer" else self.codes[int(value[5:])][1])

    @work(exclusive=True)
    async def copy_response(self, text):
        self.copy_text = text
        options = self.query_one(OptionList)
        options.disabled = True
        status = self.query_one("#message-copy-status", Static)
        status.display = True
        status.update("Copiando…")
        native = await asyncio.to_thread(copy_native, text)
        if not self.is_mounted:
            return
        # Keep Textual's internal paste buffer synchronized and support SSH via OSC 52.
        try:
            self.app.copy_to_clipboard(text)
            sent = True
        except (OSError, RuntimeError):
            sent = False
        if native:
            self.app.query_one("#status", Static).update("Copiado para a área de transferência")
            self.action_close()
            return
        options.disabled = False
        status.update(
            "Cópia enviada ao terminal, sem confirmação. Se não colar, use Selecionar texto."
            if sent
            else "Não foi possível copiar automaticamente. Use Selecionar texto."
        )
        self.query_one("#message-copy-manual").display = True
        self.query_one("#message-copy-manual").focus()
        self.fit()


class CopyPreview(DialogScreen):
    BINDINGS = [("escape", "close", "Voltar")]

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self):
        with Vertical(id="copy-preview"):
            yield Static("Texto para copiar", classes="dialog-title")
            yield Static(
                "Selecione com o mouse (Shift pode ser necessário) e use o atalho de copiar "
                "do seu terminal.",
                markup=False,
            )
            yield TextArea(self.text, read_only=True, soft_wrap=True, id="copy-preview-text")
            yield Button("Voltar", id="copy-preview-close")

    def on_mount(self):
        self.query_one(TextArea).focus()

    def action_close(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        event.stop()
        self.action_close()
