import json

import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent, cites_observed_lines
from codaro.repository import Repository

QUESTION = "Explique a estrutura deste projeto e seus pontos de entrada."


def project(root):
    (root / "pyproject.toml").write_text('[project.scripts]\ncodaro = "codaro.cli:app"\n')


def test_overview_without_citations_is_accepted_and_sources_are_prefetched(tmp_path):
    project(tmp_path)
    answer = "O comando codaro inicia codaro.cli:app, definido no manifesto Python."
    model = FakeModel([{"content": answer}])
    agent = Agent(Repository(tmp_path), model, mode="ask")
    assert agent.ask(QUESTION) == answer
    assert len(model.requests) == 1
    assert "codaro.cli:app" in model.requests[0][0][0]["content"]
    assert agent.turns[-1][-1]["content"] == answer
    flow = json.loads((tmp_path / ".codaro/prompt.json").read_text())
    assert flow["status"] == "success"
    assert flow["turns"][0]["outcome"] == "answer"
    assert flow["local_retrievals"]


@pytest.mark.parametrize(
    "question",
    [
        "Olá!",
        "Qual diretório você usa?",
        "Explique arquitetura hexagonal",
        "Como escrever testes?",
        "Obrigado",
    ],
)
def test_general_questions_do_not_require_local_source_reads(tmp_path, question):
    (tmp_path / "main.py").write_text("DO_NOT_PREFETCH = True\n")
    model = FakeModel([{"content": "Resposta sem referência."}])
    assert (
        Agent(Repository(tmp_path), model, mode="ask").ask(question) == "Resposta sem referência."
    )
    prompt = model.requests[0][0][0]["content"]
    assert "DO_NOT_PREFETCH" not in prompt
    assert "sem exigir citações" in prompt


def test_failed_read_can_be_explained_without_citation_or_retry(tmp_path):
    model = FakeModel(
        [
            call("read_lines", {"path": "missing.py", "start": 1, "end": 1}),
            {"content": "O arquivo solicitado não existe entre os caminhos permitidos."},
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    assert "não existe" in agent.ask("Leia missing.py")
    assert len(model.requests) == 2
    assert "error" in json.loads(model.requests[-1][0][-1]["content"])


def test_citation_helper_remains_available_for_optional_evaluation():
    evidence = [("main.py", 3, 5)]
    assert cites_observed_lines("main.py:4 define a função", evidence)
    assert not cites_observed_lines("main.py:9", evidence)
    assert not cites_observed_lines("other.py:4", evidence)
    assert not cites_observed_lines("main.py:" + "9" * 10000, evidence)


def test_model_receives_actual_root_and_session_metadata_is_not_project_structure(tmp_path):
    project(tmp_path)
    model = FakeModel(
        [call("get_repository_info", {}), {"content": "A raiz é a pasta selecionada."}]
    )
    agent = Agent(Repository(tmp_path), model)
    agent.ask(QUESTION)
    result = json.loads(model.requests[-1][0][-1]["content"])
    assert result["repository_root"] == str(tmp_path)
    assert result["contains_project_structure"] is False
    assert "NÃO são pontos de entrada" in model.requests[-1][0][0]["content"]


def test_followup_can_read_updated_source_without_mandatory_citation(tmp_path):
    project(tmp_path)
    model = FakeModel(
        [
            call("read_lines", {"path": "pyproject.toml", "start": 1, "end": 2}),
            {"content": "O comando inicia codaro.cli:app."},
            call("read_lines", {"path": "pyproject.toml", "start": 1, "end": 2}),
            {"content": "Agora o comando inicia new.cli:app."},
        ]
    )
    agent = Agent(Repository(tmp_path), model)
    agent.ask(QUESTION)
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\ncodaro = "new.cli:app"\n')
    assert "new.cli:app" in agent.ask(QUESTION)
    result = json.loads(model.requests[-1][0][-1]["content"])
    assert "new.cli:app" in result["content"]
