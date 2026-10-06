import asyncio
import threading

from textual.widgets import Input, Static

from codaro.agent import Agent
from codaro.provider import ModelError, Settings
from codaro.repository import Repository
from codaro.tui import CodaroApp


def run_ui(coroutine):
    # Some restricted runners cannot wake asyncio through its cross-thread socket.
    # A timer keeps the headless loop progressing, including executor shutdown.
    def loop_factory():
        loop = asyncio.new_event_loop()

        def heartbeat():
            if not loop.is_closed():
                loop.call_later(0.02, heartbeat)

        loop.call_later(0.02, heartbeat)
        return loop

    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(coroutine)


class UIModel:
    settings = Settings("http://localhost:11434/v1", "test-model")

    def __init__(self, failure=False, blocked=False):
        self.failure = failure
        self.blocked = blocked
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, tools=None):
        self.started.set()
        if self.blocked:
            self.release.wait(3)
        if self.failure:
            self.failure = False
            raise ModelError("API indisponível.")
        return {"content": "Resposta de teste."}


async def wait_ready(app, pilot):
    for _ in range(100):
        await pilot.pause(0.01)
        if not app.busy:
            return
    raise AssertionError("Chat não retornou ao estado pronto.")


def test_chat_submission_and_clear(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel())

    async def scenario():
        app = CodaroApp(agent)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            assert agent.turns
            assert not app.query_one(Input).disabled
            await pilot.press("ctrl+l")
            assert not agent.turns

    run_ui(scenario())


def test_chat_recovers_from_provider_error(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel(failure=True))

    async def scenario():
        app = CodaroApp(agent)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            assert not agent.turns
            assert not app.query_one(Input).disabled
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            assert agent.turns

    run_ui(scenario())


def test_chat_cancel_discards_inflight_response(tmp_path):
    model = UIModel(blocked=True)
    agent = Agent(Repository(tmp_path), model)

    async def scenario():
        app = CodaroApp(agent)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            assert app.busy
            assert model.started.is_set()
            await pilot.press("ctrl+x")
            assert app.cancelled.is_set()
            model.release.set()
            await wait_ready(app, pilot)
            assert not agent.turns
            assert not app.query_one(Input).disabled

    run_ui(scenario())


def test_narrow_terminal_hides_sidebar(tmp_path):
    async def scenario():
        app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
        async with app.run_test(size=(70, 25)) as pilot:
            await pilot.pause()
            assert app.screen.has_class("narrow")
            assert not app.query_one("#sidebar").display
            assert app.query_one("#status", Static)

    run_ui(scenario())


class StreamingUIModel(UIModel):
    def stream(self, messages, tools=None, on_delta=None, cancelled=None):
        self.started.set()
        on_delta("## Resposta\n\n**Parcial**")
        self.release.wait(3)
        if cancelled and cancelled.is_set():
            from codaro.provider import RequestCancelled

            raise RequestCancelled("Cancelada.")
        on_delta("\n\n```python\ndef f():\n    return True\n```")
        return {
            "content": "## Resposta\n\n**Parcial**\n\n```python\ndef f():\n    return True\n```"
        }


def test_chat_displays_partial_markdown_before_completion(tmp_path):
    from textual.widgets import Markdown

    model = StreamingUIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            assert app.busy
            assert "Parcial" in app.response_text
            assert app.reply is not None
            assert app.query(Markdown)
            model.release.set()
            await wait_ready(app, pilot)
            await pilot.pause(0.1)
            assert len(app.query("MarkdownFence")) == 1
            assert app.rendered_text.count("Parcial") == 1

    run_ui(scenario())


def test_chat_cancel_removes_partial_response(tmp_path):
    model = StreamingUIModel()
    agent = Agent(Repository(tmp_path), model)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            assert "Parcial" in app.response_text
            await pilot.press("ctrl+x")
            model.release.set()
            await wait_ready(app, pilot)
            assert app.reply is None
            assert not app.response_text
            assert not agent.turns

    run_ui(scenario())


def test_tool_cards_visible_on_narrow_terminal(tmp_path):
    from codaro.agent import AgentEvent

    async def scenario():
        app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
        async with app.run_test(size=(70, 25)) as pilot:
            app.activity(
                AgentEvent("tool_end", "Ler símbolo", "auth.py · can_edit\n3 linhas", "success", 12)
            )
            await pilot.pause()
            assert len(app.query(".tool-card")) == 1
            assert app.screen.has_class("narrow")

    run_ui(scenario())


class EditUIModel(UIModel):
    def __init__(self):
        super().__init__()
        from test_agent import edit_responses

        self.responses = iter(edit_responses())

    def complete(self, messages, tools=None):
        return next(self.responses)


def test_chat_review_apply_and_reject(tmp_path):
    from codaro.tui import EditReview

    for decision in ("apply", "reject"):
        path = tmp_path / "code.py"
        path.write_text("x = 1\n")
        agent = Agent(Repository(tmp_path), EditUIModel(), allow_edits=True)
        app = CodaroApp(agent)

        async def scenario(app=app, agent=agent, path=path, decision=decision):
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press("o", "i", "enter")
                await wait_ready(app, pilot)
                await pilot.pause(0.1)
                proposal = agent.edits.pending[0]
                assert path.read_text() == "x = 1\n"
                await pilot.click(f"#review-{proposal.id}")
                assert isinstance(app.screen, EditReview)
                assert app.screen.focused.id == "back-edit"
                await pilot.click(f"#{decision}-edit")
                await wait_ready(app, pilot)
                assert proposal.state == ("applied" if decision == "apply" else "rejected")
                assert path.read_text() == ("x = 2\n" if decision == "apply" else "x = 1\n")
                assert not agent.edits.pending
                assert app.proposal_cards[proposal.id].query_one("Button").disabled

        run_ui(scenario())


def test_chat_review_conflict_preserves_new_content(tmp_path):
    path = tmp_path / "code.py"
    path.write_text("x = 1\n")
    agent = Agent(Repository(tmp_path), EditUIModel(), allow_edits=True)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            await pilot.pause(0.1)
            proposal = agent.edits.pending[0]
            path.write_text("x = 99\n")
            await pilot.click(f"#review-{proposal.id}")
            await pilot.click("#apply-edit")
            await wait_ready(app, pilot)
            assert proposal.state == "conflict"
            assert path.read_text() == "x = 99\n"
            assert not app.query_one(Input).disabled

    run_ui(scenario())


def test_chat_review_enter_returns_and_clear_rejects(tmp_path):
    path = tmp_path / "code.py"
    path.write_text("x = 1\n")
    agent = Agent(Repository(tmp_path), EditUIModel(), allow_edits=True)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(70, 30)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            await pilot.pause(0.1)
            proposal = agent.edits.pending[0]
            await pilot.click(f"#review-{proposal.id}")
            await pilot.press("enter")
            await pilot.pause()
            assert agent.edits.pending
            assert path.read_text() == "x = 1\n"
            await pilot.press("ctrl+l")
            assert proposal.state == "rejected"
            assert not agent.edits.pending

    run_ui(scenario())


def test_chat_pwd_is_local_and_reports_real_root(tmp_path):
    model = UIModel()
    agent = Agent(Repository(tmp_path), model)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(70, 30)) as pilot:
            app.query_one(Input).value = "/pwd"
            await pilot.press("enter")
            await pilot.pause()
            assert not model.started.is_set()
            assert not app.busy
            assert not agent.turns
            assert app.query_one(Input).value == ""
            assert any(
                str(tmp_path.resolve()) in str(widget.render()) for widget in app.query(".question")
            )

    run_ui(scenario())
