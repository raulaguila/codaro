"""Regressions exercising real persistence, model requests and UI session changes."""

import copy
import json

import pytest
from test_agent import FakeModel, call
from test_tui import UIModel, run_ui, wait_ready

from codaro.agent import Agent
from codaro.agent.lifecycle import new_session
from codaro.agent.questions import register_questions
from codaro.context import TokenCounter
from codaro.conversation import Conversation
from codaro.repository import Repository
from codaro.session_catalog import SessionCatalog
from codaro.sessions import SessionStore
from codaro.tasks import TaskStore
from codaro.ui.app import CodaroApp


def pair(question="Olá", answer="Resposta"):
    return [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]


def test_user_decision_survives_restart_and_reaches_actual_model_request(tmp_path):
    marker = "Preservar APIV2_CONSTRAINT_1394 sem alterar endpoints."
    model = FakeModel(
        [
            call("ask_user_question", {"question": "Qual contrato preservar?", "choices": []}),
            {"content": "Análise concluída; nenhum arquivo alterado."},
        ]
    )
    model.settings = UIModel.settings
    first = CodaroApp(Agent(Repository(tmp_path), model, mode="ask"))

    async def scenario():
        async with first.run_test() as pilot:
            await first.submit_question("Analise o contrato da API.")
            for _ in range(150):
                await pilot.pause(0.01)
                if first.pending_user_question:
                    break
            assert first.pending_user_question
            first.answer_user_question(marker)
            await wait_ready(first, pilot)
            assert len(first.session.load()) == 1
            assert first.session.decisions == [
                [
                    {
                        "question": "Qual contrato preservar?",
                        "answer": marker,
                    }
                ]
            ]
        following = FakeModel([{"content": "Retomado."}])
        following.settings = UIModel.settings
        resumed = CodaroApp(Agent(Repository(tmp_path), following, mode="ask"), resume=True)
        async with resumed.run_test() as pilot:
            await pilot.pause()
            matches = resumed.agent.memory.search("APIV2_CONSTRAINT_1394")["results"]
            assert matches and marker in matches[0]["snippet"]
            await resumed.submit_question("Continue a análise.")
            await wait_ready(resumed, pilot)
            assert marker in json.dumps(following.requests[0][0], ensure_ascii=False)
            assert len(resumed.session.load()) == 2
            assert resumed.session.decisions[0][0]["answer"] == marker
            assert not resumed.agent.policy.auto_edits

    run_ui(scenario())


