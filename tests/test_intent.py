import pytest

from codaro.intent import is_information_request, requests_changes
from codaro.tasks import TaskStore


@pytest.mark.parametrize(
    "question",
    [
        "Remova a função antiga",
        "Renomeie auth.py",
        "Otimize o índice",
        "Simplifique o fluxo",
        "Migrate configuration",
        "Repair the parser",
        "Delete obsolete code",
        "Modify the layout",
        "Como funciona? Corrija o erro também.",
    ],
)
def test_change_requests_require_structural_evidence(tmp_path, question):
    assert requests_changes(question)
    assert not is_information_request(question)
    store = TaskStore(tmp_path, ephemeral=True)
    assert store.start(question)["requires_changes"]
    assert not store.validation_ready()


@pytest.mark.parametrize(
    "question",
    [
        "Como remover uma função?",
        "Explique como refatorar o fluxo",
        "How to migrate the database?",
        "Qual a estrutura do projeto?",
        "Olá",
        "Execute os testes",
        "Continue a resposta",
    ],
)
def test_questions_and_validation_do_not_require_code_changes(question):
    assert not requests_changes(question)
