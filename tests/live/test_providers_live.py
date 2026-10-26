"""Opt-in acceptance tests; only synthetic fixtures are sent to the provider."""

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from codaro.agent import Agent
from codaro.configuration import resolve_configuration
from codaro.llm import create_provider
from codaro.llm.catalog import ModelCatalog
from codaro.llm.profiles import ProviderStore
from codaro.repository import Repository
from codaro.sessions import SessionStore
from codaro.trace_metrics import evaluation_metrics
from codaro.workflow_evaluation import evaluate_workflows

pytestmark = pytest.mark.live
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class LiveConfiguration:
    settings: object
    profile: dict = field(repr=False)


@pytest.fixture(scope="session")
def live_configuration(live_profile):
    if live_profile == "env":
        settings = resolve_configuration(provider="env").model
        style = os.getenv("CODARO_TEST_API_STYLE", "openai")
        settings = replace(settings, api_style=style)
        profile = {
            "kind": "custom" if style == "openai" else style,
            "base_url": settings.base_url,
            "api_key": settings.api_key,
            "tls_insecure": settings.tls_insecure,
        }
    else:
        store = ProviderStore()
        name, profile = store.profile(live_profile)
        model = next((m for m in profile["models"] if m["id"] == profile["model"]), None)
        if model is None:
            pytest.fail("Selecione um modelo para o perfil antes de executar os testes.")
        # Fetch fresh metadata without changing the user's saved selection.
        settings = store.settings_for(name, profile, ModelCatalog(profile).details(model))
    return LiveConfiguration(settings, profile)


@pytest.fixture
def live_provider(live_configuration):
    return create_provider(live_configuration.settings)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    shutil.copytree(ROOT / "evaluations/fixtures/authorization", root)
    return root


def record_run(root, record_property):
    flow = json.loads((root / ".codaro/prompt.json").read_text())
    record_property("metrics", json.dumps(evaluation_metrics(flow)))
    record_property("diagnosis", json.dumps(flow.get("diagnosis", {})))


def test_catalog_and_metadata(live_configuration, record_property):
    settings, profile = live_configuration.settings, live_configuration.profile
    catalog = ModelCatalog(profile)
    models = catalog.list()
    selected = next((m for m in models if m["id"] == settings.model), None)
    assert selected is not None, "Modelo selecionado ausente no catálogo da API."
    model = catalog.details(selected)
    assert model["tools"] is not False
    record_property("context_window", model["context_window"])
    record_property("context_source", model["context_source"])


def test_streaming_keeps_reasoning_separate(live_provider, record_property):
    chunks, reasoning, arrivals = [], [], []
    started = time.monotonic()

    def on_delta(text):
        chunks.append(text)
        arrivals.append(time.monotonic() - started)

    message = live_provider.stream(
        [{"role": "user", "content": "Responda apenas com uma saudação curta em português."}],
        on_delta=on_delta,
        on_reasoning=reasoning.append,
    )
    assert message["content"] and not message.get("tool_calls")
    assert "".join(chunks) == message["content"]
    assert "<think>" not in message["content"]
    record_property("content_chunks", len(chunks))
    record_property("reasoning_chunks", len(reasoning))
    record_property("first_content_ms", round(arrivals[0] * 1000))


def test_native_tool_roundtrip(live_provider):
    live_provider.check_tool_calling()


def test_agent_reads_repository_before_analysis(project, live_provider, record_property):
    agent = Agent(
        Repository(project), live_provider, mode="ask", persist_memory=False, max_seconds=180
    )
    answer = agent.ask("Leia auth.py e explique como can_edit decide se o usuário pode editar.")
    flow = json.loads((project / ".codaro/prompt.json").read_text())
    assert answer.strip()
    assert any(
        "auth.py" in turn.get("evidence", [{}])[0].get("path", "")
        for turn in flow["turns"]
        if turn.get("evidence")
    )
    record_run(project, record_property)


def test_history_recall_after_removing_recent_turns(project, live_provider, record_property):
    agent = Agent(
        Repository(project), live_provider, mode="ask", persist_memory=False, max_seconds=180
    )
    marker = "decision_" + uuid.uuid4().hex[:12]
    agent.ask(f"Registre esta decisão da conversa: o nome da release será {marker}. Confirme.")
    # Drop the in-context copy; recovery must use the conversation archive tools.
    agent.turns.clear()
    answer = agent.ask(
        "Use search_conversation e read_conversation para recuperar a decisão anterior "
        "sobre o nome da release. Qual foi o nome escolhido?"
    )
    assert marker in answer
    record_run(project, record_property)


def test_confirmed_decision_reaches_model_after_restart(project, live_provider, record_property):
    marker = "release_" + uuid.uuid4().hex[:12]
    store = SessionStore(project)
    store.save(
        [
            [
                {"role": "user", "content": "Definir o nome da release"},
                {"role": "assistant", "content": "Decisão registrada."},
            ]
        ],
        live_provider.settings.model,
        decisions=[[{"question": "Qual nome?", "answer": marker}]],
    )
    agent = Agent(Repository(project), live_provider, mode="ask", max_seconds=180)
    agent.activate_session("default")
    agent.turns.clear()
    answer = agent.ask("Qual nome da release eu confirmei? Responda com o nome registrado.")
    assert marker in answer
    assert agent.memory.search(marker)["results"]
    assert not agent.policy.auto_edits
    record_run(project, record_property)


def test_large_source_is_read_in_pages(project, live_provider, record_property):
    # Larger than the configured input window; only a bounded first page is needed.
    marker = "SOURCE_MARKER_" + uuid.uuid4().hex[:12]
    (project / "large.py").write_text(f'RELEASE = "{marker}"\n' + "# synthetic filler\n" * 20_000)
    agent = Agent(
        Repository(project), live_provider, mode="ask", persist_memory=False, max_seconds=180
    )
    answer = agent.ask(
        "Leia somente as primeiras linhas de large.py e informe o valor de RELEASE. "
        "O arquivo é grande: use páginas pequenas."
    )
    assert marker in answer
    record_run(project, record_property)


def test_implementation_and_validation(live_provider, request, record_property):
    if not request.config.getoption("--live-execution"):
        pytest.skip("Use --live-execution para autorizar execução nas fixtures temporárias.")
    report = evaluate_workflows(
        ROOT / "evaluations/workflows.json", live_provider, allow_execution=True
    )
    record_property("workflows", json.dumps(report))
    assert report["passed"] == report["total"], report
