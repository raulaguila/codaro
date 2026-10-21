"""Exercise the real Textual app with isolated repositories and controlled providers.

Run from the repository: .venv/bin/python docs/audits/2026-10-07-ui-ux/reproduce.py
No external API calls, credentials, or production files are used.
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# The cloud runner exports NO_COLOR. Capture the regular full-color UI explicitly.
os.environ.pop("NO_COLOR", None)
sys.path.insert(0, str(ROOT / "tests"))
import httpx  # noqa: E402
from test_tui import UIModel, run_ui  # noqa: E402
from textual.containers import VerticalScroll  # noqa: E402
from textual.widgets import Button, Input, Select, Static  # noqa: E402

from codaro.agent import Agent, AgentEvent  # noqa: E402
from codaro.provider import Settings, create_provider  # noqa: E402
from codaro.provider_ui import ModelPicker, ProviderSetup  # noqa: E402
from codaro.providers import ProviderStore  # noqa: E402
from codaro.repository import Repository  # noqa: E402
from codaro.tui import CodaroApp, CommandReview, EditReview, Prompt, ScopeReview  # noqa: E402

OUT = Path(__file__).parent
RESULTS = []


def record(name, **data):
    RESULTS.append({"scenario": name, **data})


def snapshot(app, name):
    (OUT / f"{name}.svg").write_text(app.export_screenshot(), encoding="utf-8")


def geometry(widget):
    r = widget.region
    clip = widget.screen._compositor.find_widget(widget).clip
    visible = r.intersection(clip)
    return {
        "region": list(r),
        "clip": list(clip),
        "visible_region": list(visible),
        "fully_visible": visible == r and r.width > 0 and r.height > 0,
        "disabled": getattr(widget, "disabled", None),
    }


def catalog(request):
    return httpx.Response(
        200,
        json={
            "data": [
                {"id": "small-model", "context_window": 8192, "max_output_tokens": 512},
                {
                    "id": "very-long-model-name-with-a-lot-of-characters-for-layout-testing",
                    "context_window": 32768,
                },
            ]
        },
    )


async def layout(base, size):
    root = base / f"layout-{size[0]}-{size[1]}"
    root.mkdir()
    agent = Agent(Repository(root), UIModel(), mode="execute")
    app = CodaroApp(agent)
    store = ProviderStore(
        base / f"config-{size[0]}-{size[1]}", transport=httpx.MockTransport(catalog)
    )
    store.register(
        "openai-compatible", "audit-fake-key", name="audit", base_url="https://audit.invalid/v1"
    )
    store.select("audit", "small-model")
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        snapshot(app, f"welcome-{size[0]}x{size[1]}")
        session = app.query_one("#session", Static)
        record(
            "welcome",
            size=size,
            header_text=str(session.render()),
            header=geometry(session),
            hint=geometry(app.query_one("#prompt-hint")),
            prompt=geometry(app.query_one(Prompt)),
        )
        app.push_screen(ProviderSetup(store))
        await pilot.pause()
        snapshot(app, f"provider-{size[0]}x{size[1]}")
        record(
            "provider-form",
            size=size,
            focused=getattr(app.focused, "id", None),
            controls={
                w.id: geometry(w) for w in app.screen.query("Input, Select, Button, Checkbox")
            },
        )
        await pilot.press("escape")
        await pilot.pause()
        record("provider-close-focus", size=size, focused=getattr(app.focused, "id", None))
        app.push_screen(ModelPicker(store))
        await pilot.pause()
        app.screen.query_one("#model-id", Select).value = "small-model"
        await pilot.pause()
        snapshot(app, f"models-{size[0]}x{size[1]}")
        record(
            "model-picker",
            size=size,
            controls={w.id: geometry(w) for w in app.screen.query("Select, Button")},
        )
        await pilot.press("escape")
        app.push_screen(
            CommandReview(root, ["python", "-m", "pytest", "tests/test_example.py"], 120)
        )
        await pilot.pause()
        snapshot(app, f"command-{size[0]}x{size[1]}")
        record(
            "command-approval",
            size=size,
            focused=getattr(app.focused, "id", None),
            controls={w.id: geometry(w) for w in app.screen.query(Button)},
        )
        await pilot.press("escape")
        (root / "main.py").write_text("value = 1\n")
        agent.edits.observe(agent.repository.read_lines("main.py", 1, 1), b"value = 1\n")
        proposal = agent.edits.propose("main.py", "value = 1", "value = 2", "Exemplo de revisão")
        app.push_screen(EditReview(agent.edits.proposals[proposal["proposal_id"]]))
        await pilot.pause()
        snapshot(app, f"edit-{size[0]}x{size[1]}")
        record(
            "edit-approval",
            size=size,
            focused=getattr(app.focused, "id", None),
            controls={w.id: geometry(w) for w in app.screen.query(Button)},
        )
        await pilot.press("escape")
        task = agent.tasks.start("Auditar a experiência do usuário no projeto", new=True)
        app.push_screen(ScopeReview(task))
        await pilot.pause()
        snapshot(app, f"scope-{size[0]}x{size[1]}")
        record(
            "scope-approval",
            size=size,
            focused=getattr(app.focused, "id", None),
            controls={w.id: geometry(w) for w in app.screen.query("TextArea, Button")},
        )
        await pilot.press("escape")


async def provider_keyboard(base):
    root = base / "keyboard"
    root.mkdir()
    calls = []

    def failed(request):
        calls.append(str(request.url))
        return httpx.Response(401, json={"error": "audit-fake-key"})

    store = ProviderStore(base / "keyboard-config", transport=httpx.MockTransport(failed))
    app = CodaroApp(Agent(Repository(root), UIModel(), mode="execute"))
    async with app.run_test(size=(80, 24)) as pilot:
        app.push_screen(ProviderSetup(store))
        await pilot.pause()
        app.screen.query_one("#provider-key", Input).value = "audit-fake-key"
        for _ in range(15):
            if getattr(app.focused, "id", None) == "provider-test":
                break
            await pilot.press("tab")
            await pilot.pause()
        test = app.screen.query_one("#provider-test", Button)
        record(
            "provider-tab-reachable",
            focused=getattr(app.focused, "id", None),
            button=geometry(test),
        )
        snapshot(app, "provider-tab-scrolled")
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause(0.02)
            if calls and not app.screen.saving:
                break
        status = app.screen.query_one("#provider-error", Static)
        record(
            "provider-error-visible",
            text=str(status.render()),
            status=geometry(status),
            requests=len(calls),
            secret_visible="audit-fake-key" in str(status.render()),
            focused=getattr(app.focused, "id", None),
        )
        snapshot(app, "provider-401")
        await pilot.press("escape")


async def provider_wait(base):
    root = base / "provider-wait"
    root.mkdir()
    started, release = threading.Event(), threading.Event()

    def blocked(request):
        started.set()
        release.wait(20)
        return catalog(request)

    store = ProviderStore(base / "wait-config", transport=httpx.MockTransport(blocked))
    app = CodaroApp(Agent(Repository(root), UIModel(), mode="execute"))
    async with app.run_test(size=(120, 50)) as pilot:
        app.push_screen(ProviderSetup(store))
        await pilot.pause()
        app.screen.query_one("#provider-key", Input).value = "audit-fake-key"
        screen = app.screen
        # Run the actual connection method without blocking Pilot on its message queue.
        connection = asyncio.create_task(screen.test_connection())
        assert await asyncio.to_thread(started.wait, 2)
        await pilot.press("escape")
        record(
            "provider-test-cancellation",
            still_open=isinstance(app.screen, ProviderSetup),
            saving=screen.saving,
            back_disabled=screen.query_one("#provider-back").disabled,
        )
        snapshot(app, "provider-waiting")
        release.set()
        await connection
        await pilot.press("escape")


async def interactions(base):
    root = base / "interactions"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n")
    app = CodaroApp(Agent(Repository(root), UIModel(), mode="execute"))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        snapshot(app, "command-palette")
        record(
            "palette",
            screen=type(app.screen).__name__,
            commands=[c.title for c in app.get_system_commands(app.screen_stack[0])],
        )
        await pilot.press("escape")
        await pilot.press("slash")
        await pilot.pause()
        record("slash-completion", displayed=app.query_one("#completion").display)
        app.query_one(Prompt).value = "@ma"
        app.query_one(Prompt).move_cursor(app.query_one(Prompt).document.end)
        await pilot.pause()
        await pilot.press("tab")
        record("file-completion", value=app.query_one(Prompt).value)
        app.query_one(Prompt).value = "Rascunho preservado?"
        app.push_screen(ProviderSetup(ProviderStore(base / "unused-config")))
        await pilot.pause()
        await pilot.press("ctrl+l")
        await pilot.pause()
        record(
            "clear-in-provider-modal",
            screen=type(app.screen).__name__,
            draft=app.query_one(Prompt).value,
        )
        await pilot.press("escape")
        app.query_one(Prompt).value = "Não devo sumir"
        app.agent.turns = [
            [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}]
        ]
        app.session_turns = list(app.agent.turns)
        await pilot.press("ctrl+l")
        await pilot.pause()
        record(
            "clear-undo",
            turns=len(app.agent.turns),
            session_turns=len(app.session_turns),
            draft=app.query_one(Prompt).value,
        )
        await app.local_command("/task new Implementar a atividade de exemplo")
        task_before = app.agent.tasks.current()["id"]
        await pilot.press("ctrl+l")
        record(
            "clear-keeps-task",
            same_task=app.agent.tasks.current()["id"] == task_before,
            status=str(app.query_one("#status", Static).render()),
        )
        await app.local_command("/plan")
        await pilot.pause()
        snapshot(app, "mode-after-clear")
        record(
            "mode-change",
            mode=app.agent.mode.value,
            header=str(app.query_one("#session", Static).render()),
        )
        app.query_one(Prompt).value = "first\nsecond\nthird\nfourth\nfifth\nsixth\nseventh"
        await pilot.pause()
        record(
            "multiline-composer",
            region=geometry(app.query_one(Prompt)),
            hint=str(app.query_one("#prompt-hint", Static).render()),
        )
        for i in range(35):
            app.mount_message(Static(f"Mensagem {i}\n" + "linha de histórico\n" * 3))
        await pilot.pause()
        conversation = app.query_one("#conversation", VerticalScroll)
        conversation.scroll_home(animate=False)
        await pilot.pause()
        before = conversation.scroll_y
        app.activity(
            AgentEvent(
                kind="tool_end",
                title="Buscar código",
                detail="resultado",
                elapsed_ms=10,
                state="success",
            )
        )
        await pilot.pause(0.2)
        record(
            "scroll-follow",
            before=before,
            after=conversation.scroll_y,
            maximum=conversation.max_scroll_y,
        )
        snapshot(app, "scroll-tool-arrival")
        app.theme = "textual-light"
        await pilot.pause()
        snapshot(app, "light-theme")
        record(
            "light-theme",
            theme=app.theme,
            background=str(app.screen.styles.background),
            conversation_background=str(conversation.styles.background),
        )


async def pending_clear(base):
    root = base / "clear-pending"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n")
    agent = Agent(Repository(root), UIModel(), mode="execute")
    app = CodaroApp(agent)
    async with app.run_test(size=(100, 30)) as pilot:
        agent.edits.observe(agent.repository.read_lines("main.py", 1, 1), b"value = 1\n")
        item = agent.edits.propose("main.py", "value = 1", "value = 2", "Proposta pendente")
        proposal = agent.edits.proposals[item["proposal_id"]]
        await pilot.press("ctrl+l")
        await pilot.pause()
        record(
            "clear-rejects-pending",
            proposal_state=proposal.state,
            original_file=(root / "main.py").read_text(),
            modal=type(app.screen).__name__,
            status=str(app.query_one("#status", Static).render()),
        )
        await app.local_command("/task new Atividade autorizada")
        task = agent.tasks.current()
        agent.policy.grant(task["id"], ["src"], [])
        app.update_session_header()
        await pilot.press("ctrl+l")
        await pilot.pause()
        record(
            "clear-keeps-grant",
            policy=agent.policy.kind,
            paths=list(agent.policy.paths),
            task_objective=agent.tasks.current()["objective"],
        )
        snapshot(app, "clear-keeps-grant")


async def connection_failure(base):
    root = base / "connection-failure"
    root.mkdir()

    def disconnected(request):
        raise httpx.ConnectError("Offline for audit", request=request)

    provider = create_provider(
        Settings("http://localhost:11434/v1", "offline-model", provider_id="Local"),
        transport=httpx.MockTransport(disconnected),
    )
    app = CodaroApp(Agent(Repository(root), provider, mode="ask"))
    async with app.run_test(size=(100, 30)) as pilot:
        app.query_one(Prompt).value = "Explique este projeto."
        await pilot.press("enter")
        for _ in range(200):
            await pilot.pause(0.02)
            if not app.busy:
                break
        snapshot(app, "connection-failure")
        record(
            "connection-failure",
            busy=app.busy,
            notice=[str(w.render()) for w in app.query(".notice")],
            inline_error_actions=len(app.query(".notice Button")),
            status=str(app.query_one("#status", Static).render()),
        )


async def incompatible_model(base):
    root = base / "incompatible-model"
    root.mkdir()

    def metadata(request):
        return httpx.Response(
            200,
            json={"data": [{"id": "embedding-only", "supported_parameters": []}]},
        )

    store = ProviderStore(base / "incompatible-config", transport=httpx.MockTransport(metadata))
    store.register(
        "openai-compatible",
        "audit-fake-key",
        name="incompatible",
        base_url="https://audit.invalid/v1",
    )
    app = CodaroApp(Agent(Repository(root), UIModel(), mode="ask"))
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(ModelPicker(store))
        await pilot.pause()
        app.screen.query_one("#model-id", Select).value = "embedding-only"
        await pilot.pause()
        before = str(app.screen.query_one("#model-info", Static).render())
        await pilot.click("#model-use")
        await pilot.pause()
        record(
            "incompatible-model-selectable",
            metadata_before_use=before,
            error_after_use=str(app.screen.query_one("#model-error", Static).render()),
        )
        snapshot(app, "incompatible-model")
        await pilot.press("escape")


class StreamModel(UIModel):
    def stream(self, messages, tools=None, on_delta=None, cancelled=None, *, on_reasoning=None):
        on_reasoning("Vou observar a estrutura antes de responder.")
        on_delta("## Resultado\n\nParte da resposta recebida em streaming.")
        self.started.set()
        self.release.wait(8)
        if cancelled and cancelled.is_set():
            from codaro.provider import RequestCancelled

            raise RequestCancelled("Cancelado")
        on_delta("\n\nResposta completa para o teste da interface.")
        return {
            "content": (
                "## Resultado\n\nParte da resposta recebida em streaming."
                "\n\nResposta completa para o teste da interface."
            )
        }


async def streaming(base, cancel=False):
    root = base / ("cancel" if cancel else "stream")
    root.mkdir()
    model = StreamModel()
    app = CodaroApp(Agent(Repository(root), model, mode="ask"))
    async with app.run_test(size=(100, 30)) as pilot:
        app.query_one(Prompt).value = "Olá, explique como posso usar o assistente."
        await pilot.press("enter")
        await asyncio.to_thread(model.started.wait, 5)
        await pilot.pause()
        snapshot(app, "stream-partial" if not cancel else "cancel-before")
        record(
            "stream-live",
            cancel=cancel,
            busy=app.busy,
            prompt_disabled=app.query_one(Prompt).disabled,
            preview=app.generation_preview.title,
            reasoning=app.reasoning_preview.title,
        )
        if cancel:
            await pilot.press("ctrl+x")
            record("cancel-request", status=str(app.query_one("#status", Static).render()))
        model.release.set()
        for _ in range(200):
            await pilot.pause(0.02)
            if not app.busy:
                break
        await pilot.pause()
        snapshot(app, "stream-final" if not cancel else "cancel-after")
        record(
            "stream-finish",
            cancel=cancel,
            busy=app.busy,
            focused=getattr(app.focused, "id", None),
            status=str(app.query_one("#status", Static).render()),
            answer=app.response_text,
            activity_title=app.activity_group.title if app.activity_group else None,
            question_recoverable=bool(app.history.values),
        )


async def main(base):
    for size in [(60, 20), (80, 24), (120, 35), (160, 50)]:
        await layout(base, size)
    await interactions(base)
    await provider_keyboard(base)
    await provider_wait(base)
    await pending_clear(base)
    await connection_failure(base)
    await incompatible_model(base)
    await streaming(base)
    await streaming(base, cancel=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="codaro-ux-") as path:
        try:
            run_ui(main(Path(path)))
        finally:
            (OUT / "observations.json").write_text(
                json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(f"{len(RESULTS)} observations saved to {OUT}")
