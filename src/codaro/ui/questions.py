"""Question, vertical choices and free response share the main composer."""

from textual.containers import Vertical
from textual.widgets import OptionList, Static

from codaro.index import safe_preview


class QuestionDock(Vertical):
    def compose(self):
        yield Static("? Pergunta", classes="question-label", markup=False)
        yield Static("", id="question-title", markup=False)
        yield Static("", id="question-why", markup=False)
        yield OptionList(id="question-options")

    def set_question(self, question):
        self.question = question
        self.display = bool(question)
        if not question:
            return
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

    def set_free_answer(self, free):
        self.set_class(free, "free-answer")
        self.free_answer = free
        self.refresh_choices()

    def refresh_choices(self):
        question = getattr(self, "question", None)
        if not question:
            return
        options = self.query_one(OptionList)
        for index, choice in enumerate(question["choices"]):
            mark = (
                "◉ "
                if index == options.highlighted and not getattr(self, "free_answer", False)
                else "○ "
            )
            suffix = " · recomendada" if index == question.get("recommended") else ""
            options.replace_option_prompt_at_index(index, mark + safe_preview(choice) + suffix)

    def on_option_list_option_highlighted(self, event):
        event.stop()
        self.refresh_choices()

    def navigate(self, direction):
        options = self.query_one(OptionList)
        if options.option_count:
            options.highlighted = ((options.highlighted or 0) + direction) % options.option_count

    def answer(self, text, question):
        if text.strip():
            return text.strip()
        index = self.query_one(OptionList).highlighted
        return question["choices"][index] if index is not None and question["choices"] else ""

    def on_option_list_option_selected(self, event):
        event.stop()
        from codaro.ui.composer import Prompt

        self.app.query_one(Prompt).focus()
