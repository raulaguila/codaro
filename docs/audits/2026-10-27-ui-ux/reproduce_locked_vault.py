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
from capture_ui import PreviewProvider

from codaro.agent import Agent
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.dialogs import ProviderManager


class Locked:
    def get(self, *_):
        raise ValueError("Cofre bloqueado de teste")


async def check(root):
    (ROOT / "artifacts/ui-audit-2026-10-27").mkdir(parents=True, exist_ok=True)
    store = ProviderStore(root / "profiles", vault=Locked())
    store.mutate(
        lambda d: d["profiles"].update(
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
    data = json.loads(store.path.read_text())
    data["version"] = 2
    data["profiles"]["locked"]["credential_ref"] = "a" * 32
    store.path.write_text(json.dumps(data))
    store.path.chmod(0o600)
    repo = root / "project"
    repo.mkdir()
    app = CodaroApp(Agent(Repository(repo), PreviewProvider(), persist_memory=False))
    import codaro.llm.profiles as profiles

    previous = profiles.ProviderStore
    profiles.ProviderStore = lambda: store
    outcome = {}
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ProviderManager(store), app.manage_provider_decision)
            await pilot.pause()
            await pilot.click("#profile-edit-locked")
            await pilot.pause()
            outcome["app_exception"] = repr(app._exception)
    except Exception as exc:
        outcome.update(raised=type(exc).__name__, message=str(exc))
    finally:
        profiles.ProviderStore = previous
    (ROOT / "artifacts/ui-audit-2026-10-27/locked-vault-result.json").write_text(
        json.dumps(outcome, indent=2)
    )
    print(outcome)


with tempfile.TemporaryDirectory() as t:
    os.environ["XDG_CONFIG_HOME"] = str(Path(t) / "settings")
    with asyncio.Runner() as runner:
        runner.run(check(Path(t)))
