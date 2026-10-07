import asyncio
import threading

from textual.widgets import Static

from codaro.agent import Agent
from codaro.provider import ModelError, Settings
from codaro.repository import Repository
from codaro.tui import CodaroApp, Prompt


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
            self.release.wait(10)
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
            assert not app.query_one(Prompt).disabled
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
            assert not app.query_one(Prompt).disabled
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
            # Submission starts a worker; indexing/debug writes precede the model call.
            # Synchronize on the call instead of assuming Enter has started it already.
            try:
                assert await asyncio.to_thread(model.started.wait, 10)
                assert app.busy
                await pilot.press("ctrl+x")
                assert app.cancelled.is_set()
            finally:
                model.release.set()
            await wait_ready(app, pilot)
            assert not agent.turns
            assert not app.query_one(Prompt).disabled

    run_ui(scenario())


def test_narrow_terminal_uses_full_width_chat(tmp_path):
    async def scenario():
        app = CodaroApp(Agent(Repository(tmp_path), UIModel()))
        async with app.run_test(size=(70, 25)) as pilot:
            await pilot.pause()
            assert app.query_one("#conversation").region.width == 70
            assert not app.query("#sidebar, #repository, #activity")
            assert app.query_one("#status", Static)
            assert app.query_one("#session", Static).tooltip == str(tmp_path)

    run_ui(scenario())


class StreamingUIModel(UIModel):
    def stream(self, messages, tools=None, on_delta=None, cancelled=None):
        on_delta("## Resposta\n\n**Parcial**")
        self.started.set()
        self.release.wait(10)
        if cancelled and cancelled.is_set():
            from codaro.provider import RequestCancelled

            raise RequestCancelled("Cancelada.")
        on_delta("\n\n```python\ndef f():\n    return True\n```")
        return {
            "content": "## Resposta\n\n**Parcial**\n\n```python\ndef f():\n    return True\n```"
        }


def test_chat_keeps_streaming_preview_separate_from_final_markdown(tmp_path):
    from textual.widgets import Markdown

    model = StreamingUIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            assert await asyncio.to_thread(model.started.wait, 10)
            assert app.busy
            assert not app.response_text
            assert app.reply is None
            assert not app.query(Markdown)
            assert not app.query(".speaker")
            preview = app.generation_preview
            assert preview is not None
            assert "Parcial" in preview.text
            assert "não validada" in preview.title
            assert preview.collapsed
            assert app.activity_group.collapsed
            # Streaming remains available by explicitly expanding the investigation.
            app.activity_group.collapsed = False
            preview.collapsed = False
            await pilot.pause()
            assert preview.content.visible
            model.release.set()
            await wait_ready(app, pilot)
            await pilot.pause(0.1)
            assert len(app.query("MarkdownFence")) == 1
            assert app.rendered_text.count("Parcial") == 1
            assert preview.collapsed
            assert "geração concluída" in preview.title
            assert app.session_turns[-1][-1]["content"] == app.response_text

    run_ui(scenario())


def test_chat_cancel_removes_partial_response(tmp_path):
    model = StreamingUIModel()
    agent = Agent(Repository(tmp_path), model)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            assert await asyncio.to_thread(model.started.wait, 10)
            preview = app.generation_preview
            assert "Parcial" in preview.text
            await pilot.press("escape")
            model.release.set()
            await wait_ready(app, pilot)
            assert app.reply is None
            assert not app.response_text
            assert not agent.turns
            assert not app.session_turns
            assert "interrompida" in preview.title
            assert preview.collapsed

    run_ui(scenario())


