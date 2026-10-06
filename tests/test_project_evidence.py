import json

import httpx
import pytest
from test_agent import FakeModel, call
from test_streaming import chunk, encode

from codaro.agent import Agent
from codaro.provider import ModelError, OpenAICompatible, Settings
from codaro.repository import Repository

QUESTION = "Explique a estrutura deste projeto e seus pontos de entrada."
WRONG = "Os pontos de entrada são get_repository_info, list_files e search_code."


def project(root):
    (root / "pyproject.toml").write_text('[project.scripts]\ncodaro = "codaro.cli:app"\n')


def test_photo_regression_metadata_is_rejected_then_files_are_read(tmp_path):
    project(tmp_path)
    model = FakeModel(
        [
            call("get_repository_info", {}),
            {"content": WRONG},
            call("list_files", {}, "files"),
            call("read_lines", {"path": "pyproject.toml", "start": 1, "end": 2}, "read"),
            {"content": "pyproject.toml:2 declara o comando codaro, que inicia codaro.cli:app."},
        ]
    )
    deltas = []
    events = []
    agent = Agent(Repository(tmp_path), model)
    answer = agent.ask(QUESTION, on_delta=deltas.append, on_detail=events.append)
    assert "codaro.cli:app" in answer
    assert deltas == [answer]
    assert WRONG not in json.dumps(model.requests[2][0])
    assert WRONG not in json.dumps(agent.turns)
    assert any(event.kind == "model_end" and event.state == "retry" for event in events)
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["turns"][1]["response"]["content"] == WRONG
    assert flow["turns"][1]["outcome"] == "evidence_repair"
    assert flow["turns"][-1]["evidence"] == [
        {"path": "pyproject.toml", "start_line": 1, "end_line": 2}
    ]
    assert flow["final_answer"] == answer


def test_repeated_metadata_explanation_is_bounded_and_not_saved(tmp_path):
    project(tmp_path)
    model = FakeModel([call("get_repository_info", {}), {"content": WRONG}, {"content": WRONG}])
    agent = Agent(Repository(tmp_path), model)
    with pytest.raises(ModelError, match="sem citar arquivos"):
        agent.ask(QUESTION)
    assert len(model.requests) == 3
    assert not agent.turns
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["status"] == "error"
    assert "final_answer" not in flow


@pytest.mark.parametrize(
    "first",
    [
        call("list_files", {}),
        call("search_code", {"query": "codaro"}),
        call("read_lines", {"path": "missing.py", "start": 1, "end": 1}),
    ],
)
def test_previews_listing_and_failed_reads_are_not_implementation_evidence(tmp_path, first):
    project(tmp_path)
    model = FakeModel([first, {"content": "pyproject.toml:2 inicia o comando."}])
    with pytest.raises(ModelError, match="sem citar arquivos"):
        Agent(Repository(tmp_path), model, max_steps=1).ask(QUESTION)


@pytest.mark.parametrize(
    "answer",
    [
        "O projeto inicia um comando.",
        "pyproject.toml:99 inicia o comando.",
        "other-pyproject.toml:2 inicia o comando.",
        "pyproject.toml:" + "9" * 5000,
    ],
)
def test_citation_must_point_to_lines_actually_read_this_turn(tmp_path, answer):
    project(tmp_path)
    model = FakeModel(
        [
            call("read_lines", {"path": "pyproject.toml", "start": 1, "end": 2}),
            {"content": answer},
        ]
    )
    with pytest.raises(ModelError, match="sem citar arquivos"):
        Agent(Repository(tmp_path), model, max_steps=1).ask(QUESTION)


def test_partial_line_does_not_qualify_as_complete_evidence(tmp_path):
    (tmp_path / "main.py").write_text("def main(): " + "pass; " * 2000)
    model = FakeModel(
        [
            call("read_lines", {"path": "main.py", "start": 1, "end": 1}),
            {"content": "main.py:1 inicia o projeto."},
        ]
    )
    with pytest.raises(ModelError, match="sem citar arquivos"):
        Agent(Repository(tmp_path), model, max_steps=1).ask(QUESTION)


def test_followup_recovers_fresh_source_before_accepting_a_citation(tmp_path):
    project(tmp_path)
    model = FakeModel(
        [
            call("read_lines", {"path": "pyproject.toml", "start": 1, "end": 2}),
            {"content": "pyproject.toml:2 inicia codaro."},
            {"content": "pyproject.toml:2 inicia codaro."},
            {"content": "pyproject.toml:2 inicia new.cli:app."},
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    agent.ask(QUESTION)
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\ncodaro = "new.cli:app"\n')
    assert "new.cli:app" in agent.ask(QUESTION)
    assert len(agent.turns) == 2
    assert "new.cli:app" in model.requests[-1][0][0]["content"]
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["turns"][0]["outcome"] == "evidence_repair"
    assert "new.cli:app" in flow["local_retrievals"][0]["calls"][0]["result"]["content"]


@pytest.mark.parametrize(
    "question,tool,answer",
    [
        ("Em qual diretório do projeto estamos?", "get_repository_info", "Na pasta selecionada."),
        ("Quais arquivos existem?", "list_files", "Existe pyproject.toml."),
        ("Quais ferramentas você tem?", "get_repository_info", "Posso listar e ler arquivos."),
    ],
)
def test_session_and_listing_questions_do_not_require_source_reads(
    tmp_path, question, tool, answer
):
    project(tmp_path)
    model = FakeModel([call(tool, {}), {"content": answer}])
    assert Agent(Repository(tmp_path), model).ask(question) == answer
    assert len(model.requests) == 2


def test_general_architecture_question_is_not_forced_to_read_local_code(tmp_path):
    model = FakeModel([{"content": "MVC separa modelo, visão e controlador."}])
    assert "MVC" in Agent(Repository(tmp_path), model).ask("O que é arquitetura MVC?")
    assert len(model.requests) == 1


def test_empty_scope_returns_actual_limitation_instead_of_metadata_explanation(tmp_path):
    model = FakeModel([{"content": WRONG}])
    answer = Agent(Repository(tmp_path), model).ask(QUESTION)
    assert "Não encontrei arquivos permitidos" in answer
    assert WRONG not in answer


def test_overview_uses_json_planning_and_streams_after_source_evidence(tmp_path):
    project(tmp_path)
    payloads = []

    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            assert not payload.get("stream")
            assert payload["tools"][0]["type"] == "function"
            assert payload["tools"][0]["function"]["name"] == "get_repository_info"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": call(
                                "read_lines", {"path": "pyproject.toml", "start": 1, "end": 2}
                            )
                        }
                    ]
                },
            )
        assert payload["stream"] is True
        tool = payload["messages"][-1]
        assert tool["role"] == "tool"
        assert "codaro.cli:app" in json.loads(tool["content"])["content"]
        raw = encode(
            [chunk({"content": "pyproject.toml:2 inicia codaro.cli:app."}), chunk(reason="stop")]
        )
        return httpx.Response(200, content=raw, headers={"content-type": "text/event-stream"})

    provider = OpenAICompatible(
        Settings("https://example.test/v1", "model"), httpx.MockTransport(handler)
    )
    deltas = []
    answer = Agent(Repository(tmp_path), provider).ask(QUESTION, on_delta=deltas.append)
    assert answer == "".join(deltas) == "pyproject.toml:2 inicia codaro.cli:app."
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert [turn["kind"] for turn in flow["turns"]] == ["chat", "stream"]
    assert [turn["request"] for turn in flow["turns"]] == payloads
