import json

import httpx
import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent, is_information_request
from codaro.policies import Mode
from codaro.provider import ModelError, Settings, create_provider
from codaro.repository import Repository
from codaro.tui import GenerationPreview


def test_consultation_preserves_old_pending_task_and_prefetches_sources(tmp_path):
    (tmp_path / "README.md").write_text("Codaro ajuda desenvolvedores.\n")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\ncodaro="codaro.cli:app"\n')
    model = FakeModel([{"content": "Codaro ajuda desenvolvedores, com entrada codaro.cli:app."}])
    agent = Agent(Repository(tmp_path), model, mode="execute")
    agent.tasks.start("Implemente uma funcionalidade")
    agent.tasks.update(lambda task: task.update(revision=1, state="blocked"))
    previous = agent.tasks.current()
    answer = agent.ask("o que pode me falar sobre o projeto atual?")
    assert "Validação pendente" not in answer
    assert agent.tasks.current() == previous
    assert agent.mode == Mode.EXECUTE and agent.allow_edits
    assert len(model.requests) == 1
    prompt, tools = model.requests[0]
    assert "Codaro ajuda desenvolvedores" in prompt[0]["content"]
    assert "codaro.cli:app" in prompt[0]["content"]
    assert not {"apply_changes", "run_command", "finish_task"}.intersection(
        tool["function"]["name"] for tool in tools
    )
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["status"] == "success" and flow["intent"] == "consultation"


def test_consultation_restores_mode_on_provider_failure(tmp_path):
    class Broken(FakeModel):
        def complete(self, *args, **kwargs):
            raise ModelError("Falha simulada")

    agent = Agent(Repository(tmp_path), Broken([]), mode="execute")
    agent.tasks.start("Crie uma funcionalidade")
    previous = agent.tasks.current()
    with pytest.raises(ModelError):
        agent.ask("Explique o projeto")
    assert agent.mode == Mode.EXECUTE and agent.allow_edits
    assert agent.tasks.current() == previous


@pytest.mark.parametrize(
    "question",
    [
        "Explique o projeto e implemente uma funcionalidade",
        "Como está? Continue a implementação",
        "Crie um módulo",
        "Valide as alterações",
    ],
)
def test_action_requests_keep_execution_mode(question):
    assert not is_information_request(question)


def test_validation_retry_feeds_answer_back_and_stops_identical_repetition(tmp_path):
    answer = "Criei o arquivo mas não fiz a verificação."
    model = FakeModel(
        [
            call(
                "apply_changes",
                {
                    "reason": "Criar módulo",
                    "operations": [{"kind": "create", "path": "x.py", "content": "x=1\n"}],
                },
            ),
            {"content": answer},
            {"content": answer},
        ]
    )
    agent = Agent(Repository(tmp_path), model, mode="execute", approve_edit=lambda *_: True)
    result = agent.ask("Crie o módulo e valide")
    assert "Validação pendente" in result
    assert len(model.requests) == 3
    assert {"role": "assistant", "content": answer} in model.requests[-1][0]
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["status"] == "blocked"
    assert flow["turns"][1]["outcome"] == "validation_repair"
    assert flow["turns"][2]["outcome"] == "blocked_no_progress"


def test_ollama_truncation_retains_reason_usage_and_explicit_preview_event(tmp_path):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "length" if len(calls) == 1 else "stop",
                "prompt_eval_count": 3000,
                "eval_count": 1400 if len(calls) == 1 else 10,
                "message": {
                    "role": "assistant",
                    "content": "Início. " if len(calls) == 1 else "Fim.",
                },
            },
        )

    provider = create_provider(
        Settings("http://localhost:11434", "qwen2.5:7b", api_style="ollama"),
        transport=httpx.MockTransport(handler),
    )
    events = []
    agent = Agent(Repository(tmp_path), provider, mode="ask")
    assert agent.ask("Olá", on_detail=events.append) == "Início. Fim."
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    attempt = flow["turns"][0]["http_attempts"][0]
    assert attempt["finish_reason"] == "length"
    assert attempt["usage"]["completion_tokens"] == 1400
    retry = next(event for event in events if event.state == "retry")
    assert retry.title == "Resposta interrompida"
    assert "Continue apenas a resposta" in calls[1]["messages"][0]["content"]
    assert "tools" not in calls[1]


def test_preview_displays_specific_retry_reason():
    preview = GenerationPreview()
    preview.finish("retry", "Limite de resposta atingido · Gerando uma versão mais curta")
    assert preview.title == "Limite de resposta atingido · Gerando uma versão mais curta"
    assert preview.collapsed


def test_ui_consultation_does_not_report_old_task_as_blocked(tmp_path):
    from test_tui import UIModel, run_ui, wait_ready
    from textual.widgets import Static

    from codaro.tui import CodaroApp, Prompt

    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    agent.tasks.start("Implemente uma funcionalidade")
    agent.tasks.update(lambda task: task.update(revision=1, state="blocked"))
    previous = agent.tasks.current()

    async def scenario():
        app = CodaroApp(agent)
        async with app.run_test(size=(120, 35)) as pilot:
            app.query_one(Prompt).load_text("o que pode me falar sobre o projeto atual?")
            await pilot.press("enter")
            await wait_ready(app, pilot)
            assert str(app.query_one("#status", Static).render()) == "Pronto"
            assert app.response_text == "Resposta de teste."
            assert agent.tasks.current() == previous
            assert agent.mode == Mode.EXECUTE

    run_ui(scenario())
