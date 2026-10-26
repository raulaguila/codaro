"""Read-only UI audit of all 46 approved prototype scenarios.

Run from the repository: .venv/bin/python docs/audits/2026-10-21-all-screens-comparison/reproduce.py
Real Textual rendering; synthetic events and mocked HTTP, without model inference.
Missing flows are captured as their closest existing screen, never implemented here.
"""

import json
import os
import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

import httpx
from textual.widgets import Button, Collapsible, Input, Select

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests"))
os.environ.pop("NO_COLOR", None)
from test_tui import UIModel, run_ui  # noqa: E402

from codaro.agent import Agent, AgentEvent  # noqa: E402
from codaro.edits import EditProposal  # noqa: E402
from codaro.llm.profiles import ProviderStore  # noqa: E402
from codaro.preferences import PreferencesStore  # noqa: E402
from codaro.repository import Repository  # noqa: E402
from codaro.ui.app import CodaroApp  # noqa: E402
from codaro.ui.composer import Prompt  # noqa: E402
from codaro.ui.dialogs import NewConversation  # noqa: E402
from codaro.ui.messages import UserMessage  # noqa: E402
from codaro.ui.model_list import ModelList  # noqa: E402
from codaro.ui.plan import ExecutionPlan  # noqa: E402
from codaro.ui.preferences import PreferencesDialog  # noqa: E402
from codaro.ui.providers import ModelPicker, ProviderSetup  # noqa: E402
from codaro.ui.reviews import ChangesReview, CommandReview  # noqa: E402

OUT = Path(__file__).parent
CATALOG = json.loads((ROOT / "docs/design/ui-prototype/screens.json").read_text())
OBSERVATIONS = []
CHECKS = []
QUESTION = CATALOG["questions"]["ask-choice"]
ANSWER = (
    "A autenticação começa em `src/auth.py` e verifica as permissões do usuário.\n\n"
    "Encontrei duas melhorias:\n\n"
    "1. Tratar usuários ausentes antes de acessar permissões.\n"
    "2. Cobrir acesso permitido e negado nos testes."
)


class CatalogTransport:
    def __init__(self):
        self.status = 200
        self.release = threading.Event()
        self.release.set()

    def __call__(self, request):
        self.release.wait(5)
        if request.url.path.endswith("chat/completions"):
            payload = json.loads(request.content)
            if payload["messages"][-1]["role"] == "tool":
                marker = json.loads(payload["messages"][-1]["content"])["probe_result"]
                message = {"role": "assistant", "content": marker}
            else:
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "audit-probe",
                            "type": "function",
                            "function": {"name": "codaro_probe", "arguments": "{}"},
                        }
                    ],
                }
            return httpx.Response(200, json={"choices": [{"message": message}]})
        return httpx.Response(
            self.status,
            json={
                "data": [
                    {
                        "id": "qwen3.8:27b",
                        "context_window": 131072,
                        "supported_parameters": ["tools"],
                    },
                    {"id": "qwen2.5-coder:7b", "context_window": 32768},
                    {"id": "llama3.1:8b", "context_window": 131072},
                    {"id": "modelo-sem-metadados"},
                    {"id": "embedding-local", "supported_parameters": [], "type": "embedding"},
                ]
            }
            if self.status == 200
            else {"error": {"message": "Invalid API key"}},
        )


def capture(app, scene, size, note=""):
    name = f"{scene}-{size[0]}x{size[1]}"
    (OUT / "screens" / (name + ".svg")).write_text(app.export_screenshot())
    rendered = "\n".join(strip.text for strip in app.screen._compositor.render_strips())
    (OUT / "screens" / (name + ".txt")).write_text(rendered + "\n")
    controls = []
    for widget in app.screen.query("Button, Input, OptionList, TextArea"):
        if not widget.visible or not widget.display:
            continue
        try:
            clip = app.screen._compositor.find_widget(widget).clip
        except Exception:
            continue
        region = widget.region
        controls.append(
            {
                "id": widget.id,
                "type": type(widget).__name__,
                "region": list(region),
                "clip": list(clip),
                "fully_visible": region.area > 0 and region.intersection(clip) == region,
                "disabled": widget.disabled,
            }
        )
    OBSERVATIONS.append(
        {
            "scene": scene,
            "size": size,
            "capture": name,
            "screen": type(app.screen).__name__,
            "note": note,
            "controls": controls,
        }
    )


