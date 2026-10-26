"""Capture the real Textual UI with synthetic data and no provider requests.

Run from the repository root. Outputs go to ignored artifacts/ui by default.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from codaro.agent import Agent
from codaro.llm import Settings
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.providers import ProviderSetup


class PreviewProvider:
    settings = Settings("http://127.0.0.1:11434/v1", "preview-model")

    def complete(self, *args, **kwargs):
        raise AssertionError("Visual capture must never call a model.")


async def capture(output, root):
    manifest = []
    for width, height in ((80, 24), (120, 35)):
        app = CodaroApp(Agent(Repository(root), PreviewProvider(), persist_memory=False))
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
            app.push_screen(ProviderSetup(ProviderStore(directory=root / "config")))
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
