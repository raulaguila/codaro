"""Inspect the running Textual UI without changing application code or real profiles.

Run from the checkout: .venv/bin/python docs/audits/2026-10-21-interface-review/reproduce.py
All repositories, provider credentials and model responses are synthetic.
"""

import asyncio
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import httpx
from textual.widgets import Button

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("NO_COLOR", None)

from test_tui import UIModel, run_ui, wait_ready  # noqa: E402

from codaro.agent import Agent, AgentEvent  # noqa: E402
from codaro.edits import EditProposal  # noqa: E402
from codaro.llm import ModelError, Settings  # noqa: E402
from codaro.llm.profiles import ProviderStore  # noqa: E402
from codaro.preferences import PreferencesStore  # noqa: E402
from codaro.repository import Repository  # noqa: E402
from codaro.ui.app import CodaroApp  # noqa: E402
from codaro.ui.composer import Prompt  # noqa: E402
from codaro.ui.dialogs import NewConversation, ProviderManager  # noqa: E402
from codaro.ui.preferences import PreferencesDialog  # noqa: E402
from codaro.ui.providers import ModelPicker, ProviderSetup  # noqa: E402
from codaro.ui.reviews import CommandReview, EditReview, ScopeReview  # noqa: E402

OUT = Path(__file__).parent
OBSERVATIONS = []
SETTINGS = Settings(
    "https://audit.invalid/v1",
    "qwen3.8:27b",
    context_window=131072,
    provider_id="local",
    context_source="API: max_model_len",
)


def screen_text(app):
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


def geometry(widget):
    if not widget.display:
        return {"display": False, "fully_visible": False}
    region = widget.region
    clip = widget.screen._compositor.find_widget(widget).clip
    return {
        "region": list(region),
        "content_region": list(widget.content_region),
        "visible": list(region.intersection(clip)),
        "fully_visible": bool(region.area) and region.intersection(clip) == region,
        "display": widget.display,
    }