def check(name, passed, detail):
    CHECKS.append({"name": name, "passed": bool(passed), "detail": detail})


def seed_turn(app):
    app.hide_welcome()
    app.active_question = "Analise a autenticação e proponha melhorias."
    app.mount_message(UserMessage(app.active_question))
    app.activity(AgentEvent("model_start", "Consulta", context_tokens=16000, context_limit=122368))


def seed_activity(app):
    app.activity(AgentEvent("tool_end", "Ler linhas", "src/auth.py:1–80", "success", 12))
    app.activity(AgentEvent("tool_start", "Buscar testes", "can_edit"))


def seed_plan(app, state="executing"):
    app.agent.tasks.start("Corrigir a autenticação e validar")
    app.agent.tasks.plan(
        [
            {"title": "Investigar a autenticação", "state": "done"},
            {"title": "Corrigir a validação de usuários", "state": "doing"},
            {"title": "Cobrir os cenários de permissão", "state": "todo"},
            {"title": "Executar testes e revisar o resultado", "state": "todo"},
        ],
        ["Testes aprovados"],
    )
    app.agent.tasks.state(state)
    app.show_plan()


def show_question(app, scene, resolved=lambda _answer: None):
    q = CATALOG["questions"][scene]
    app.show_user_question(
        {
            "question": q["title"],
            "explanation": q["why"],
            "choices": q["choices"],
            "recommended": q["recommended"],
        },
        resolved,
    )


