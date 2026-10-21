"""Headless interaction audit of the real Textual UI; no external API calls.

Run: .venv/bin/python docs/audits/2026-10-21-ui-ux/reproduce.py
Artifacts contain only synthetic projects, model responses and credentials.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import httpx
from textual.widgets import Button, Input, Static

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("NO_COLOR", None)

from test_tui import UIModel, run_ui, wait_ready  # noqa: E402

from codaro.agent import Agent  # noqa: E402
from codaro.llm import RequestCancelled  # noqa: E402
from codaro.llm.profiles import ProviderStore  # noqa: E402
from codaro.repository import Repository  # noqa: E402
from codaro.ui.app import CodaroApp  # noqa: E402
from codaro.ui.composer import Prompt  # noqa: E402
from codaro.ui.dialogs import NewConversation, ProviderManager  # noqa: E402
from codaro.ui.providers import ModelPicker, ProviderSetup  # noqa: E402
from codaro.ui.reviews import CommandReview, ScopeReview  # noqa: E402

OUT = Path(__file__).parent
RESULTS = []


def record(scenario, **data):
    RESULTS.append({"scenario": scenario, **data})
    (OUT / "observations.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2) + "\n")


def screen_text(app):
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


def capture(app, name):
    (OUT / f"{name}.svg").write_text(app.export_screenshot())
    (OUT / f"{name}.txt").write_text(screen_text(app) + "\n")


def geometry(widget):
    if not widget.display:
        return {"intentionally_hidden": True, "fully_visible": False}
    region = widget.region
    clip = widget.screen._compositor.find_widget(widget).clip
    return {
        "region": list(region),
        "visible": list(region.intersection(clip)),
        "fully_visible": bool(region.area) and region.intersection(clip) == region,
    }


def catalog(request):
    return httpx.Response(
        200,
        json={
            "data": [
                {"id": "chat", "context_window": 32768, "supported_parameters": ["tools"]},
                {"id": "embedding", "supported_parameters": []},
            ]
        },
    )


def store_for(base):
    store = ProviderStore(base / "profiles", transport=httpx.MockTransport(catalog))
    store.register(
        "openai-compatible", "synthetic-key", name="local", base_url="https://audit.invalid/v1"
    )
    store.select("local", "chat")
    return store


async def layouts(base, size):
    root = base / f"layout-{size[0]}"
    root.mkdir()
    store = store_for(root)
    app = CodaroApp(Agent(Repository(root), UIModel(), mode="execute"))
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        suffix = f"{size[0]}x{size[1]}"
        capture(app, f"welcome-{suffix}")
        record(
            "welcome",
            size=size,
            prompt=geometry(app.query_one(Prompt)),
            chat=geometry(app.query_one("#conversation")),
            buttons={b.id: geometry(b) for b in app.query(Button)},
        )
        app.query_one(Prompt).value = "/"
        app.query_one(Prompt).move_cursor(app.query_one(Prompt).document.end)
        await pilot.pause()
        capture(app, f"completion-{suffix}")
        record(
            "completion",
            size=size,
            chat_height=app.query_one("#conversation").region.height,
            menu=geometry(app.query_one("#completion")),
        )
        app.query_one(Prompt).value = ""
        for modal, name in [
            (ProviderSetup(store), "provider"),
            (ModelPicker(store), "models"),
            (ProviderManager(store), "manager"),
            (NewConversation(), "new"),
            (CommandReview(root, ["python", "-m", "pytest"], 60), "command"),
            (ScopeReview({"id": "audit", "objective": "Revisar aplicação " * 40}), "scope"),
        ]:
            app.push_screen(modal)
            await pilot.pause()
            capture(app, f"{name}-{suffix}")
            record(
                name,
                size=size,
                focused=getattr(app.focused, "id", None),
                controls={b.id: geometry(b) for b in modal.query(Button)},
            )
            await pilot.press("escape")
            await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        capture(app, f"palette-{suffix}")
        record("palette", size=size, opened=type(app.screen).__name__)
        await pilot.press("escape")


async def reasoning(base):
    root = base / "reasoning"
    root.mkdir()
    app = CodaroApp(Agent(Repository(root), UIModel(), mode="ask"))
    async with app.run_test(size=(100, 30)) as pilot:
        app.hide_welcome()
        app.append_reasoning("PRIMEIRA_ETAPA_OCULTA")
        app.finish_preview("tools")
        await pilot.pause()
        app.append_reasoning("SEGUNDA_ETAPA_OCULTA")
        await pilot.pause()
        text = screen_text(app)
        capture(app, "reasoning-collapsed")
        record(
            "reasoning-collapsed",
            collapsed=app.reasoning_group.collapsed,
            step_two_leaked="SEGUNDA_ETAPA_OCULTA" in text,
            parents=[type(p.parent).__name__ for p in app.query(".reasoning-preview")],
        )
        app.finish_preview("tools")
        app.append_reasoning("TERCEIRA_ETAPA_OCULTA")
        app.finish_preview("answer")
        app.active_question = "Mostre como as etapas ficam agrupadas."
        app.finish("Resposta final separada.")
        await pilot.pause()
        capture(app, "reasoning-final")
        record(
            "reasoning-final",
            rendered_titles=screen_text(app).count("etapa encerrada"),
            final_parent=getattr(app.reply.parent, "id", None),
        )


class StreamModel(UIModel):
    def stream(self, messages, tools=None, on_delta=None, cancelled=None, *, on_reasoning=None):
        on_reasoning("Observando o pedido antes de responder.")
        on_delta("## Resposta\n\nPrimeira parte recebida em streaming.")
        self.started.set()
        self.release.wait(15)
        if cancelled and cancelled.is_set():
            raise RequestCancelled("Cancelada.")
        on_delta("\n\nÚltima parte recebida.")
        return {
            "content": (
                "## Resposta\n\nPrimeira parte recebida em streaming.\n\nÚltima parte recebida."
            )
        }


async def stream_and_cancel(base, cancel):
    root = base / ("cancel" if cancel else "stream")
    root.mkdir()
    model = StreamModel()
    app = CodaroApp(Agent(Repository(root), model, mode="ask"))
    async with app.run_test(size=(100, 30)) as pilot:
        app.query_one(Prompt).value = "Olá, como usar o assistente?"
        await pilot.press("enter")
        try:
            assert await asyncio.to_thread(model.started.wait, 5)
            await pilot.pause()
            capture(app, "cancel-before" if cancel else "stream-live")
            app.query_one(Prompt).value = "Próximo rascunho"
            await pilot.press("enter")
            record(
                "stream-draft",
                cancel=cancel,
                preserved=app.query_one(Prompt).value == "Próximo rascunho",
            )
            if cancel:
                await pilot.press("ctrl+x")
        finally:
            model.release.set()
        await wait_ready(app, pilot)
        await pilot.pause()
        capture(app, "cancel-after" if cancel else "stream-final")
        record(
            "stream-end",
            cancel=cancel,
            turns=len(app.agent.turns),
            draft=app.query_one(Prompt).value,
            focused=getattr(app.focused, "id", None),
            recovery_actions=len(app.query(".recovery-actions Button")),
        )


async def keyboard(base):
    root = base / "keyboard"
    root.mkdir()
    app = CodaroApp(Agent(Repository(root), UIModel(failure=True), mode="ask"))
    async with app.run_test(size=(80, 24)) as pilot:
        app.query_one(Prompt).value = "/stat"
        app.query_one(Prompt).move_cursor(app.query_one(Prompt).document.end)
        await pilot.pause()
        before = app.query_one("#completion").display
        await pilot.press("escape")
        await pilot.pause()
        record(
            "completion-escape",
            menu_initially_open=before,
            menu_still_open=app.query_one("#completion").display,
            status=str(app.query_one("#status", Static).render()),
        )
        app.query_one(Prompt).value = "Uma pergunta"
        await pilot.press("enter")
        await wait_ready(app, pilot)
        await pilot.pause()
        capture(app, "provider-error")
        record(
            "provider-error",
            actions={b.id: geometry(b) for b in app.query(".recovery-actions Button")},
        )
        app.query_one(Prompt).value = "Rascunho que deve ser preservado"
        await pilot.click("#retry-question")
        await wait_ready(app, pilot)
        await pilot.pause()
        capture(app, "retry-finished")
        record(
            "retry-finished",
            turns=len(app.agent.turns),
            draft=app.query_one(Prompt).value,
            old_actions=[
                {"id": b.id, "disabled": b.disabled} for b in app.query(".recovery-actions Button")
            ],
        )


async def slow_catalog(base, refresh):
    root = base / ("slow-refresh" if refresh else "slow-save")
    root.mkdir()
    store = store_for(root)
    started, release = threading.Event(), threading.Event()

    def blocked(request):
        started.set()
        release.wait(5)
        return catalog(request)

    store.transport = httpx.MockTransport(blocked)
    app = CodaroApp(Agent(Repository(root), UIModel(), mode="ask"))
    async with app.run_test(size=(80, 24)) as pilot:
        modal = ModelPicker(store) if refresh else ProviderSetup(store)
        app.push_screen(modal)
        await pilot.pause()
        if not refresh:
            modal.query_one("#provider-key", Input).value = "synthetic-key"
            modal.query_one("#provider-name", Input).value = "second"
        modal.query_one("#model-refresh" if refresh else "#provider-save", Button).press()
        try:
            assert await asyncio.to_thread(started.wait, 2)
            await pilot.press("escape")
            capture(app, "model-refresh-wait" if refresh else "provider-save-wait")
            record(
                "slow-catalog",
                refresh=refresh,
                escape_closed=app.screen is not modal,
                back_disabled=modal.query_one(
                    "#model-back" if refresh else "#provider-back", Button
                ).disabled,
                busy=modal.fetching if refresh else modal.saving,
            )
        finally:
            release.set()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not (modal.fetching if refresh else modal.saving):
                break
        await pilot.pause()
        if app.screen is modal:
            await pilot.press("escape")


async def cramped_composer(base):
    root = base / "composer"
    root.mkdir()
    app = CodaroApp(Agent(Repository(root), UIModel(), mode="ask"))
    async with app.run_test(size=(60, 20)) as pilot:
        app.query_one(Prompt).value = "linha de contexto\n" * 7 + "/"
        app.query_one(Prompt).move_cursor(app.query_one(Prompt).document.end)
        await pilot.pause()
        capture(app, "multiline-60x20")
        record(
            "multiline",
            chat=geometry(app.query_one("#conversation")),
            prompt=geometry(app.query_one(Prompt)),
        )
        app.query_one(Prompt).value = "/"
        app.query_one(Prompt).move_cursor(app.query_one(Prompt).document.end)
        await pilot.pause()
        capture(app, "completion-small")
        record(
            "completion-small",
            chat_height=app.query_one("#conversation").region.height,
            menu=geometry(app.query_one("#completion")),
        )


async def main():
    with tempfile.TemporaryDirectory(prefix="codaro-ux-") as directory:
        base = Path(directory)
        for size in [(60, 20), (80, 24), (120, 35), (160, 50)]:
            await layouts(base, size)
        await reasoning(base)
        await stream_and_cancel(base, False)
        await stream_and_cancel(base, True)
        await keyboard(base)
        await slow_catalog(base, False)
        await slow_catalog(base, True)
        await cramped_composer(base)
    print(f"Recorded {len(RESULTS)} observations in {OUT}")


def verify():
    def rows(name):
        return [r for r in RESULTS if r["scenario"] == name]

    checks = {
        "UX01": not rows("reasoning-collapsed")[0]["step_two_leaked"]
        and rows("reasoning-final")[0]["rendered_titles"] == 0,
        "UX02": rows("retry-finished")[0]["draft"] == "Rascunho que deve ser preservado",
        "UX03": all(not r["busy"] and not r["back_disabled"] for r in rows("slow-catalog")),
        "UX04": all(r["chat_height"] >= 4 for r in rows("completion")),
        "UX05": not rows("completion-escape")[0]["menu_still_open"],
        "UX06": all(r["buttons"]["configure-start"]["fully_visible"] for r in rows("welcome")),
        "UX07": all(r["recovery_actions"] == 1 for r in rows("stream-end") if r["cancel"]),
        "UX08": not rows("retry-finished")[0]["old_actions"],
    }
    (OUT / "verification.json").write_text(json.dumps(checks, indent=2) + "\n")
    assert all(checks.values()), checks
    print("All eight audit findings verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    OUT = args.output
    OUT.mkdir(parents=True, exist_ok=True)
    run_ui(main())
    if args.verify:
        verify()
