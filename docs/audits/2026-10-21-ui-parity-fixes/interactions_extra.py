"""Additional lifecycle and compact-layout checks for the comparative audit."""

import json
import os
import tempfile
from pathlib import Path

import httpx
from reproduce import (
    CHECKS,
    OBSERVATIONS,
    OUT,
    Agent,
    AgentEvent,
    CatalogTransport,
    CodaroApp,
    Prompt,
    ProviderSetup,
    ProviderStore,
    Repository,
    UIModel,
    capture,
    check,
    run_ui,
    seed_plan,
    show_question,
)

from codaro.ui.dialogs import ProviderManager
from codaro.ui.plan import ExecutionPlan


async def scenario(root):
    project = root / "codaro"
    project.mkdir()
    agent = Agent(Repository(project), UIModel(), mode="execute", persist_memory=False)
    app = CodaroApp(agent)
    store = ProviderStore(root / "providers", transport=httpx.MockTransport(CatalogTransport()))
    store.register(
        "openai-compatible", "synthetic-key", name="Local", base_url="https://audit.invalid/v1"
    )
    async with app.run_test(size=(120, 35)) as pilot:
        await pilot.pause()
        mode = app.query_one("#select-mode")
        before = mode.region
        await pilot.hover("#select-mode")
        await pilot.pause()
        check("hover-stable-size", mode.region == before, "Hover no modo não muda dimensões")
        await pilot.press("ctrl+p")
        await pilot.pause()
        capture(app, "palette-extra", (120, 35))
        await pilot.press("escape")
        app.push_screen(ProviderManager(store))
        await pilot.pause()
        capture(app, "provider-manager-extra", (120, 35))
        await pilot.press("escape")
        # Confirm a completed tool does not remain announced as running.
        app.activity(AgentEvent("tool_start", "Buscar testes", "can_edit"))
        await pilot.pause()
        app.activity(AgentEvent("tool_end", "Buscar testes", "3 resultados", "success", 12))
        await pilot.pause()
        stages = app.activity_group.parent
        check(
            "finished-tool-not-running",
            not stages.current,
            f"Resumo após fim da ferramenta: completed={stages.completed!r}, "
            f"current={stages.current!r}",
        )
        capture(app, "finished-tool-extra", (120, 35))
        app.activity_group.collapsed = True
        app.activity(AgentEvent("tool_end", "Ler linhas", "src/auth.py:1–10", "success", 2))
        await pilot.pause()
        check(
            "stages-stay-collapsed",
            app.activity_group.collapsed,
            "Evento novo preserva recolhimento manual dos detalhes",
        )
        seed_plan(app)
        app.query_one(ExecutionPlan).toggle()
        app.show_plan()
        app.activity(AgentEvent("tool_end", "Ler linhas", "tests/test_auth.py", "success", 2))
        await pilot.pause()
        check(
            "plan-stays-collapsed",
            not app.query_one(ExecutionPlan).expanded,
            "Atualização de ferramenta mantém plano recolhido junto ao input",
        )
        # Persist and restore a question/answer that the final response does not repeat.
        app.active_question = "Analise o contrato da API"
        responses = []
        show_question(app, "ask-choice", responses.append)
        await pilot.pause()
        app.answer_user_question("Preservar /api/v2 por compatibilidade")
        app.finish("Análise concluída.")
        await pilot.pause()
        await app.restore_session()
        await pilot.pause()
        visible = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
        check(
            "question-decision-restored",
            "Preservar /api/v2" in visible,
            "A decisão deve continuar no histórico visível após /resume",
        )
        capture(app, "question-restored-extra", (120, 35))
        seed_plan(app)
        app.query_one(ExecutionPlan).expanded = True
        app.show_plan()
        app.query_one(Prompt).value = "rascunho preservado"
        await pilot.resize_terminal(60, 20)
        await pilot.pause(0.1)
        check(
            "resize-short-layout",
            app.has_class("short-terminal"),
            f"Após resize 120×35 → 60×20: app.size={app.size}, classes={sorted(app.classes)}",
        )
        show_question(app, "ask-choice")
        await pilot.pause()
        capture(app, "question-plan-extra", (60, 20))
        app.clear_user_question()
        app.busy = True
        app.update_session_header()
        app.open_mode_picker()
        check(
            "mode-available-during-execution",
            type(app.screen).__name__ == "ModePicker",
            "O modo pode ser preparado para o próximo pedido",
        )
        if type(app.screen).__name__ == "ModePicker":
            await pilot.press("escape")
        app.busy = False
        app.push_screen(ProviderSetup(store))
        await pilot.pause()
        capture(app, "provider-extra", (60, 20))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="codaro-ui-extra-") as directory:
        os.environ["XDG_CONFIG_HOME"] = directory
        run_ui(scenario(Path(directory)))
    (OUT / "extra-interactions.json").write_text(
        json.dumps(CHECKS, ensure_ascii=False, indent=2) + "\n"
    )
    (OUT / "extra-observations.json").write_text(
        json.dumps(OBSERVATIONS, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(CHECKS, ensure_ascii=False, indent=2))
