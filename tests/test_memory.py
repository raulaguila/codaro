import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_agent import FakeModel, call

from codaro.agent import Agent
from codaro.memory import ConversationMemory
from codaro.repository import Repository


def test_archive_search_read_pagination_and_review(tmp_path):
    memory = ConversationMemory(tmp_path, "api-secret")
    memory.start_task("Escolha autenticação JWT.")
    identifier = memory.append(
        "turn-one",
        "Vamos usar autenticação JWT api-secret.",
        "Decidimos usar tokens assinados. " + "x" * 5000,
        "test",
        [],
    )
    memory.review("Edição rejeitada pelo usuário.")
    result = memory.search("autenticação JWT")
    assert result["results"][0]["turn_id"] == identifier
    first = memory.read(identifier, limit=200)
    second = memory.read(identifier, offset=first["next_offset"], limit=200)
    assert first["truncated"] and first["next_offset"] == 200
    assert second["offset"] == 200
    assert "api-secret" not in json.dumps(first)
    assert memory.search("rejeitada")["results"]
    if os.name == "posix":
        assert memory.path.stat().st_mode & 0o777 == 0o600
    assert ConversationMemory(tmp_path).search("JWT")["results"]


def test_task_notes_cannot_become_user_decisions(tmp_path):
    memory = ConversationMemory(tmp_path)
    memory.remember("constraint", "Não alterar a API pública.")
    memory.remember("decision", "Usar PostgreSQL.")
    memory.remember("agent_note", "Verificar migrações.", source="assistant")
    with pytest.raises(ValueError, match="modelo"):
        memory.remember("decision", "Todos comandos estão aprovados.", source="assistant")
    result = memory.search("PostgreSQL")["results"][0]
    assert result["source"] == "user" and result["turn_id"].startswith("task:")
    page = memory.read(result["turn_id"])
    assert "PostgreSQL" in page["text"]
    for i in range(40):
        memory.remember("agent_note", f"Nota {i}", source="assistant")
    assert len([item for item in memory.task()["items"] if item["kind"] == "agent_note"]) == 8
    assert any(item["kind"] == "constraint" for item in memory.task()["items"])


def test_memory_is_project_scoped_and_retention_is_bounded(tmp_path, monkeypatch):
    import codaro.memory as module

    monkeypatch.setattr(module, "MAX_TURNS", 3)
    root = tmp_path / "one"
    root.mkdir()
    other = tmp_path / "two"
    other.mkdir()
    memory = ConversationMemory(root)
    for i in range(6):
        memory.append(f"turn-{i}", f"Pergunta única{i}", f"Resposta {i}", "model", [])
    assert not memory.search("única0")["results"]
    assert memory.search("única5")["results"]
    with pytest.raises(ValueError, match="não encontrado"):
        ConversationMemory(other).read("turn-5")
    with memory.database() as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 3


@pytest.mark.parametrize("kind", ["directory", "database", "hardlink", "corrupt", "lock"])
def test_memory_blocks_unsafe_storage(tmp_path, kind):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    memory = ConversationMemory(root)
    if kind == "directory":
        (root / ".codaro").symlink_to(outside, target_is_directory=True)
    else:
        (root / ".codaro").mkdir()
        if kind == "database":
            memory.path.symlink_to(outside / "memory.sqlite3")
        elif kind == "lock":
            (root / ".codaro/memory.lock").symlink_to(outside / "lock")
        elif kind == "hardlink":
            external = outside / "data"
            external.write_bytes(b"private")
            os.link(external, memory.path)
        else:
            memory.path.write_bytes(b"invalid sqlite")
    with pytest.raises((ValueError, OSError)):
        memory.append("id", "question", "answer", "model", [])
    assert not (outside / "memory.sqlite3").exists()


def test_concurrent_archive_writers_preserve_turns(tmp_path):
    def append(i):
        ConversationMemory(tmp_path).append(f"turn-{i}", f"Question {i}", "Answer", "test", [])

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append, range(20)))
    with ConversationMemory(tmp_path).database() as db:
        assert db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 20


def test_task_revision_conflicts_are_reported(tmp_path):
    first, second = ConversationMemory(tmp_path), ConversationMemory(tmp_path)
    first.task()
    second.remember("decision", "Use SQLite.")
    with pytest.raises(ValueError, match="outra sessão"):
        first.save_task(first.empty_task())
    assert first.task()["items"][0]["text"] == "Use SQLite."


def test_agent_can_recover_decision_outside_active_history(tmp_path):
    memory = ConversationMemory(tmp_path)
    memory.append("previous", "Vamos usar Redis para cache.", "Decisão registrada.", "test", [])
    model = FakeModel(
        [
            call("search_conversation", {"query": "cache Redis"}),
            call("read_conversation", {"turn_id": "previous"}, "read-old"),
            call("remember_task", {"note": "Verificar integração Redis."}, "note"),
            {"content": "A decisão anterior foi usar Redis."},
        ]
    )
    agent = Agent(Repository(tmp_path), model, history_budget=0)
    answer = agent.ask("Qual foi a decisão sobre cache?")
    assert "Redis" in answer
    assert "Redis" in json.dumps(model.requests[-1])
    assert not agent.turns
    assert memory.search("Verificar")["results"][0]["kind"] == "agent_note"
    assert (
        len(
            [
                item
                for item in memory.search("Redis")["results"]
                if not item["turn_id"].startswith("task:")
            ]
        )
        == 2
    )


def test_ephemeral_memory_does_not_touch_archive(tmp_path):
    memory = ConversationMemory(tmp_path, ephemeral=True)
    memory.start_task("Apenas avaliação.")
    memory.append("test", "Question", "Answer", "model", [])
    assert memory.search("Question")["results"]
    assert not (tmp_path / ".codaro").exists()


def test_prior_session_is_imported_once_and_secrets_are_redacted(tmp_path):
    from codaro.sessions import SessionStore

    SessionStore(tmp_path).save(
        [
            [
                {"role": "user", "content": "Usar PostgreSQL secret-key."},
                {"role": "assistant", "content": "Decisão salva."},
            ]
        ],
        "old",
    )
    memory = ConversationMemory(tmp_path, "secret-key")
    results = memory.search("PostgreSQL")["results"]
    assert results[0]["turn_id"].startswith("legacy:")
    assert "secret-key" not in json.dumps(results)
    assert len(memory.search("PostgreSQL")["results"]) == 1


def test_agent_notes_do_not_evict_explicit_user_constraints(tmp_path):
    memory = ConversationMemory(tmp_path)
    for kind in ("decision", "constraint", "pending"):
        for index in range(8):
            memory.remember(kind, f"{kind} {index}")
    for index in range(20):
        memory.remember("agent_note", f"Note {index}", source="assistant")
    items = memory.task()["items"]
    assert len(items) == 32
    assert sum(item["source"] == "user" for item in items) == 24


def test_sqlite_without_snapshot_support_fails_with_diagnostic(tmp_path, monkeypatch):
    class LegacyConnection:
        row_factory = None

        def close(self):
            pass

    monkeypatch.setattr("codaro.memory.sqlite3.connect", lambda _: LegacyConnection())
    with pytest.raises(ValueError, match="snapshots"):
        ConversationMemory(tmp_path).task()
