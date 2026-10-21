"""Compare approved vector mockups with captures of the real Textual application.

Run: .venv/bin/python docs/audits/2026-10-21-prototype-fidelity/reproduce.py
All model data, credentials and HTTP responses are synthetic.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from textual.widgets import Collapsible

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("NO_COLOR", None)

from test_tui import UIModel, run_ui  # noqa: E402

from codaro.agent import Agent, AgentEvent  # noqa: E402
from codaro.llm.profiles import ProviderStore  # noqa: E402
from codaro.repository import Repository  # noqa: E402
from codaro.ui.app import CodaroApp  # noqa: E402
from codaro.ui.messages import UserMessage  # noqa: E402
from codaro.ui.providers import ModelPicker, ProviderSetup  # noqa: E402

OUT = Path(__file__).parent
OBSERVATIONS = []


def capture(app, name):
    (OUT / f"{name}.svg").write_text(app.export_screenshot(), encoding="utf8")
    text = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
    (OUT / f"{name}.txt").write_text(text + "\n", encoding="utf8")
    controls = {}
    for selector in [
        "#prompt",
        "#select-mode",
        "#question-send",
        "#mode-apply",
        "#provider-save",
        "#model-use",
    ]:
        for widget in app.screen.query(selector):
            if widget.display:
                clip = app.screen._compositor.find_widget(widget).clip
                controls[selector] = {
                    "region": list(widget.region),
                    "fully_visible": widget.region.intersection(clip) == widget.region,
                }
    OBSERVATIONS.append({"capture": name, "size": list(app.size), "controls": controls})


def catalog(_request):
    return httpx.Response(
        200,
        json={
            "data": [
                {"id": "qwen3.8:27b", "context_window": 131072, "supported_parameters": ["tools"]},
                {"id": "llama3.1:8b", "context_window": 131072, "supported_parameters": ["tools"]},
                {"id": "qwen2.5-coder:7b", "context_window": 32768},
            ]
        },
    )


async def scenario(root, size):
    model = UIModel()
    from dataclasses import replace

    model.settings = replace(model.settings, model="qwen3.8:27b", context_window=131072)
    agent = Agent(Repository(root / "codaro"), model, mode="execute", persist_memory=False)
    app = CodaroApp(agent)
    suffix = f"{size[0]}x{size[1]}"
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        capture(app, "welcome-" + suffix)
        app.hide_welcome()
        app.mount_message(UserMessage("Como funciona a autenticação?"))
        app.response_text = (
            "O fluxo começa em src/auth.py. Falta tratar usuários ausentes "
            "antes de consultar as permissões."
        )
        app.flush_response()
        app.reply = app.speaker = None
        app.mount_message(UserMessage("Corrija esse caso mantendo o contrato atual."))
        app.activity(AgentEvent("tool_end", "Ler linhas", "src/auth.py:1–80", "success", 12))
        app.activity(AgentEvent("tool_start", "Conferir testes", "tests/test_auth.py"))
        app.append_reasoning("Vou conferir a validação atual e os testes relacionados.")
        agent.tasks.start("Corrigir a autenticação e validar")
        agent.tasks.plan(
            [
                {"title": "Investigar a autenticação", "state": "done"},
                {"title": "Corrigir a validação de usuários", "state": "doing"},
                {"title": "Cobrir os cenários de permissão", "state": "todo"},
                {"title": "Executar testes e revisar o resultado", "state": "todo"},
            ],
            ["Testes aprovados"],
        )
        app.show_plan()
        app.activity(
            AgentEvent("model_start", "Consulta", context_tokens=16000, context_limit=122368)
        )
        app.query_one("#status").update("Em execução")
        await pilot.pause()
        capture(app, "plan-chat-" + suffix)
        app.activity_group.collapsed = False
        app.reasoning_group.collapsed = False
        await pilot.pause()
        capture(app, "stages-expanded-" + suffix)
        app.activity_group.collapsed = True
        app.append_delta(
            "Vou preservar o contrato atual e corrigir a validação de usuários ausentes."
        )
        await pilot.pause()
        capture(app, "streaming-" + suffix)
        app.finish_preview("tools")
        app.show_user_question(
            {
                "question": "Podemos alterar o contrato da autenticação?",
                "explanation": "Essa escolha define se a mudança pode exigir ajustes nos clientes.",
                "choices": [
                    "Preservar o contrato atual",
                    "Pode alterar o contrato",
                    "Preciso de outra abordagem",
                ],
                "recommended": 0,
            },
            lambda _answer: None,
        )
        await pilot.pause()
        capture(app, "question-" + suffix)
        app.clear_user_question()
        app.query_one("#status").update("Pronto")
        app.open_mode_picker()
        await pilot.pause()
        capture(app, "mode-" + suffix)
        app.screen.query_one("#edit-policy", Collapsible).collapsed = False
        await pilot.pause()
        capture(app, "edit-policy-" + suffix)
        await pilot.press("escape")
        store = ProviderStore(root / "config", transport=httpx.MockTransport(catalog))
        store.register(
            "openai-compatible", "synthetic-key", name="Local", base_url="https://audit.invalid/v1"
        )
        store.select("Local", "qwen3.8:27b")
        app.push_screen(ProviderSetup(store))
        await pilot.pause()
        capture(app, "provider-" + suffix)
        await pilot.press("escape")
        app.push_screen(ModelPicker(store))
        await pilot.pause()
        capture(app, "models-" + suffix)
        await pilot.press("escape")
        assert not model.started.is_set()


def compare():
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)

    def tag(name):
        return f"{{{ns}}}{name}"

    svg = ET.Element(tag("svg"), width="2300", height="880", viewBox="0 0 2300 880")
    ET.SubElement(svg, tag("rect"), width="2300", height="880", fill="#0b1017")
    for index, (label, path) in enumerate(
        [
            ("Protótipo aprovado", ROOT / "docs/design/ui-prototype/screens/plan-chat.svg"),
            ("CLI atual · captura real Textual", OUT / "plan-chat-120x35.svg"),
        ]
    ):
        text = ET.SubElement(
            svg,
            tag("text"),
            x=str(24 + index * 1140),
            y="35",
            fill="#dfe6ef",
            style="font:20px sans-serif",
        )
        text.text = label
        source = ET.parse(path).getroot()
        frame = ET.SubElement(
            svg,
            tag("svg"),
            x=str(24 + index * 1140),
            y="60",
            width="1110",
            height="790",
            viewBox=source.get("viewBox", f"0 0 {source.get('width')} {source.get('height')}"),
        )
        for child in source:
            frame.append(copy.deepcopy(child))
    ET.ElementTree(svg).write(OUT / "comparison.svg", encoding="unicode")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="codaro-fidelity-") as directory:
        for size in [(60, 20), (80, 24), (120, 35), (160, 50)]:
            root = Path(directory) / f"{size[0]}x{size[1]}"
            (root / "codaro").mkdir(parents=True)
            run_ui(scenario(root, size))
    (OUT / "observations.json").write_text(
        json.dumps(OBSERVATIONS, ensure_ascii=False, indent=2) + "\n"
    )
    compare()
    with tempfile.TemporaryFile() as log:
        for name in [
            "plan-chat-120x35",
            "mode-120x35",
            "question-120x35",
            "models-120x35",
            "provider-120x35",
            "comparison",
        ]:
            subprocess.run(
                [
                    "inkscape",
                    str(OUT / (name + ".svg")),
                    "--export-type=png",
                    "--export-filename=" + str(OUT / (name + ".png")),
                ],
                check=True,
                stdout=log,
                stderr=log,
            )
    print(f"Captured {len(OBSERVATIONS)} native states; comparison and six previews exported")