def test_cli_question_decision_persists_without_ui(tmp_path):
    model = FakeModel(
        [
            call("ask_user_question", {"question": "Qual contrato?", "choices": []}),
            {"content": "Decisão registrada."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, mode="ask")
    answer = "Preservar o contrato " + "x" * 7000
    register_questions(agent, lambda question, cancelled: {"user_response": answer})
    agent.ask("Confirme o contrato comigo.")
    store = agent.sessions.store("default")
    store.load()
    assert store.decisions[0][0]["answer"] == answer
    matches = agent.memory.search("contrato")["results"]
    decision = next(item for item in matches if item["turn_id"].startswith("decision:"))
    pages, offset = [], 0
    while True:
        page = agent.memory.read(decision["turn_id"], offset=offset, limit=4000)
        pages.append(page["text"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert answer in "".join(pages)
    assert not agent.policy.auto_edits


@pytest.mark.parametrize("failure", ["limit", "activation"])
def test_failed_new_conversation_preserves_task_permissions_and_session(
    tmp_path, monkeypatch, failure
):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    (tmp_path / "main.py").write_text("x = 1\n")
    agent.edits.observe(agent.repository.read_lines("main.py", 1, 1), b"x = 1\n")
    proposed = agent.edits.propose("main.py", "x = 1", "x = 2", "Correção")
    proposal = agent.edits.proposals[proposed["proposal_id"]]
    agent.tasks.start("Atividade que deve permanecer ativa")
    agent.policy.auto_edits = True
    before = copy.deepcopy(agent.tasks.load())
    if failure == "limit":
        for n in range(29):
            agent.sessions.create(f"Conversa {n}")
    else:

        def fail(identifier):
            raise OSError("falha de gravação simulada")

        monkeypatch.setattr(agent.sessions, "activate", fail)
    app = CodaroApp(agent)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app.show_user_question({"question": "Qual contrato?", "choices": []}, lambda _: None)
            await app.reset_conversation("")
            assert agent.session_id == "default"
            assert agent.tasks.load() == before
            assert agent.policy.auto_edits
            assert proposal.state == "pending"
            assert app.pending_user_question["question"] == "Qual contrato?"
            assert app.session is agent.conversation.store
            assert agent.sessions.load()["active"] == "default"
            assert len(agent.sessions.load()["items"]) == (30 if failure == "limit" else 1)

    run_ui(scenario())


def test_destination_corruption_does_not_change_live_session(tmp_path, monkeypatch):
    agent = Agent(Repository(tmp_path), FakeModel([]), mode="ask")
    previous = agent.memory, agent.tasks, agent.conversation

    def fail(store):
        raise ValueError("Tarefas corrompidas")

    monkeypatch.setattr(TaskStore, "load", fail)
    with pytest.raises(ValueError, match="corrompidas"):
        new_session(agent)
    assert (agent.memory, agent.tasks, agent.conversation) == previous
    assert agent.sessions.load()["active"] == "default"


def test_memory_import_uses_selected_session_instead_of_default(tmp_path):
    SessionStore(tmp_path).save([pair("PRIVATE_DEFAULT_MARKER")], "model")
    agent = Agent(Repository(tmp_path), FakeModel([]))
    identifier = agent.sessions.create("Outra conversa")
    agent.activate_session(identifier)
    assert not agent.memory.search("PRIVATE_DEFAULT_MARKER")["results"]


def test_archiving_frees_capacity_preserving_history_and_requires_inactive_session(tmp_path):
    catalog = SessionCatalog(tmp_path)
    identifiers = [catalog.create(f"Conversa {n}") for n in range(29)]
    stored = catalog.store(identifiers[0])
    stored.save([pair()], "model")
    with pytest.raises(ValueError, match="30"):
        catalog.create()
    with pytest.raises(ValueError, match="ativa"):
        catalog.archive("default")
    catalog.archive(identifiers[0])
    catalog.create()
    assert stored.load() == [pair()]
    with pytest.raises(ValueError, match="30"):
        catalog.archive(identifiers[0], archived=False)
    with pytest.raises(ValueError, match="encontrada"):
        catalog.activate(identifiers[0])
    catalog.archive(identifiers[1])
    catalog.archive(identifiers[0], archived=False)
    catalog.activate(identifiers[0])
    assert catalog.load()["active"] == identifiers[0]


def test_failed_write_preserves_confirmed_decisions_in_memory_and_on_disk(tmp_path, monkeypatch):
    conversation = Conversation(SessionStore(tmp_path))
    conversation.confirm("Qual contrato?", "Manter v2", "model")
    before = conversation.store.path.read_bytes()

    def fail(path, content):
        raise OSError("disco indisponível")

    monkeypatch.setattr("codaro.sessions.atomic_write", fail)
    with pytest.raises(OSError):
        conversation.complete(pair(), "model")
    assert conversation.completed == 0
    assert conversation.store.pending_decisions[0]["answer"] == "Manter v2"
    assert conversation.store.path.read_bytes() == before


def test_transcript_is_retained_independently_of_model_working_history(tmp_path):
    model = FakeModel([{"content": "Primeira"}, {"content": "Segunda"}])
    agent = Agent(Repository(tmp_path), model, history_budget=0)
    agent.ask("Olá")
    agent.ask("Tudo bem?")
    assert agent.turns == []
    assert len(agent.conversation.store.load()) == 2


def test_decisions_context_is_bounded_and_complete_answers_are_recoverable(tmp_path):
    conversation = Conversation(SessionStore(tmp_path))
    answer = "KEEP_FULL_" + "x" * 7000
    conversation.confirm("Qual contrato?", answer, "model")
    counter = TokenCounter()
    context = conversation.context(counter, 256)
    assert counter.count({"messages": [{"role": "user", "content": context}]}) <= 256
    assert "read_conversation" in context
    assert "KEEP_FULL_" not in context
    assert conversation.decisions()[0]["answer"] == answer


def test_ephemeral_conversation_uses_same_validation_without_writing_session(tmp_path):
    conversation = Conversation(SessionStore(tmp_path), ephemeral=True)
    conversation.confirm("Qual contrato?", "Manter v2", "model")
    conversation.complete(pair(), "model")
    assert conversation.store.load() == [pair()]
    assert conversation.store.decisions[0][0]["answer"] == "Manter v2"
    assert not conversation.store.path.exists()


def test_failed_session_save_prevents_switch_and_answer_submission(tmp_path, monkeypatch):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    agent.tasks.start("Manter a tarefa atual")
    app = CodaroApp(agent)
    replies = []

    def fail(*args, **kwargs):
        raise OSError("disco indisponível")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            before = copy.deepcopy(agent.tasks.load())
            monkeypatch.setattr(agent.conversation, "replace", fail)
            await app.reset_conversation("")
            assert agent.tasks.load() == before
            assert agent.sessions.load()["active"] == "default"
            assert len(agent.sessions.load()["items"]) == 1
            app.show_user_question({"question": "Qual contrato?", "choices": []}, replies.append)
            app.answer_user_question("Manter v2")
            assert not replies
            assert app.pending_user_question is not None
            assert not app.current_decisions

    run_ui(scenario())


def test_ui_initializes_from_durable_transcript_after_working_history_is_trimmed(tmp_path):
    agent = Agent(Repository(tmp_path), UIModel())
    agent.conversation.confirm("Qual contrato?", "Preservar v2", "model")
    agent.conversation.complete(pair(), "model")
    agent.turns.clear()
    app = CodaroApp(agent)
    assert app.session_turns == [pair()]
    assert app.session_decisions[0][0]["answer"] == "Preservar v2"
    assert app.save_session()
    app.session.load()
    assert app.session.decisions[0][0]["answer"] == "Preservar v2"


def test_failure_closing_old_task_keeps_ui_attached_to_committed_new_session(tmp_path, monkeypatch):
    agent = Agent(Repository(tmp_path), UIModel(), mode="execute")
    agent.tasks.start("Tarefa anterior")
    previous = agent.tasks
    app = CodaroApp(agent)

    def fail(*args, **kwargs):
        raise OSError("falha ao encerrar tarefa anterior")

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            monkeypatch.setattr(previous, "mutate", fail)
            await app.reset_conversation("")
            assert agent.session_id != "default"
            assert agent.sessions.load()["active"] == agent.session_id
            assert app.session is agent.conversation.store
            assert agent.tasks.current() is None
            assert any(
                "Nova conversa aberta" in str(item.render()) for item in app.query(".notice")
            )

    run_ui(scenario())


def test_confirmed_decision_remains_recoverable_after_archive_retention(tmp_path, monkeypatch):
    monkeypatch.setattr("codaro.memory.MAX_TURNS", 2)
    agent = Agent(Repository(tmp_path), UIModel())
    agent.conversation.confirm("Qual contrato?", "KEEP_RETAINED_CONTRACT_V2", "model")
    agent.conversation.index_decisions(agent.memory, "model")
    for i in range(3):
        agent.memory.append(f"later-{i}", f"Pergunta {i}", "Resposta", "model", [])
    with agent.memory.database() as db:
        assert not db.execute("SELECT 1 FROM turns WHERE id LIKE 'decision:%'").fetchone()
    result = agent.memory.search("KEEP_RETAINED_CONTRACT_V2")["results"][0]
    recovered = agent.memory.read(result["turn_id"])
    assert "KEEP_RETAINED_CONTRACT_V2" in recovered["text"]
    assert result["source"] == "user_decision_not_authorization"