def capture(app, name, **data):
    text = screen_text(app)
    (OUT / f"{name}.svg").write_text(app.export_screenshot(), encoding="utf-8")
    (OUT / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
    OBSERVATIONS.append({"capture": name, **data})
    (OUT / "observations.json").write_text(
        json.dumps(OBSERVATIONS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def make_model(**kwargs):
    model = UIModel(**kwargs)
    model.settings = SETTINGS
    return model


def catalog(request):
    return httpx.Response(
        200,
        json={
            "data": [
                {"id": "qwen3.8:27b", "context_window": 131072, "supported_parameters": ["tools"]},
                {"id": "llama3.1:8b", "context_window": 131072, "supported_parameters": ["tools"]},
            ]
        },
    )


def store_for(base):
    store = ProviderStore(base / "providers", transport=httpx.MockTransport(catalog))
    store.register("openai-compatible", "synthetic-key", name="local", base_url=SETTINGS.base_url)
    store.select("local", SETTINGS.model)
    return store


async def layouts(base, size):
    root = base / f"layout-{size[0]}"
    root.mkdir()
    (root / "auth.py").write_text("def can_edit(user):\n    return user.is_admin\n")
    store = store_for(root)
    app = CodaroApp(Agent(Repository(root), make_model(), mode="execute"))
    suffix = f"{size[0]}x{size[1]}"
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        capture(
            app,
            f"welcome-{suffix}",
            size=size,
            chat=geometry(app.query_one("#conversation")),
            footer_stack={
                name: geometry(app.query_one(name))
                for name in ("#context-meter", "#status", "#prompt", "#prompt-hint")
            },
        )
        for identifier in ("configure-start", "suggest-explore", "suggest-search", "suggest-edit"):
            prompt = app.query_one(Prompt)
            prompt.focus()
            await pilot.hover("#brand")
            await pilot.pause()
            button = app.query_one("#" + identifier, Button)
            before = geometry(button)
            await pilot.hover(button)
            await pilot.pause()
            capture(
                app,
                f"hover-{identifier}-{suffix}",
                size=size,
                before=before,
                after=geometry(button),
                label=button.label.plain,
                label_visible=button.label.plain in screen_text(app),
            )
            await pilot.hover("#brand")
            button.focus()
            await pilot.pause()
            capture(
                app,
                f"focus-{identifier}-{suffix}",
                size=size,
                after=geometry(button),
                label_visible=button.label.plain in screen_text(app),
            )

        prompt.focus()
        await pilot.hover("#brand")
        app.activate_provider(SETTINGS)
        app.activity(
            AgentEvent(
                "model_start",
                "Consultando",
                context_tokens=16000,
                context_limit=app.agent.adaptive_input_limit,
            )
        )
        await pilot.pause()
        capture(
            app,
            f"context-duplicate-{suffix}",
            size=size,
            context_lines=[s for s in screen_text(app).splitlines() if "Contexto" in s],
            chat=geometry(app.query_one("#conversation")),
        )

        prompt.value = "/"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        capture(
            app,
            f"completion-{suffix}",
            size=size,
            chat=geometry(app.query_one("#conversation")),
            completion=geometry(app.query_one("#completion")),
        )
        prompt.value = "Primeira linha\nSegunda linha\nTerceira linha\nQuarta linha"
        prompt.move_cursor(prompt.document.end)
        await pilot.pause()
        capture(
            app, f"multiline-{suffix}", size=size, chat=geometry(app.query_one("#conversation"))
        )
        prompt.value = ""

        proposal = EditProposal(
            "audit",
            "auth.py",
            "Corrigir autorização",
            b"return user.is_admin\n",
            b"return user.is_admin or user.editor\n",
            "--- auth.py\n+++ auth.py\n@@ -1 +1 @@\n-return user.is_admin\n"
            "+return user.is_admin or user.editor\n",
        )
        modals = [
            (ProviderSetup(store), "provider"),
            (ModelPicker(store), "models"),
            (ProviderManager(store), "manager"),
            (PreferencesDialog(PreferencesStore(root / "preferences")), "preferences"),
            (NewConversation(), "new"),
            (EditReview(proposal), "edit"),
            (CommandReview(root, ["python", "-m", "pytest", "-q"], 60), "command"),
            (ScopeReview({"id": "audit", "objective": "Revisar aplicação " * 30}), "scope"),
        ]
        for modal, name in modals:
            app.push_screen(modal)
            await pilot.pause()
            capture(
                app,
                f"{name}-{suffix}",
                size=size,
                controls={
                    b.id: {
                        **geometry(b),
                        "label": b.label.plain,
                        "label_visible": b.label.plain in screen_text(app),
                    }
                    for b in modal.query(Button)
                },
            )
            await pilot.press("escape")
            await pilot.pause()
        await pilot.press("ctrl+p")
        await pilot.pause()
        capture(app, f"palette-{suffix}", size=size)
        await pilot.press("escape")
    print("layouts complete", suffix, flush=True)


class StreamModel(UIModel):
    settings = SETTINGS

    def stream(self, messages, tools=None, on_delta=None, cancelled=None, *, on_reasoning=None):
        on_reasoning("Conferindo os arquivos disponíveis para responder ao pedido.")
        on_delta("O projeto possui uma função de autorização em `auth.py`.\n\n")
        self.started.set()
        self.release.wait(15)
        on_delta("Ela consulta `user.is_admin` para permitir a edição.")
        return {
            "content": "O projeto possui uma função de autorização em `auth.py`.\n\n"
            "Ela consulta `user.is_admin` para permitir a edição."
        }


async def conversation(base):
    root = base / "conversation"
    root.mkdir()
    (root / "auth.py").write_text("def can_edit(user):\n    return user.is_admin\n")
    model = StreamModel()
    app = CodaroApp(Agent(Repository(root), model, mode="ask"))
    async with app.run_test(size=(100, 30)) as pilot:
        await app.submit_question("Explique a autorização do projeto.")
        try:
            assert await asyncio.to_thread(model.started.wait, 5)
            app.query_one(Prompt).value = "Meu próximo pedido ainda não enviado"
            await pilot.pause()
            capture(
                app,
                "stream-live-100x30",
                busy=app.busy,
                input_disabled=app.query_one(Prompt).disabled,
            )
        finally:
            model.release.set()
        await wait_ready(app, pilot)
        await pilot.pause()
        capture(app, "stream-final-100x30", final_parent=app.reply.parent.id)
        app.reasoning_group.collapsed = False
        await pilot.pause()
        capture(app, "reasoning-expanded-100x30")
        app.reasoning_group.collapsed = True

        app.agent.provider.settings = replace(SETTINGS, tls_insecure=True)
        app.update_session_header()
        await pilot.pause()
        capture(app, "insecure-header-100x30")

    error = make_model(failure=True)
    error.complete = lambda *args, **kwargs: (_ for _ in ()).throw(
        ModelError("API retornou HTTP 401. Confira o modelo, a URL e a credencial.")
    )
    app = CodaroApp(Agent(Repository(root), error, mode="ask"))
    async with app.run_test(size=(80, 24)) as pilot:
        await app.submit_question("Explique auth.py")
        await wait_ready(app, pilot)
        await pilot.pause()
        capture(app, "authentication-error-80x24")


async def main(base):
    for size in [(60, 20), (80, 24), (120, 35), (160, 50)]:
        await layouts(base, size)
    await conversation(base)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="codaro-interface-review-") as temporary:
        base = Path(temporary)
        os.environ["XDG_CONFIG_HOME"] = str(base / "global-config")
        run_ui(main(base))
    print(f"Captured {len(OBSERVATIONS)} states in {OUT}")