def test_streamed_tool_iteration_never_becomes_a_chat_answer(tmp_path):
    from test_agent import call

    from codaro.tui import GenerationPreview

    class IteratingModel(UIModel):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.final_started = threading.Event()
            self.final_release = threading.Event()

        def stream(self, messages, tools=None, on_delta=None, cancelled=None):
            self.calls += 1
            if self.calls == 1:
                on_delta("Vou listar arquivos antes de responder.")
                self.started.set()
                self.release.wait(10)
                return call("list_files", {})
            on_delta("Não há arquivos permitidos.")
            self.final_started.set()
            self.final_release.wait(10)
            return {"content": "Não há arquivos permitidos."}

    model = IteratingModel()
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(70, 25)) as pilot:
            try:
                await pilot.press("o", "i", "enter")
                assert await asyncio.to_thread(model.started.wait, 10)
                first = app.generation_preview
                assert first is not None
                assert not app.query("Markdown, .speaker")
                model.release.set()
                assert await asyncio.to_thread(model.final_started.wait, 10)
                await pilot.pause()
                assert "intermediária" in first.title
                assert first.collapsed
                assert "Vou listar" in first.text
                assert len(app.query(GenerationPreview)) == 2
                assert not app.query("Markdown, .speaker")
                assert len(app.activity_group.events) == 1
            finally:
                model.release.set()
                model.final_release.set()
            await wait_ready(app, pilot)
            await pilot.pause()
            assert app.rendered_text == "Não há arquivos permitidos."
            assert len(app.query("Markdown")) == 1
            assert len(app.query(".speaker")) == 1
            assert len(app.query(GenerationPreview)) == 2
            assert all(preview.collapsed for preview in app.query(GenerationPreview))
            assert "Vou listar" not in app.session_turns[-1][-1]["content"]

    run_ui(scenario())


def test_failed_stream_retains_only_an_interrupted_preview(tmp_path):
    class FailingStream(UIModel):
        def stream(self, messages, tools=None, on_delta=None, cancelled=None):
            on_delta("Texto incompleto antes da falha.")
            raise ModelError("Conexão interrompida.")

    app = CodaroApp(Agent(Repository(tmp_path), FailingStream()))

    async def scenario():
        from codaro.tui import GenerationPreview

        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            await pilot.pause()
            preview = app.query_one(GenerationPreview)
            assert "interrompida" in preview.title
            assert preview.collapsed
            assert "Texto incompleto" in preview.text
            assert not app.query("Markdown, .speaker")
            assert not app.session_turns
            assert "Conexão interrompida" in str(app.query_one(".notice", Static).render())

    run_ui(scenario())


def test_rejected_stream_is_labeled_and_prior_answer_stays_visible(tmp_path):
    from codaro.tui import GenerationPreview

    class RepairingStream(UIModel):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def stream(self, messages, tools=None, on_delta=None, cancelled=None):
            self.calls += 1
            if self.calls == 1:
                return {"content": "Primeira resposta estável."}
            if self.calls == 2:
                text = '{"name":"list_files","parameters":{}}'
                on_delta(text)
                return {"content": text}
            on_delta("Segunda resposta corrigida.")
            self.started.set()
            self.release.wait(10)
            return {"content": "Segunda resposta corrigida."}

    model = RepairingStream()
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            original_reply = app.query_one("Markdown")
            try:
                await pilot.press("o", "i", "enter")
                assert await asyncio.to_thread(model.started.wait, 10)
                await pilot.pause()
                previews = list(app.query(GenerationPreview))
                assert any("rejeitada" in preview.title for preview in previews)
                assert len(app.query("Markdown")) == 1
                assert app.query_one("Markdown") is original_reply
                assert original_reply.is_attached
            finally:
                model.release.set()
            await wait_ready(app, pilot)
            await pilot.pause()
            assert len(app.query("Markdown")) == 2
            assert len(app.query(".speaker")) == 2
            assert app.rendered_text == "Segunda resposta corrigida."
            assert len(app.session_turns) == 2
            assert "list_files" not in app.session_turns[-1][-1]["content"]

    run_ui(scenario())


