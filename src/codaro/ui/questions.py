"""Question, vertical choices and free response share the main composer."""

from rich.cells import cell_len
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, OptionList, Static

from codaro.index import safe_preview


class QuestionDock(Vertical):
    def compose(self):
        yield Static("? Pergunta", classes="question-label", markup=False)
        with Horizontal(id="question-heading"):
            yield Static("", id="question-title", markup=False)
            yield Button("Detalhes", id="question-details")
        yield Static("", id="question-why", markup=False)
        yield OptionList(id="question-options")

    def set_question(self, question):
        self.question = question
        self.display = bool(question)
        if not question:
            return
        self.set_paused(False)
        self.query_one("#question-title", Static).update(safe_preview(question["question"]))
        self.query_one("#question-why", Static).update(
            safe_preview(question.get("explanation", ""))
        )
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options(
            [
                safe_preview(choice)
                + (" · recomendada" if i == question.get("recommended") else "")
                for i, choice in enumerate(question["choices"])
            ]
        )
        options.display = bool(question["choices"])
        options.styles.height = len(question["choices"])
        if question["choices"]:
            options.highlighted = question.get("recommended", 0)
        self.set_free_answer(False)
        self.call_after_refresh(self.fit)

    def on_resize(self):
        if getattr(self, "question", None):
            self.call_after_refresh(self.fit)

    def fit(self):
        if not getattr(self, "question", None) or not self.is_attached:
            return
        width = max(10, self.content_size.width - 12)
        lines = Text(safe_preview(self.question["question"])).wrap(self.app.console, width)
        maximum = 1 if self.app.size.height < 25 else 2
        if len(lines) > maximum:
            lines[maximum - 1].truncate(max(1, width - 1))
            lines[maximum - 1].append("…")
        self.query_one("#question-title", Static).update(Text("\n").join(lines[:maximum]))
        self.refresh_choices()

    def on_button_pressed(self, event):
        if event.button.id != "question-details":
            return
        event.stop()
        from codaro.ui.composer import Prompt
        from codaro.ui.details import DetailsDialog

        question = self.question
        self.app.push_screen(
            DetailsDialog(
                "Detalhes da pergunta",
                [
                    ("Pergunta", question["question"]),
                    ("Explicação", question.get("explanation") or "Sem explicação adicional"),
                    *[(f"Opção {i + 1}", choice) for i, choice in enumerate(question["choices"])],
                ],
            ),
            lambda _: self.app.query_one(Prompt).focus(),
        )

    def set_paused(self, paused):
        self.query_one(".question-label", Static).update(
            "Pergunta pendente · tarefa pausada" if paused else "? Pergunta"
        )
        self.set_class(paused, "paused")

    def set_free_answer(self, free):
        self.set_class(free, "free-answer")
        self.free_answer = free
        self.refresh_choices()

    def refresh_choices(self):
        question = getattr(self, "question", None)
        if not question:
            return
        options = self.query_one(OptionList)
        width = max(1, options.content_size.width - 2)
        expanded = self.app.size.height >= 25 and not getattr(self, "free_answer", False)
        for index, choice in enumerate(question["choices"]):
            mark = (
                "◉ "
                if index == options.highlighted and not getattr(self, "free_answer", False)
                else "○ "
            )
            suffix = " · recomendada" if index == question.get("recommended") else ""
            selected = index == options.highlighted
            rows = 2 if expanded and selected and cell_len(choice) > width else 1
            label = Text(
                mark
                + middle_ellipsis(
                    safe_preview(choice).replace("\n", " "),
                    max(1, width * rows - cell_len(mark + suffix)),
                )
                + suffix
            )
            if rows == 2:
                label = Text("\n").join(label.wrap(self.app.console, width)[:2])
            else:
                label.truncate(width, overflow="ellipsis")
            options.replace_option_prompt_at_index(index, label)
        extra = int(expanded and any(cell_len(choice) > width for choice in question["choices"]))
        options.styles.height = len(question["choices"]) + extra

    def on_option_list_option_highlighted(self, event):
        event.stop()
        self.refresh_choices()

    def navigate(self, direction):
        options = self.query_one(OptionList)
        if options.option_count:
            options.highlighted = ((options.highlighted or 0) + direction) % options.option_count
            options.scroll_visible(animate=False)

    def answer(self, text, question):
        if text.strip():
            return text.strip()
        index = self.query_one(OptionList).highlighted
        return question["choices"][index] if index is not None and question["choices"] else ""

    def on_option_list_option_selected(self, event):
        event.stop()
        from codaro.ui.composer import Prompt

        self.app.query_one(Prompt).focus()


def middle_ellipsis(value, width):
    """Keep distinguishing endings visible when choices share a long prefix."""
    if cell_len(value) <= width:
        return value
    if width < 7:
        text = Text(value)
        text.truncate(width, overflow="ellipsis")
        return text.plain
    left = Text(value)
    left.truncate((width - 3) // 2, overflow="crop")
    tail_width = width - 3 - cell_len(left.plain)
    tail = value[-tail_width:]
    while cell_len(tail) > tail_width:
        tail = tail[1:]
    return left.plain + " … " + tail
