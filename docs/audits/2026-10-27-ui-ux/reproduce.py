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
from textual.widgets import Button, Checkbox, Collapsible, Input, Select

from codaro.agent import Agent
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui import provider_actions
from codaro.ui.app import CodaroApp
from codaro.ui.dialogs import ProviderManager
from codaro.ui.providers import ProviderSetup
from codaro.ui.reviews import CommandReview

OUTPUT = Path(os.environ.get("CODARO_UI_AUDIT_OUTPUT", ROOT / "artifacts/ui-audit-2026-10-27"))
OUTPUT.mkdir(parents=True, exist_ok=True)
results = {"screens": [], "hover": [], "observations": []}


class LockedVault:
    def get(self, *args):
        raise ValueError(
            "Cofre indisponível ou bloqueado; nenhuma credencial alternativa foi usada."
        )

    def delete(self, *args):
        return True


async def audit(root):
    store = ProviderStore(
        root / "profiles",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"models": [{"name": "example-model"}]})
        ),
    )
    store.register("ollama", "", name="Local", base_url="http://localhost:11434")
    for size in [(60, 20), (80, 24), (120, 35)]:
        project = root / f"project-{size[0]}"
        project.mkdir()
        (project / "main.py").write_text("x = 1\n")
        agent = Agent(Repository(project), PreviewProvider(), mode="execute", persist_memory=False)
        app = CodaroApp(agent)
        async with app.run_test(size=size) as pilot:

            async def save(state, app=app, size=size, pilot=pilot):
                await pilot.pause()
                stem = f"{state}-{size[0]}x{size[1]}"
                (OUTPUT / (stem + ".svg")).write_text(app.export_screenshot())
                text = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
                (OUTPUT / (stem + ".txt")).write_text(text)
                entry = {
                    "state": state,
                    "size": size,
                    "file": stem + ".svg",
                    "focus": app.focused.id if app.focused else None,
                }
                results["screens"].append(entry)
                return text

            async def hover_buttons(state, app=app, size=size, pilot=pilot):
                for button in list(app.screen.query(Button)):
                    if (
                        not button.display
                        or not button.visible
                        or button.region.width <= 0
                        or button.disabled
                    ):
                        continue
                    before = button.region
                    label = str(button.label)
                    try:
                        await pilot.hover(button)
                        await pilot.pause(0.05)
                    except Exception:
                        continue
                    results["hover"].append(
                        {
                            "state": state,
                            "size": size,
                            "id": button.id,
                            "label": label,
                            "stable_geometry": button.region == before,
                            "color": str(button.styles.color),
                            "background": str(button.styles.background),
                            "region": list(button.region),
                        }
                    )

            await hover_buttons("header")
            app.push_screen(ProviderManager(store))
            text = await save("provider-manager-http")
            await hover_buttons("provider-manager")
            results["observations"].append(
                {
                    "case": "http_transport_label",
                    "size": size,
                    "http_url_visible": "http://localhost:11434" in text,
                    "claims_verified_tls": "TLS verificado" in text,
                }
            )
            await pilot.press("escape")
            dialog = ProviderSetup(store)
            app.push_screen(dialog)
            await pilot.pause()
            dialog.query_one("#provider-kind", Select).value = "openai-compatible"
            dialog.query_one("#provider-key", Input).value = "synthetic-value"
            dialog.query_one("#provider-advanced", Collapsible).collapsed = False
            dialog.query_one("#provider-vault", Checkbox).focus()
            await save("provider-vault")
            await hover_buttons("provider-vault")
            await pilot.press("escape")
            app.push_screen(
                CommandReview(project / "service", ["python", "-m", "pytest", "-q"], 300)
            )
            await save("command-cwd")
            await hover_buttons("command-cwd")
            await pilot.press("escape")
            agent.tasks.start("Implementar e validar")
            agent.tasks.plan(
                [
                    {"title": "Implementação", "state": "done"},
                    {"title": "Testes", "state": "doing"},
                ],
                ["Testes aprovados"],
            )
            app.show_plan()
            app.busy = True
            job = agent.jobs.start(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    'import time; print("running",flush=True); time.sleep(30)',
                ],
                purpose="validation",
                task=agent.tasks.current(),
            )
            for _ in range(100):
                if "running" in agent.jobs.poll(job["job_id"])["output"]:
                    break
                await asyncio.sleep(0.02)
            app.action_cancel()
            await asyncio.sleep(0.1)
            results["observations"].append(
                {
                    "case": "cancel_ui_background_job",
                    "size": size,
                    "cancel_flag": app.cancelled.is_set(),
                    "job_after_cancel": agent.jobs.poll(job["job_id"])["state"],
                }
            )
            await save("cancel-with-background-job")
            app.busy = False
            app.cancel_execution_plan()
            results["observations"].append(
                {
                    "case": "cancel_plan_background_job",
                    "size": size,
                    "plan_state": agent.tasks.current()["state"],
                    "job_after_plan_cancel": agent.jobs.poll(job["job_id"])["state"],
                }
            )
            await save("cancelled-plan-background-job")
            agent.jobs.close()
            app.cancelled.clear()
            app.busy = False
            app.theme = "textual-light"
            await save("light-theme")
            await hover_buttons("light-header")
        # Actual provider-management edit callback receives a locked key reference.
    locked = ProviderStore(root / "locked", vault=LockedVault())
    locked.mutate(
        lambda value: value["profiles"].update(
            locked={
                "kind": "openai-compatible",
                "base_url": "https://example.test/v1",
                "api_key": "",
                "tls_insecure": False,
                "model": "",
                "models": [],
            }
        )
    )
    raw = json.loads(locked.path.read_text())
    raw["version"] = 2
    raw["profiles"]["locked"]["credential_ref"] = "a" * 32
    locked.path.write_text(json.dumps(raw))
    locked.path.chmod(0o600)
    project = root / "locked-project"
    project.mkdir()
    app = CodaroApp(Agent(Repository(project), PreviewProvider(), persist_memory=False))
    async with app.run_test(size=(120, 35)) as pilot:
        import codaro.llm.profiles as profiles

        previous = profiles.ProviderStore
        profiles.ProviderStore = lambda: locked
        try:
            try:
                provider_actions.manage_provider_decision(app, "edit:locked")
                await pilot.pause()
                results["observations"].append(
                    {
                        "case": "edit_locked_vault_callback",
                        "recovered": app._exception is None,
                        "screen": type(app.screen).__name__,
                    }
                )
            except Exception as exc:
                results["observations"].append(
                    {
                        "case": "edit_locked_vault_callback",
                        "uncaught_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
        finally:
            profiles.ProviderStore = previous
    (OUTPUT / "probe-results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(
        json.dumps(
            {
                "screens": len(results["screens"]),
                "hover_checks": len(results["hover"]),
                "unstable_hover": sum(not item["stable_geometry"] for item in results["hover"]),
                "observations": results["observations"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


with tempfile.TemporaryDirectory(prefix="codaro-ui-audit-extra-") as temporary:
    os.environ["XDG_CONFIG_HOME"] = str(Path(temporary) / "settings")

    def loop_factory():
        loop = asyncio.new_event_loop()

        def heartbeat():
            if not loop.is_closed():
                loop.call_later(0.02, heartbeat)

        loop.call_later(0.02, heartbeat)
        return loop

    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(audit(Path(temporary)))