def test_large_preview_is_bounded_without_truncating_final_answer(tmp_path):
    class LargeStream(UIModel):
        def stream(self, messages, tools=None, on_delta=None, cancelled=None):
            on_delta("a" * 3500)
            on_delta("b" * 3500)
            on_delta("FINAL")
            return {"content": "a" * 3500 + "b" * 3500 + "FINAL"}

    app = CodaroApp(Agent(Repository(tmp_path), LargeStream()))

    async def scenario():
        from codaro.tui import GenerationPreview

        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            await pilot.pause()
            preview = app.query_one(GenerationPreview)
            assert len(preview.text) == 4000
            assert "prévia limitada" in str(preview.content.render())
            assert app.response_text.endswith("FINAL")
            assert len(app.response_text) == 7005

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
            assert app.query_one("#conversation").region.width == 70

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
            assert not app.query_one(Prompt).disabled

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
            app.query_one(Prompt).value = "/pwd"
            await pilot.press("enter")
            await pilot.pause()
            assert not model.started.is_set()
            assert not app.busy
            assert not agent.turns
            assert app.query_one(Prompt).value == ""
            assert any(
                str(tmp_path.resolve()) in str(widget.render()) for widget in app.query(".question")
            )

    run_ui(scenario())


def test_multiline_composer_preserves_newlines_until_enter(tmp_path):
    model = UIModel()
    agent = Agent(Repository(tmp_path), model)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test(size=(110, 35)) as pilot:
            await pilot.press("o", "i", "alt+enter", "x")
            assert app.query_one(Prompt).text == "oi\nx"
            assert not model.started.is_set()
            assert app.query_one(Prompt).size.height >= 2
            await pilot.press("enter")
            await wait_ready(app, pilot)
            assert agent.turns[-1][0]["content"] == "oi\nx"
            assert app.query_one(Prompt).text == ""

    run_ui(scenario())


def test_full_width_chat_preserves_draft_and_messages_on_resize(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            assert app.query_one("#conversation").region.width == 120
            await pilot.press("/", "p", "w", "d", "enter")
            app.query_one(Prompt).value = "Meu rascunho"
            await pilot.resize_terminal(70, 30)
            await pilot.pause()
            assert app.query_one("#conversation").region.width == 70
            await pilot.resize_terminal(120, 35)
            await pilot.pause()
            assert app.query_one("#conversation").region.width == 120
            assert app.query_one(Prompt).value == "Meu rascunho"
            assert str(tmp_path) in str(app.query_one(".question", Static).render())
            assert not app.query("#sidebar, #repository, #activity")

    run_ui(scenario())


def test_starter_suggestion_fills_draft_without_calling_model(tmp_path):
    model = UIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model, allow_edits=True))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.click("#suggest-explore")
            assert "estrutura" in app.query_one(Prompt).text
            assert app.focused is app.query_one(Prompt)
            assert not model.started.is_set()
            await pilot.press("enter")
            await wait_ready(app, pilot)
            assert not app.query_one("#welcome").display
            await pilot.press("ctrl+l")
            assert app.query_one("#welcome").display
            assert not app.query(".tool-card")

    run_ui(scenario())


def test_tool_details_are_collapsed_and_can_be_expanded(tmp_path):
    from textual.widgets import Collapsible

    from codaro.agent import AgentEvent

    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            app.activity(AgentEvent("tool_start", "Ler linhas", "code.py:1–3"))
            assert "Ler linhas… · code.py:1–3" in str(app.query_one("#status", Static).render())
            app.activity(
                AgentEvent("tool_end", "Ler linhas", "code.py:1–3\n3 linhas", "success", 12)
            )
            await pilot.pause()
            card = app.query_one(".tool-card", Collapsible)
            assert card.collapsed
            assert len(app.query(".tool-card")) == 1
            assert "Ler linhas · 12 ms" in str(app.query_one("#status", Static).render())
            card.scroll_visible(animate=False)
            await pilot.pause()
            await pilot.click("CollapsibleTitle")
            assert not card.collapsed

    run_ui(scenario())