async def render_scene(directory, scene, size):
    directory.mkdir(parents=True)
    os.environ["XDG_CONFIG_HOME"] = str(directory / "user-config")
    project = directory / "codaro"
    project.mkdir()
    model = UIModel()
    model.settings = replace(model.settings, model="qwen3.8:27b", context_window=131072)
    agent = Agent(Repository(project), model, mode="execute", persist_memory=False)
    transport = CatalogTransport()
    store = ProviderStore(
        directory / "user-config/codaro", transport=httpx.MockTransport(transport)
    )
    store.register(
        "openai-compatible", "synthetic-key", name="Local", base_url="https://audit.invalid/v1"
    )
    store.select("Local", "qwen3.8:27b")
    app = CodaroApp(agent, setup_required=scene == "first-run")
    note = ""
    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.05)
        if scene == "first-run":
            pass
        elif scene.startswith("slash"):
            value = {"slash": "/", "slash-filter": "/mo", "slash-empty": "/xyz"}[scene]
            app.query_one(Prompt).value = value
            app.query_one(Prompt).move_cursor(app.query_one(Prompt).document.end)
            await pilot.pause(0.05)
            app.update_completions()
        elif scene in {"mode", "edit-approval"}:
            seed_turn(app)
            app.response_text = ANSWER
            app.flush_response()
            app.open_mode_picker()
            await pilot.pause(0.05)
            if scene == "edit-approval":
                app.screen.query_one("#edit-policy", Collapsible).collapsed = False
        elif scene.startswith("provider"):
            app.push_screen(ProviderSetup(store))
            await pilot.pause(0.05)
            dialog = app.screen
            kind = (
                "ollama"
                if scene == "provider-ollama"
                else ("openai-compatible" if scene == "provider-custom" else "openai")
            )
            dialog.query_one("#provider-kind", Select).value = kind
            dialog.query_one("#provider-key", Input).value = "synthetic-key"
            await pilot.pause(0.05)
            if scene == "provider-custom":
                dialog.query_one("#provider-url", Input).value = "https://audit.invalid/v1"
                dialog.query_one("#provider-advanced", Collapsible).collapsed = False
                dialog.query_one("#provider-tls").value = True
            if scene == "provider-testing":
                transport.release.clear()
                dialog.on_button_pressed(Button.Pressed(dialog.query_one("#provider-test")))
            elif scene in {"provider-success", "provider-error"}:
                transport.status = 401 if scene == "provider-error" else 200
                await dialog.test_connection()
        elif scene in {"models", "models-loading", "models-empty", "model-fallback", "model-tools"}:
            app.push_screen(ModelPicker(store))
            await pilot.pause(0.1)
            dialog = app.screen
            if scene == "models-empty":
                dialog.query_one("#model-search", Input).value = "modelo-inexistente"
            elif scene == "models-loading":
                transport.release.clear()
                dialog.on_button_pressed(Button.Pressed(dialog.query_one("#model-refresh")))
            elif scene == "model-fallback":
                dialog.query_one(ModelList).value = "modelo-sem-metadados"
                dialog.update_model("modelo-sem-metadados")
            elif scene == "model-tools":
                dialog.query_one("#model-details", Collapsible).collapsed = False
                await dialog.lookup(dialog.query_one("#model-test"))
        elif scene == "preferences":
            app.push_screen(PreferencesDialog(PreferencesStore(directory / "prefs")))
        elif scene == "new-session":
            app.query_one(Prompt).value = "Meu próximo pedido ainda não enviado"
            seed_plan(app)
            app.push_screen(NewConversation())
        elif scene in {"status", "help", "context-compacting"}:
            seed_turn(app)
            command = {"status": "/status", "help": "/help", "context-compacting": "/compact"}[
                scene
            ]
            await app.local_command(command)
            if scene == "context-compacting":
                note = "Compactação local sem inferência; histórico completo permanece salvo."
        elif scene in {"review-diff", "command-approval"}:
            seed_turn(app)
            seed_plan(app)
            if scene == "review-diff":
                proposal = EditProposal(
                    "audit",
                    "src/auth.py",
                    "Tratar usuário ausente",
                    b"return user.is_admin",
                    b"if user is None: return False",
                    "@@ can_edit(user)\n- return user.is_admin\n+ if user is None:\n"
                    "+     return False\n+ return user.is_admin",
                )
                app.push_screen(ChangesReview([proposal]))
            else:
                app.push_screen(
                    CommandReview(
                        project, ["python", "-m", "pytest", "tests/test_auth.py", "-q"], 60
                    )
                )
        elif scene == "resumed":
            seed_plan(app, "blocked")
            app.session.save(
                [
                    [
                        {"role": "user", "content": "Corrija a autenticação"},
                        {"role": "assistant", "content": ANSWER},
                    ]
                ],
                "qwen3.8:27b",
            )
            await app.restore_session()
            app.show_plan()
        elif scene != "welcome":
            seed_turn(app)
            if scene in {"conversation", "plan-chat"}:
                app.response_text = ANSWER
                app.flush_response()
                app.reply = app.speaker = None
                app.response_text = app.rendered_text = ""
                app.mount_message(UserMessage("Corrija esse caso mantendo o contrato atual."))
            seed_activity(app)
            if scene in {"thinking", "stages-expanded"}:
                app.append_reasoning("Vou comparar a regra atual com os testes de autorização.")
            if scene == "stages-expanded":
                await pilot.pause(0.1)
                app.activity_group.collapsed = False
                app.reasoning_group.collapsed = False
            if scene in {"streaming", "answer", "conversation", "done", "validating", "plan-chat"}:
                app.append_delta(ANSWER[:90] if scene == "streaming" else ANSWER)
            if scene == "done":
                seed_plan(app)
                task = agent.tasks.current()
                agent.tasks.plan(
                    [{**step, "state": "done"} for step in task["plan"]], task["criteria"]
                )
                agent.tasks.validation(
                    {"argv": ["python", "-m", "pytest"], "exit_code": 0, "timed_out": False}
                )
                agent.tasks.state("completed")
            if scene in {"answer", "conversation", "done"}:
                app.activity(AgentEvent("tool_end", "Buscar testes", "3 resultados", "success", 10))
                await pilot.pause(0.05)
                app.finish(ANSWER)
            if scene in {"ask-choice", "ask-text", "context-scope", "ask-followup"}:
                show_question(app, "ask-choice" if scene == "ask-followup" else scene)
                if scene == "ask-followup":
                    app.answer_user_question("")
                    app.append_delta("Vou preservar o contrato atual ao implementar e testar.")
                elif scene == "context-scope":
                    note = (
                        "Pergunta ilustrativa. O gatilho real de capacidade é verificado "
                        "nos testes de regressão."
                    )
            if scene.startswith("plan") or scene == "validating":
                seed_plan(
                    app,
                    "planned"
                    if scene == "plan"
                    else ("validating" if scene == "validating" else "executing"),
                )
                if scene == "plan":
                    await app.local_command("/plan")
                if scene == "plan-collapsed":
                    app.query_one(ExecutionPlan).expanded = False
                    app.show_plan()
                if scene == "validating":
                    app.activity(
                        AgentEvent(
                            "tool_start",
                            "Executar comando",
                            "python -m pytest tests/test_auth.py -q",
                        )
                    )
            if scene in {"connection-error", "auth-error", "cancelled"}:
                seed_plan(app, "blocked")
                kind = {
                    "connection-error": "unavailable",
                    "auth-error": "authentication",
                    "cancelled": "cancelled",
                }[scene]
                message = {
                    "connection-error": "Não foi possível conectar ao provedor.",
                    "auth-error": "API retornou HTTP 401. Confira a credencial.",
                    "cancelled": "Execução interrompida; alterações anteriores preservadas.",
                }[scene]
                await pilot.pause(0.05)
                app.finish(message, successful=False, outcome=kind)
            if scene == "no-results":
                app.activity(
                    AgentEvent("tool_end", "Buscar código", "Nenhum resultado", "success", 12)
                )
                await pilot.pause(0.05)
                app.finish(
                    "Não encontrei correspondências. Posso procurar pelo fluxo de login "
                    "ou você pode indicar um arquivo."
                )
            if scene == "done":
                note = (
                    "Recibo derivado de uma validação sintética no registro da tarefa; "
                    "não houve execução real de testes nesta captura."
                )
        if scene in {
            "thinking",
            "tool-running",
            "stages-expanded",
            "streaming",
            "ask-choice",
            "ask-text",
            "context-scope",
            "ask-followup",
            "plan-collapsed",
            "plan-expanded",
            "plan-chat",
            "validating",
        }:
            app.busy = True
            app.update_session_header()
        await pilot.pause(0.15)
        capture(app, scene, size, note)
        # Interactions use real keyboard dispatch after the baseline capture.
        if scene == "slash":
            before = app.query_one(Prompt).value
            await pilot.press("down")
            check(f"{size}/slash-arrows", app.completion_index == 1, "Seta move o destaque")
            await pilot.press("escape")
            check(
                f"{size}/slash-escape",
                app.query_one(Prompt).value == before,
                "Esc preserva o texto",
            )
        if scene == "slash-filter":
            await pilot.press("enter")
            await pilot.pause(0.1)
            check(
                f"{size}/slash-enter",
                type(app.screen).__name__ == "ModelPicker",
                f"Enter em /mo abriu {type(app.screen).__name__}",
            )
        if scene == "ask-choice":
            await pilot.press("down")
            app.query_one(Prompt).value = "/api/v1"
            await pilot.pause(0.05)
            check(
                f"{size}/question-literal-slash",
                not app.query_one("#completion").display,
                "Resposta começando com / não abre comandos",
            )
            await pilot.press("escape")
            check(
                f"{size}/question-pause-pending",
                app.pending_user_question is not None,
                "Esc mantém pergunta pendente",
            )
            capture(app, "question-paused-extra", size)
        if scene == "new-session":
            await pilot.press("escape")
            check(
                f"{size}/new-session-draft",
                app.query_one(Prompt).value.startswith("Meu próximo"),
                "Cancelar nova conversa preserva rascunho e plano",
            )
        if scene == "model-fallback":
            info = str(app.screen.query_one("#model-info").render())
            check(
                f"{size}/fallback-labelled",
                "provisório" in info.lower(),
                "Texto visível: " + info,
            )
        transport.release.set()
        if scene in {"models-loading", "provider-testing"}:
            await pilot.pause(0.2)
        assert not model.started.is_set(), "Audit must not perform model inference"


