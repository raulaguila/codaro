"""A framed, input-anchored menu; keyboard routing stays with the composer."""

from rich.text import Text
from textual.widgets import Static

from codaro.index import safe_preview


class CompletionMenu(Static):
    def show_options(self, options, selected, first, count, width, colors):
        last = min(first + count, len(options))
        commands = bool(options and options[0].value.startswith("/"))
        title = "Comandos" if commands else "Arquivos"
        self.border_title = f"{title} · {first + 1}–{last} de {len(options)}"
        content = Text()
        command_width = min(18, max(10, width // 3))
        for index in range(first, last):
            name, separator, description = safe_preview(options[index].label).partition(" · ")
            row = Text("› " if index == selected else "  ")
            name_text = Text(name)
            name_text.truncate(
                command_width if separator else width - 2, overflow="ellipsis", pad=bool(separator)
            )
            row.append_text(name_text)
            if separator:
                row.append("  " + description)
            row.truncate(width, overflow="ellipsis", pad=True)
            if index == selected:
                row.stylize(f"bold {colors['background']} on {colors['accent']}")
            if index != first:
                content.append("\n")
            content.append_text(row)
        self.styles.height = last - first + 2
        self.update(content)
