"""Run outside the checkout against an installed wheel, with no real model calls."""

import asyncio
import json
import subprocess
import sys
import tempfile
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import codaro
from codaro.agent import Agent
from codaro.index import CodeIndex
from codaro.llm import Settings
from codaro.navigation import file_outline
from codaro.project_map import ProjectMap
from codaro.repository import Repository
from codaro.ui.app import CodaroApp
from codaro.ui.composer import Composer, Prompt
from codaro.ui.modes import ModePicker
from codaro.ui.plan import ExecutionPlan


class Model:
    settings = Settings("http://localhost:11434/v1", "package-smoke")

    def complete(self, messages, tools=None):
        return {"content": "Resposta de teste do pacote."}


async def scenario():
    with tempfile.TemporaryDirectory(prefix="codaro-package-") as directory:
        root = Path(directory)
        (root / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
        (root / "schema.sql").write_text("CREATE TABLE users (id INTEGER);\n", encoding="utf-8")
        repository = Repository(root)
        with CodeIndex(repository) as index:
            assert index.search("main", language="go")[0]["symbol"] == "main"
            assert index.search("users", kind="table")[0]["path"] == "schema.sql"
            assert ProjectMap().build(index)["languages"]["go"] == 1
        assert file_outline(repository, "main.go")["results"][0]["symbol"] == "main"
        agent = Agent(Repository(Path(directory)), Model())
        assert "Resposta de teste" in agent.ask("Olá")
        trace = json.loads((Path(directory) / ".codaro/prompt.json").read_text(encoding="utf-8"))
        assert trace["totals"]["model_requests"] == 1
        async with CodaroApp(agent).run_test(size=(120, 35)) as pilot:
            app = pilot.app
            assert app.query_one(Prompt)
            assert app.query_one(Composer)
            assert app.query_one(ExecutionPlan).parent is app.query_one(Composer)
            app.open_mode_picker()
            await pilot.pause()
            assert isinstance(app.screen, ModePicker)
            await pilot.press("escape")
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert "command-palette" in app.screen.id


def loop_factory():
    loop = asyncio.new_event_loop()

    def heartbeat():
        if not loop.is_closed():
            loop.call_later(0.02, heartbeat)

    loop.call_later(0.02, heartbeat)
    return loop


def main():
    assert "site-packages" in str(Path(codaro.__file__).resolve()), "Teste requer pacote instalado"
    assert version("codaro") == codaro.__version__
    assert files("codaro.ui").joinpath("styles.tcss").is_file()
    subprocess.run(
        [sys.executable, "-c", "from codaro.cli import app; app()", "--help"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(scenario())
    print("Installed package: CLI, imports, CSS, agent, diagnostics and UI passed.")


if __name__ == "__main__":
    main()
