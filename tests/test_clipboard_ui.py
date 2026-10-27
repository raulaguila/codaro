import asyncio
import threading

import pytest
from test_tui import UIModel, run_ui
from textual.widgets import OptionList, Static, TextArea

from codaro.agent import Agent
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.message_actions import CopyPreview, MessageActions

ANSWER = "Uma resposta com ação 🎯.\n\n```python\nprint('olá')\n```\n"


@pytest.mark.parametrize("option,expected", [(0, ANSWER), (1, "print('olá')\n")])
def test_answer_and_code_copy_use_native_clipboard_without_losing_draft(
    tmp_path, monkeypatch, option, expected
):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
    calls = []
    ui_thread = threading.get_ident()

    def native(text):
        assert threading.get_ident() != ui_thread
        calls.append(text)
        return True

    monkeypatch.setattr("codaro.ui.message_actions.copy_native", native)

    async def scenario():
        async with app.run_test() as pilot:
            app.active_question = "Explique"
            app.finish(ANSWER)
            app.query_one(Prompt).value = "Rascunho mantido"
            await pilot.press("ctrl+o")
            app.screen.query_one(OptionList).highlighted = option
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert calls == [expected]
            assert app.clipboard == expected
            assert not isinstance(app.screen, MessageActions)
            assert "Copiado para a área de transferência" in str(app.query_one("#status").render())
            assert app.query_one(Prompt).value == "Rascunho mantido"

    run_ui(scenario())


@pytest.mark.parametrize("size", [(40, 16), (80, 24), (120, 40)])
def test_terminal_fallback_is_honest_and_offers_manual_copy(tmp_path, monkeypatch, size):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
    monkeypatch.setattr("codaro.ui.message_actions.copy_native", lambda text: False)

    async def scenario():
        async with app.run_test(size=size) as pilot:
            app.active_question = "Explique"
            app.finish(ANSWER)
            app.query_one(Prompt).value = "Não perder"
            await pilot.press("ctrl+o")
            app.screen.query_one(OptionList).highlighted = 1
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            actions = app.screen
            assert isinstance(actions, MessageActions)
            assert app.clipboard == "print('olá')\n"
            assert "sem confirmação" in str(actions.query_one("#message-copy-status").render())
            manual = actions.query_one("#message-copy-manual")
            assert manual.display and manual.region.bottom <= size[1]
            await pilot.click("#message-copy-manual")
            assert isinstance(app.screen, CopyPreview)
            preview = app.screen.query_one(TextArea)
            assert preview.read_only and preview.text == "print('olá')\n"
            assert app.screen.query_one("#copy-preview-close").region.bottom <= size[1]
            await pilot.press("escape", "escape")
            assert app.query_one(Prompt).value == "Não perder"
            assert not app.agent.provider.started.is_set()

    run_ui(scenario())


def test_terminal_write_failure_keeps_text_available_for_manual_copy(tmp_path, monkeypatch):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
    monkeypatch.setattr("codaro.ui.message_actions.copy_native", lambda text: False)

    def terminal(text):
        raise OSError("Terminal indisponível")

    monkeypatch.setattr(app, "copy_to_clipboard", terminal)

    async def scenario():
        async with app.run_test() as pilot:
            app.active_question = "Explique"
            app.finish(ANSWER)
            await pilot.press("ctrl+o", "enter")
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, MessageActions)
            assert "Não foi possível" in str(app.screen.query_one("#message-copy-status").render())
            await pilot.click("#message-copy-manual")
            assert app.screen.query_one(TextArea).text == ANSWER

    run_ui(scenario())


def test_slow_clipboard_does_not_block_escape_or_discard_draft(tmp_path, monkeypatch):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
    started, release = threading.Event(), threading.Event()

    def native(text):
        started.set()
        release.wait(5)
        return True

    monkeypatch.setattr("codaro.ui.message_actions.copy_native", native)

    async def scenario():
        async with app.run_test() as pilot:
            app.active_question = "Explique"
            app.finish(ANSWER)
            app.query_one(Prompt).value = "Continuar digitando"
            await pilot.press("ctrl+o", "enter")
            try:
                assert await asyncio.to_thread(started.wait, 2)
                await pilot.press("escape")
                assert not isinstance(app.screen, MessageActions)
                assert app.query_one(Prompt).value == "Continuar digitando"
                assert "Copiado" not in str(app.query_one("#status", Static).render())
            finally:
                release.set()

    run_ui(scenario())