async def main(root):
    for size in [(120, 35), (80, 24)]:
        for item in CATALOG["screens"]:
            scene = item["id"]
            if len(sys.argv) > 1 and scene not in sys.argv[1:]:
                continue
            try:
                await render_scene(root / f"{scene}-{size[0]}", scene, size)
            except Exception as exc:
                OBSERVATIONS.append(
                    {"scene": scene, "size": size, "error": f"{type(exc).__name__}: {exc}"}
                )
                print(f"FAILED {scene} {size}: {exc}", flush=True)
        print(f"Completed {size}", flush=True)


if __name__ == "__main__":
    (OUT / "screens").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codaro-all-screens-") as directory:
        os.environ["XDG_CONFIG_HOME"] = directory
        run_ui(main(Path(directory)))
    for filename, records, key in [
        ("observations.json", OBSERVATIONS, lambda row: (row["scene"], tuple(row["size"]))),
        ("interactions.json", CHECKS, lambda row: row["name"]),
    ]:
        path = OUT / filename
        if len(sys.argv) > 1 and path.exists():
            replacements = {key(row) for row in records}
            records = [
                row for row in json.loads(path.read_text()) if key(row) not in replacements
            ] + records
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(OBSERVATIONS)} observations, {len(CHECKS)} interaction checks")