def test_tls_insecure_is_visible_in_session(tmp_path):
    model = UIModel()
    model.settings = Settings("https://example.test/v1", "test", tls_insecure=True)
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(120, 35)):
            session = app.query_one("#session", Static)
            assert "TLS sem verificação" in str(session.render())
            assert session.has_class("insecure")

    run_ui(scenario())


def test_oversized_draft_is_preserved_and_not_sent(tmp_path):
    model = UIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            app.query_one(Prompt).value = "x" * 8001
            await pilot.press("enter")
            assert not model.started.is_set()
            assert len(app.query_one(Prompt).text) == 8001
            assert "8.000" in str(app.query_one("#status", Static).render())

    run_ui(scenario())


def test_intermediate_tool_prose_is_removed_before_final_answer(tmp_path):
    from test_agent import FakeModel, call

    from codaro.provider import Settings

    message = call("list_files", {})
    message["content"] = "Vou pensar e chamar uma ferramenta."
    model = FakeModel([message, {"content": "Não há arquivos permitidos."}])
    model.settings = Settings("http://localhost:11434/v1", "test")
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            await pilot.pause()
            assert app.rendered_text == "Não há arquivos permitidos."
            assert len(app.query(".speaker")) == 1
            assert len(app.query("Markdown")) == 1
            assert len(app.query(".tool-card")) == 1

    run_ui(scenario())


def test_project_overview_never_renders_rejected_session_explanation(tmp_path):
    from test_agent import FakeModel, call

    (tmp_path / "main.py").write_text("def main(): return 0\n")
    model = FakeModel(
        [
            call("get_repository_info", {}),
            {"content": "Os pontos de entrada são list_files e search_code."},
            call("read_lines", {"path": "main.py", "start": 1, "end": 1}, "read"),
            {"content": "main.py:1 define a função main."},
        ]
    )
    model.settings = Settings("http://localhost:11434/v1", "test")
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(120, 35)) as pilot:
            app.query_one(
                Prompt
            ).value = "Explique a estrutura deste projeto e seus pontos de entrada."
            await pilot.press("enter")
            await wait_ready(app, pilot)
            await pilot.pause()
            assert app.rendered_text == "main.py:1 define a função main."
            assert len(app.query("Markdown")) == 1
            assert len(app.query(".speaker")) == 1
            assert "list_files e search_code" not in app.response_text
            assert len(app.query(".tool-card")) == 1
            assert len(app.activity_group.events) == 3

    run_ui(scenario())


def test_local_commands_completion_and_history_restore_draft(tmp_path):
    model = UIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            prompt = app.query_one(Prompt)
            await pilot.press("/", "s", "t", "tab")
            assert prompt.value == "/status "
            await pilot.press("enter")
            assert "Último contexto enviado" in str(app.query_one(".question", Static).render())
            assert not model.started.is_set()
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            prompt.value = "rascunho"
            await pilot.press("up")
            assert prompt.value == "oi"
            await pilot.press("down")
            assert prompt.value == "rascunho"
            prompt.value = "/desconhecido"
            await pilot.press("enter")
            assert len(app.agent.turns) == 1
            assert "desconhecido" in str(app.query_one(".notice", Static).render())

    run_ui(scenario())


def test_file_completion_is_local_and_multiline_navigation_is_preserved(tmp_path):
    (tmp_path / "main file.py").write_text("x = 1\n")
    (tmp_path / ".env").write_text("TOKEN=private\n")
    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            for _ in range(100):
                if app.reference_paths:
                    break
                await pilot.pause(0.01)
            assert app.reference_paths == ["main file.py"]
            prompt = app.query_one(Prompt)
            prompt.value = "Leia @main"
            prompt.move_cursor(prompt.document.end)
            await pilot.press("tab")
            assert prompt.value == 'Leia @"main file.py" '
            prompt.value = "primeira\nsegunda"
            prompt.move_cursor(prompt.document.end)
            await pilot.press("up")
            assert prompt.cursor_location[0] == 0
            assert prompt.value == "primeira\nsegunda"
            assert not app.agent.provider.started.is_set()

    run_ui(scenario())


