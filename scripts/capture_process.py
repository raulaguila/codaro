"""Capture the chronological agent process and its final disclosure without LLM calls."""

import argparse
import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from capture_ui import PreviewProvider

from codaro.agent import Agent, AgentEvent
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.messages import UserMessage


async def capture(root, output):
    for width, height in ((60, 20), (80, 24), (120, 35), (160, 45)):
        project = root / f"project-{width}"
        project.mkdir()
        (project / "main.py").write_text("def greet(name): return 'hello ' + name\n")
        app = CodaroApp(Agent(Repository(project), PreviewProvider()))
        async with app.run_test(size=(width, height)) as pilot:

            async def save(name, app=app, pilot=pilot, width=width, height=height):
                await pilot.pause()
                path = output / f"{name}-{width}x{height}"
                path.with_suffix(".svg").write_text(app.export_screenshot(), encoding="utf-8")
                path.with_suffix(".txt").write_text(
                    "\n".join(strip.text for strip in app.screen._compositor.render_strips()),
                    encoding="utf-8",
                )

            app.hide_welcome()
            app.busy = True
            app.generation_started = time.monotonic()
            app.active_question = "Analise a função greet e sugira uma melhoria."
            app.mount_message(UserMessage(app.active_question))
            app.append_reasoning("Vou ler a função e conferir como ela trata nomes vazios.")
            await save("01-thinking")
            app.finish_preview("tools")
            app.activity(AgentEvent("tool_start", "Ler arquivo", "main.py:1", "running"))
            await save("02-tool-running")
            app.activity(
                AgentEvent("tool_end", "Ler arquivo", "main.py:1 · função greet", "success", 12)
            )
            app.append_reasoning(
                "A API está clara. Vou conferir os testes antes de sugerir uma mudança."
            )
            await save("03-thinking-again")
            app.finish_preview("tools")
            app.activity(AgentEvent("tool_start", "Buscar testes", "greet", "running"))
            app.activity(
                AgentEvent("tool_end", "Buscar testes", "Nenhum teste encontrado.", "success", 9)
            )
            app.append_delta("A função concatena a saudação com o nome informado.")
            app.finish(
                "A função `greet` monta a saudação. Sugiro testar nomes vazios "
                "antes de alterar o contrato. Nenhum arquivo foi alterado."
            )
            await save("04-final")
            app.activity_group.collapsed = False
            await save("05-process-expanded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/process-ui"))
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codaro-process-ui-") as directory:
        root = Path(directory)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "settings")}):

            def loop_factory():
                loop = asyncio.new_event_loop()

                def heartbeat():
                    if not loop.is_closed():
                        loop.call_later(0.02, heartbeat)

                loop.call_later(0.02, heartbeat)
                return loop

            with asyncio.Runner(loop_factory=loop_factory) as runner:
                runner.run(capture(root, output))


if __name__ == "__main__":
    main()
