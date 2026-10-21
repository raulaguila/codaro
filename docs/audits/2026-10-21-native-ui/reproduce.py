"""Capture the running Textual UI with synthetic state and no network requests.

Run: .venv/bin/python docs/audits/2026-10-21-native-ui/reproduce.py
"""

import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from textual.widgets import OptionList, Static

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("NO_COLOR", None)

from test_tui import UIModel, run_ui  # noqa: E402

from codaro.agent import Agent, AgentEvent  # noqa: E402
from codaro.repository import Repository  # noqa: E402
from codaro.ui.app import CodaroApp  # noqa: E402
from codaro.ui.modes import ModePicker  # noqa: E402
from codaro.ui.plan import ExecutionPlan  # noqa: E402

OUT = Path(__file__).parent
OBSERVATIONS = []


def capture(app, name):
    (OUT / f"{name}.svg").write_text(app.export_screenshot(), encoding="utf8")
    text = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
    (OUT / f"{name}.txt").write_text(text + "\n", encoding="utf8")
    OBSERVATIONS.append(
        {
            "capture": name,
            "size": [app.size.width, app.size.height],
            "geometry": {
                selector: list(app.query_one(selector).region)
                for selector in ["#conversation", "#composer", "#prompt", "#execution-plan"]
            },
        }
    )


async def scenario(root, size):
    model = UIModel()
    model.settings = replace(model.settings, model="qwen3.8:27b")
    agent = Agent(Repository(root), model, mode="execute", persist_memory=False)
    app = CodaroApp(agent)
    suffix = f"{size[0]}x{size[1]}"
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        capture(app, "welcome-" + suffix)
        agent.tasks.start("Corrigir a autenticação e validar o resultado")
        agent.tasks.plan(
            [
                {"title": "Investigar a autenticação", "state": "done"},
                {"title": "Corrigir a validação de usuários", "state": "doing"},
                {"title": "Cobrir os cenários de permissão", "state": "todo"},
                {"title": "Executar testes e revisar o resultado", "state": "todo"},
            ],
            ["Testes aprovados"],
        )
        app.hide_welcome()
        app.mount_message(Static("Como funciona a autenticação?", classes="question"))
        app.response_text = "A validação fica em src/auth.py. Falta tratar usuários ausentes."
        app.flush_response()
        app.reply = app.speaker = None
        app.mount_message(
            Static("Corrija esse caso mantendo o contrato atual.", classes="question")
        )
        app.activity(AgentEvent("tool_end", "Ler linhas", "src/auth.py:1–80", "success", 12))
        app.append_reasoning("Vou conferir a validação atual e seus testes.")
        app.show_plan()
        await pilot.pause()
        capture(app, "plan-chat-" + suffix)
        app.query_one(ExecutionPlan).toggle()
        app.show_plan()
        await pilot.pause()
        capture(app, "plan-collapsed-" + suffix)
        app.open_mode_picker()
        await pilot.pause()
        capture(app, "mode-" + suffix)
        app.screen.query_one(OptionList).highlighted = list(app.screen.modes).index(agent.mode)
        app.screen.query_one("#edit-policy").collapsed = False
        await pilot.pause()
        capture(app, "edit-policy-" + suffix)
        assert isinstance(app.screen, ModePicker)
        await pilot.press("escape")
        app.cancel_execution_plan()
        await pilot.pause()
        capture(app, "plan-cancelled-" + suffix)
        assert not model.started.is_set()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="codaro-native-ui-") as directory:
        for size in [(60, 20), (80, 24), (120, 35), (160, 50)]:
            run_ui(scenario(Path(directory), size))
    (OUT / "observations.json").write_text(
        json.dumps(OBSERVATIONS, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    print(f"Captured {len(OBSERVATIONS)} native UI states without model requests")