def test_activity_group_summarizes_multiple_tools_and_opens_errors(tmp_path):
    from codaro.agent import AgentEvent
    from codaro.tui import ActivityGroup

    app = CodaroApp(Agent(Repository(tmp_path), UIModel()))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            app.activity(
                AgentEvent("tool_end", "Ler linhas", "main.py:1–3\n3 linhas", "success", 2)
            )
            app.activity(
                AgentEvent("tool_end", "Buscar código", "Consulta: main\n1 resultado", "success", 3)
            )
            await pilot.pause()
            group = app.query_one(ActivityGroup)
            assert group.collapsed
            assert "2 ações" in group.title
            app.activity(
                AgentEvent("tool_end", "Ler linhas", "missing.py\nInexistente", "error", 1)
            )
            await pilot.pause()
            assert not group.collapsed
            assert len(app.query(ActivityGroup)) == 1
            assert "Inexistente" in str(group.details.render())

    run_ui(scenario())


def test_session_resume_restores_conversation_without_calling_model(tmp_path):
    from codaro.sessions import SessionStore

    store = SessionStore(tmp_path)
    store.save(
        [
            [
                {"role": "user", "content": "pergunta anterior"},
                {"role": "assistant", "content": "resposta anterior"},
            ]
        ],
        "test-model",
    )
    model = UIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model), resume=True)

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            assert len(app.agent.turns) == 1
            assert len(app.query("Markdown")) == 1
            assert not model.started.is_set()
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            assert len(store.load()) == 2

    run_ui(scenario())


def test_command_review_defaults_to_reject_and_approval_executes(tmp_path):
    import sys

    from test_agent import FakeModel, call

    from codaro.tui import CommandReview

    for approved in (False, True):
        target = tmp_path / "ran.txt"
        target.unlink(missing_ok=True)
        script = "from pathlib import Path; Path('ran.txt').write_text('yes'); print('ok')"
        model = FakeModel(
            [
                call("run_command", {"argv": [sys.executable, "-c", script]}),
                {"content": "Resultado recebido."},
            ]
        )
        model.settings = Settings("http://localhost/v1", "test")
        app = CodaroApp(Agent(Repository(tmp_path), model, allow_edits=True))

        async def scenario(app=app, approved=approved, target=target):
            async with app.run_test(size=(100, 35)) as pilot:
                await pilot.press("o", "i", "enter")
                for _ in range(200):
                    if isinstance(app.screen, CommandReview):
                        break
                    await pilot.pause(0.01)
                assert isinstance(app.screen, CommandReview)
                await pilot.pause(0.1)
                assert not target.exists()
                assert app.screen.focused.id == "reject-command"
                if approved:
                    await pilot.click("#approve-command")
                else:
                    await pilot.press("enter")
                await wait_ready(app, pilot)
                assert target.exists() == approved
                assert app.query_one(Prompt).disabled is False

        run_ui(scenario())


def test_cancel_during_command_approval_dismisses_without_execution(tmp_path):
    import sys

    from test_agent import FakeModel, call

    from codaro.tui import CommandReview

    model = FakeModel([call("run_command", {"argv": [sys.executable, "-c", "raise Exception()"]})])
    model.settings = Settings("http://localhost/v1", "test")
    app = CodaroApp(Agent(Repository(tmp_path), model, allow_edits=True))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.press("o", "i", "enter")
            for _ in range(200):
                if isinstance(app.screen, CommandReview):
                    break
                await pilot.pause(0.01)
            assert isinstance(app.screen, CommandReview)
            await pilot.press("ctrl+x")
            await wait_ready(app, pilot)
            await pilot.pause()
            assert not isinstance(app.screen, CommandReview)
            assert not app.agent.turns
            assert "cancelada" in str(app.query_one(".notice", Static).render())

    run_ui(scenario())


