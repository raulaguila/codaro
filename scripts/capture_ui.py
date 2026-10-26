"""Capture the real Textual UI with synthetic data and no provider requests.

Run from the repository root. Outputs go to ignored artifacts/ui by default.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

import httpx

from codaro.agent import Agent, AgentEvent
from codaro.llm import Settings
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Prompt
from codaro.ui.message_actions import FilePreview, MessageActions
from codaro.ui.messages import UserMessage
from codaro.ui.providers import ModelPicker, ProviderSetup
from codaro.ui.reviews import ChangesReview
from codaro.ui.sessions import SessionPicker


class PreviewProvider:
    settings = Settings("http://127.0.0.1:11434/v1", "preview-model")

    def complete(self, *args, **kwargs):
        raise AssertionError("Visual capture must never call a model.")


async def capture(output, root):
    manifest = []
    store = ProviderStore(
        directory=root / "profiles",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "qwen-local", "context_window": 131072},
                        {
                            "id": "company-finetuned-production-model-with-a-long-identifier",
                            "context_window": 32768,
                        },
                    ]
                },
            )
        ),
    )
    store.register(
        "openai-compatible", "synthetic-key", name="Local", base_url="https://demo.test/v1"
    )
    for width, height in ((60, 20), (80, 24), (120, 35)):
        project = root / f"project-{width}"
        project.mkdir()
        (project / "main.py").write_text('def greet(name):\n    return "hello " + name\n')
        agent = Agent(Repository(project), PreviewProvider(), persist_memory=False)
        agent.sessions.create("Revisar contrato da API")
        app = CodaroApp(agent)
        async with app.run_test(size=(width, height)) as pilot:

            async def save(state, app=app, width=width, height=height):
                await pilot.pause()
                name = f"{state}-{width}x{height}"
                (output / (name + ".svg")).write_text(app.export_screenshot(), encoding="utf-8")
                visible = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
                (output / (name + ".txt")).write_text(visible, encoding="utf-8")
                manifest.append({"state": state, "size": [width, height], "file": name + ".svg"})

            await save("welcome")
            await pilot.press("/")
            await save("commands")
            await pilot.press("escape")
            app.query_one(Prompt).value = ""
            app.open_mode_picker()
            await save("modes")
            await pilot.press("escape")
            app.show_user_question(
                {
                    "question": "Qual contrato devemos preservar?",
                    "choices": ["API atual", "Novo contrato"],
                    "recommended": 0,
                },
                lambda answer: None,
            )
            await save("question")
            app.clear_user_question()
            app.active_question = "Explique o objetivo deste projeto."
            app.finish("Este projeto apresenta uma conversa com decisões e validação explícitas.")
            await save("answer")
            app.busy = True
            app.active_question = "Revise o contrato da função greet."
            app.mount_message(UserMessage(app.active_question))
            app.activity(
                AgentEvent("model_start", "Investigando", context_tokens=8420, context_limit=30000)
            )
            app.append_reasoning(
                "Vou consultar o contrato e verificar o tratamento de nomes vazios."
            )
            app.activity(AgentEvent("tool_start", "Ler arquivo", "main.py", state="running"))
            app.activity(
                AgentEvent("tool_end", "Ler arquivo", "main.py:1–2", state="success", elapsed_ms=12)
            )
            await save("thinking")
            app.append_delta("O contrato de `main.py` pode preservar o retorno atual.\n\n")
            app.flush_preview()
            # Native metrics are synthetic here; never fabricate them in real use.
            app.activity(
                AgentEvent("generation_metrics", "Geração", output_tokens=120, generation_seconds=3)
            )
            await save("streaming")
            answer = (
                "## Contrato preservado\n\nVeja `main.py:2`.\n\n"
                "- Entradas válidas mantêm o resultado.\n"
                "- Nomes vazios precisam de um caso de teste.\n\n"
                '```python\nreturn "hello " + (name or "visitante")\n```'
            )
            app.finish(answer)
            await save("chat-final")
            app.push_screen(MessageActions(answer))
            await save("message-actions")
            await pilot.press("escape")
            app.push_screen(FilePreview("main.py", 2))
            await save("file-preview")
            await pilot.press("escape")
            agent.tasks.start("Preservar o contrato público")
            agent.tasks.plan(
                [
                    {
                        "title": f"Etapa {i + 1}: validar contrato e consumidores",
                        "state": "done" if i < 18 else "doing" if i == 18 else "todo",
                    }
                    for i in range(24)
                ],
                ["Testes aprovados"],
            )
            app.show_plan()
            await save("active-plan")
            app.show_user_question(
                {
                    "question": "Qual contrato devemos preservar para os consumidores atuais? " * 5,
                    "explanation": "Esta decisão afeta o contrato público e os testes existentes. "
                    * 8,
                    "choices": [
                        f"Opção {i + 1}: " + "preservar consumidores e validar cenários " * 4
                        for i in range(3)
                    ],
                    "recommended": 0,
                },
                lambda _: None,
            )
            await save("long-question")
            await pilot.click("#question-details")
            await save("question-details")
            await pilot.press("escape")
            app.clear_user_question()
            app.set_generation_status("Pronto")
            agent.tasks.state("cancelled")
            app.show_plan()
            app.push_screen(SessionPicker(agent.sessions, agent.session_id))
            await save("sessions")
            await pilot.press("escape")
            agent.edits.observe(
                agent.repository.read_lines("main.py", 1, 2), (project / "main.py").read_bytes()
            )
            result = agent.edits.propose(
                "main.py",
                'return "hello " + name',
                'return "hello " + (name or "visitante")',
                "Preservar o contrato e aceitar nomes ausentes",
            )
            app.push_screen(ChangesReview([agent.edits.proposals[result["proposal_id"]]]))
            await save("edit-review")
            await pilot.press("escape")
            app.push_screen(ModelPicker(store))
            await save("models")
            await pilot.press("escape")
            app.push_screen(ProviderSetup(store))
            await save("provider")
            await pilot.press("escape")
    (output / "manifest.json").write_text(
        json.dumps({"synthetic": True, "screens": manifest}, indent=2)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/ui"))
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codaro-ui-") as temporary:
        root = Path(temporary)
        # No user provider/preferences files are read, even when run locally.
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(root / "settings")
        try:
            # A timer also wakes executor shutdown in restricted headless runners.
            def loop_factory():
                loop = asyncio.new_event_loop()

                def heartbeat():
                    if not loop.is_closed():
                        loop.call_later(0.02, heartbeat)

                loop.call_later(0.02, heartbeat)
                return loop

            with asyncio.Runner(loop_factory=loop_factory) as runner:
                runner.run(capture(output, root))
        finally:
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous


if __name__ == "__main__":
    main()
