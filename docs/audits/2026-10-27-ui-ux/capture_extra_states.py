# ruff: noqa: E402
"""Reprodução isolada da auditoria; não chama modelos nem altera o projeto analisado."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import httpx
from capture_ui import PreviewProvider
from textual.widgets import Input

from codaro.agent import Agent
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.dialogs import NewConversation
from codaro.ui.preferences import PreferencesDialog
from codaro.ui.providers import ModelPicker, ProviderSetup
from codaro.ui.reviews import ExternalToolReview, ScopeReview
from codaro.ui.session_details import help_dialog, session_dialog

OUT = Path(os.environ.get("CODARO_UI_AUDIT_OUTPUT", ROOT / "artifacts/ui-audit-2026-10-27"))
OUT.mkdir(parents=True, exist_ok=True)


async def audit(root):
    manifest = []
    store = ProviderStore(
        root / "profiles",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "qwen-example", "context_window": 131072},
                        {"id": "embedding-only"},
                    ]
                },
            )
        ),
    )
    store.register(
        "openai-compatible", "synthetic-key", name="Example", base_url="https://example.test/v1"
    )

    def disable(value):
        value["profiles"]["Example"]["models"][1]["tools"] = False

    store.mutate(disable)
    error_store = ProviderStore(
        root / "errors",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(401, json={"error": {"message": "Invalid API key"}})
        ),
    )
    for size in [(60, 20), (80, 24), (120, 35)]:
        project = root / str(size[0])
        project.mkdir()
        (project / "main.py").write_text("x=1\n")
        agent = Agent(Repository(project), PreviewProvider(), persist_memory=False, mode="execute")
        app = CodaroApp(agent)
        async with app.run_test(size=size) as pilot:

            async def save(state, app=app, size=size, pilot=pilot):
                await pilot.pause()
                stem = f"{state}-{size[0]}x{size[1]}"
                (OUT / (stem + ".svg")).write_text(app.export_screenshot())
                (OUT / (stem + ".txt")).write_text(
                    "\n".join(s.text for s in app.screen._compositor.render_strips())
                )
                manifest.append({"state": state, "size": size, "file": stem + ".svg"})

            for screen, state in [
                (help_dialog(app), "help"),
                (session_dialog(app), "status"),
                (PreferencesDialog(), "preferences"),
                (NewConversation(), "new-conversation"),
            ]:
                app.push_screen(screen)
                await save(state)
                await pilot.press("escape")
            await pilot.press("ctrl+p")
            await save("palette")
            await pilot.press("escape")
            agent.tasks.start(
                "Atualizar o contrato público preservando a compatibilidade dos consumidores"
            )
            agent.tasks.plan(
                [{"title": "Implementar", "state": "todo"}, {"title": "Validar", "state": "todo"}],
                ["Testes aprovados"],
            )
            app.push_screen(ScopeReview(agent.tasks.current()))
            await save("scope-review")
            await pilot.press("escape")
            app.push_screen(
                ExternalToolReview(
                    project, "mcp:issue_tracker", "update_issue", {"issue": 123, "status": "done"}
                )
            )
            await save("external-approval")
            await pilot.press("escape")
            dialog = ModelPicker(store)
            app.push_screen(dialog)
            await save("model-incompatible")
            dialog.query_one("#model-search", Input).value = "nothing-matches"
            await save("model-empty")
            await pilot.press("escape")
            dialog = ProviderSetup(error_store)
            app.push_screen(dialog)
            await pilot.pause()
            dialog.query_one("#provider-key", Input).value = "synthetic-invalid"
            await dialog.test_connection()
            await save("provider-auth-error")
            await pilot.press("escape")
            app.show_user_question(
                {"question": "Qual contrato devemos preservar?", "choices": []}, lambda _: None
            )
            await save("question-free")
            app.question_paused = True
            app.query_one("#question-dock").set_paused(True)
            await save("question-paused")
            app.clear_user_question()
            app.show_plan()
            app.active_question = "Revise main.py e mantenha seu contrato."
            app.finish(
                "Conexão interrompida durante a geração.", successful=False, outcome="unavailable"
            )
            await save("connection-recovery")
            app.finish(
                "Atividade interrompida pelo usuário.", successful=False, outcome="cancelled"
            )
            await save("cancelled-recovery")
    (OUT / "states-manifest.json").write_text(json.dumps({"screens": manifest}, indent=2))
    print("Capturas adicionais:", len(manifest))


with tempfile.TemporaryDirectory(prefix="codaro-ui-states-") as t:
    os.environ["XDG_CONFIG_HOME"] = str(Path(t) / "settings")

    def loop_factory():
        loop = asyncio.new_event_loop()

        def heartbeat():
            if not loop.is_closed():
                loop.call_later(0.02, heartbeat)

        loop.call_later(0.02, heartbeat)
        return loop

    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(audit(Path(t)))