def test_applied_edit_can_continue_into_approved_validation(tmp_path):
    import sys

    from test_agent import FakeModel, call, edit_responses

    from codaro.tui import CommandReview

    path = tmp_path / "code.py"
    path.write_text("x = 1\n")
    script = (
        "from pathlib import Path; assert Path('code.py').read_text() == 'x = 2\\n'; print('OK')"
    )
    model = FakeModel(
        [
            *edit_responses(),
            call("read_lines", {"path": "code.py", "start": 1, "end": 1}),
            call("run_command", {"argv": [sys.executable, "-c", script]}, "validate"),
            {"content": "code.py:1 validado com saída 0."},
        ]
    )
    model.settings = Settings("http://localhost/v1", "test")
    app = CodaroApp(Agent(Repository(tmp_path), model, allow_edits=True))

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("o", "i", "enter")
            await wait_ready(app, pilot)
            await pilot.pause(0.1)
            proposal = app.agent.edits.pending[0]
            await pilot.click(f"#review-{proposal.id}")
            await pilot.click("#apply-edit")
            await wait_ready(app, pilot)
            await pilot.pause(0.1)
            assert path.read_text() == "x = 2\n"
            await pilot.click(f"#validate-{proposal.id}")
            for _ in range(200):
                if isinstance(app.screen, CommandReview):
                    break
                await pilot.pause(0.01)
            assert isinstance(app.screen, CommandReview)
            await pilot.pause(0.1)
            await pilot.click("#approve-command")
            await wait_ready(app, pilot)
            assert app.rendered_text == "code.py:1 validado com saída 0."
            restored = app.session.load()
            assert len(restored) == 2
            assert "Edição aplicada" in restored[0][-1]["content"]

    run_ui(scenario())


def test_saved_conversation_survives_context_pruning(tmp_path):
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), history_budget=0))

    async def scenario():
        async with app.run_test(size=(100, 35)) as pilot:
            for question in ("Primeira pergunta", "Segunda pergunta"):
                app.query_one(Prompt).value = question
                await pilot.press("enter")
                await wait_ready(app, pilot)
            assert len(app.agent.turns) == 0
            assert len(app.session.load()) == 2
            app.query_one(Prompt).value = "/compact"
            await pilot.press("enter")
            assert len(app.session.load()) == 2

    run_ui(scenario())


def test_memory_map_and_history_ui_commands_do_not_consult_model(tmp_path):
    (tmp_path / "main.py").write_text("def main(): return True\n")
    model = UIModel()
    app = CodaroApp(Agent(Repository(tmp_path), model))

    async def scenario():
        async with app.run_test(size=(110, 35)) as pilot:
            await app.local_command("/memory decision Usar SQLite.")
            await pilot.pause()
            await app.local_command("/history SQLite")
            await pilot.pause()
            await app.local_command("/map")
            await pilot.pause()
            assert not model.started.is_set()
            assert app.agent.memory.task()["items"][0]["text"] == "Usar SQLite."
            content = "\n".join(str(widget.render()) for widget in app.query(".question"))
            assert "SQLite" in content and "main.py" in content

    run_ui(scenario())


def test_ui_undo_reviews_inverse_diff_and_default_enter_does_not_apply(tmp_path):
    from test_edits import manager_for, propose

    from codaro.tui import EditReview

    manager, path = manager_for(tmp_path)
    proposal = propose(manager)
    manager.apply(proposal.id)
    app = CodaroApp(Agent(Repository(tmp_path), UIModel(), allow_edits=True))

    async def scenario():
        async with app.run_test(size=(110, 35)) as pilot:
            await app.local_command("/undo")
            await pilot.pause(0.1)
            assert isinstance(app.screen, EditReview)
            assert path.read_bytes() == proposal.after
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, EditReview)
            assert path.read_bytes() == proposal.after
            pending = app.agent.edits.pending[0]
            app.review_decision(pending.id, "apply")
            await wait_ready(app, pilot)
            assert path.read_bytes() == proposal.before
            assert not app.agent.edits.pending

    run_ui(scenario())
