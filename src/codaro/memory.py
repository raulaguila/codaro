"""Project-local archive and explicit task memory; no model reasoning is stored."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from codaro.chunks import terms
from codaro.sessions import SessionStore
from codaro.storage import private_lock, private_read
from codaro.trace import MAX_EVENT_BYTES, atomic_write, timestamp

MAX_MEMORY_BYTES = 8_000_000
MAX_TURNS = 500


class ConversationMemory:
    def __init__(self, root: Path, secret: str = "", *, ephemeral=False):
        self.root = root
        self.path = root / ".codaro" / "memory.sqlite3"
        self.redact = SessionStore(root, secret).redact
        self.last_turn_id: str | None = None
        self.ephemeral = ephemeral
        self._raw: bytes | None = None
        self._task_revision = None

    @contextmanager
    def database(self, *, write=False):
        guard = nullcontext() if self.ephemeral else private_lock(self.path.with_suffix(".lock"))
        with guard:
            with self._database(write=write) as db:
                yield db

    @contextmanager
    def _database(self, *, write=False):
        # Deserialize into memory rather than letting SQLite follow database/journal
        # links. Persist a bounded snapshot through the existing private atomic writer.
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        try:
            if not callable(getattr(db, "serialize", None)) or not callable(
                getattr(db, "deserialize", None)
            ):
                raise ValueError("SQLite sem suporte a snapshots; confira codaro doctor.")
            try:
                raw = self._raw if self.ephemeral else private_read(self.path, MAX_MEMORY_BYTES)
            except FileNotFoundError:
                raw = None
            if raw is not None:
                db.deserialize(raw)
                db.execute("PRAGMA trusted_schema=OFF")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                root = db.execute("SELECT value FROM meta WHERE key='root'").fetchone()
                if version != 1 or not root or root[0] != str(self.root):
                    raise ValueError("Memória incompatível com este projeto.")
            else:
                db.executescript("""
                    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE turns(
                        id TEXT PRIMARY KEY, created TEXT NOT NULL, model TEXT NOT NULL,
                        question TEXT NOT NULL, answer TEXT NOT NULL, actions TEXT NOT NULL,
                        review TEXT NOT NULL DEFAULT ''
                    );
                    CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED, question, answer, review);
                    PRAGMA user_version=1;
                """)
                db.execute("INSERT INTO meta VALUES('root',?)", (str(self.root),))
                db.execute("INSERT INTO meta VALUES('task',?)", (json.dumps(self.empty_task()),))
                db.commit()
            yield db
            if write:
                db.commit()
                while True:
                    count = db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
                    data = db.serialize()
                    if count <= MAX_TURNS and len(data) <= MAX_MEMORY_BYTES:
                        break
                    oldest = db.execute("SELECT id FROM turns ORDER BY rowid LIMIT 1").fetchone()
                    if oldest is None or count <= 1:
                        raise ValueError("Memória excede o limite de armazenamento.")
                    db.execute("DELETE FROM search WHERE id=?", (oldest[0],))
                    db.execute("DELETE FROM turns WHERE id=?", (oldest[0],))
                    db.commit()
                    db.execute("VACUUM")
                if self.ephemeral:
                    self._raw = data
                else:
                    atomic_write(self.path, data)
        except sqlite3.Error as exc:
            raise ValueError("Memória SQLite inválida ou indisponível.") from exc
        finally:
            db.close()

    @staticmethod
    def empty_task():
        return {"objective": "", "requests": [], "items": []}

    def task(self) -> dict:
        with self.database() as db:
            return self.task_from_database(db)

    def task_from_database(self, db):
        raw = db.execute("SELECT value FROM meta WHERE key='task'").fetchone()
        try:
            task = json.loads(raw[0])
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("Memória da tarefa inválida.") from exc
        if (
            not isinstance(task, dict)
            or set(task) != {"objective", "requests", "items"}
            or not isinstance(task["objective"], str)
            or len(task["objective"]) > 2000
            or not isinstance(task["requests"], list)
            or len(task["requests"]) > 4
            or not all(isinstance(item, str) and len(item) <= 500 for item in task["requests"])
            or not isinstance(task["items"], list)
            or len(task["items"]) > 32
        ):
            raise ValueError("Memória da tarefa inválida.")
        for item in task["items"]:
            if (
                not isinstance(item, dict)
                or item.get("kind") not in {"decision", "constraint", "pending", "agent_note"}
                or item.get("source") not in {"user", "assistant"}
                or (item.get("source") == "assistant" and item.get("kind") != "agent_note")
                or not isinstance(item.get("id"), str)
                or len(item["id"]) != 12
                or not isinstance(item.get("created"), str)
                or len(item["created"]) > 80
                or not isinstance(item.get("text"), str)
                or len(item["text"]) > 300
            ):
                raise ValueError("Item de memória inválido.")
        revision = db.execute("SELECT value FROM meta WHERE key='task_revision'").fetchone()
        self._task_revision = int(revision[0]) if revision else 0
        return self.redact(task)

    def save_task(self, task):
        with self.database(write=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key='task_revision'").fetchone()
            revision = int(row[0]) if row else 0
            if self._task_revision is not None and revision != self._task_revision:
                raise ValueError("Memória mudou em outra sessão; tente novamente.")
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES('task_revision',?)", (str(revision + 1),)
            )
            self._task_revision = revision + 1
            db.execute(
                "UPDATE meta SET value=? WHERE key='task'",
                (json.dumps(self.redact(task), ensure_ascii=False),),
            )

    def start_task(self, question: str):
        task = self.task()
        task["objective"] = question[:2000]
        task["requests"] = [*task["requests"], question[:500]][-4:]
        self.save_task(task)

    def remember(self, kind: str, text: str, *, source="user") -> dict:
        if kind not in {"decision", "constraint", "pending", "agent_note"}:
            raise ValueError("Tipo de memória inválido.")
        if source not in {"user", "assistant"} or (source == "assistant" and kind != "agent_note"):
            raise ValueError("O modelo só pode registrar notas, não decisões do usuário.")
        if not isinstance(text, str) or not text.strip() or len(text) > 300:
            raise ValueError("Memória deve conter de 1 a 300 caracteres.")
        task = self.task()
        item = {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "source": source,
            "text": text,
            "created": timestamp(),
        }
        same = [old for old in task["items"] if old["kind"] == kind]
        if any(old["text"] == text for old in same):
            return {"saved": False, "already_saved": True}
        if len(same) >= 8:
            task["items"].remove(same[0])
        task["items"] = [*task["items"], item][-32:]
        self.save_task(task)
        return {"saved": True, "item": self.redact(item)}

    def clear_task(self):
        self.task()
        self.save_task(self.empty_task())

    def append(self, run_id: str, question: str, answer: str, model: str, actions: list):
        values = self.redact(
            {
                "question": question[:8000],
                "answer": answer[:16000],
                "model": model[:200],
                "actions": actions[:64],
            }
        )
        while len(json.dumps(values["actions"], ensure_ascii=False)) > 8000:
            values["actions"].pop()
        identifier = run_id
        with self.database(write=True) as db:
            if not db.execute("SELECT 1 FROM turns WHERE id=?", (identifier,)).fetchone():
                db.execute(
                    "INSERT INTO turns(id,created,model,question,answer,actions) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        identifier,
                        timestamp(),
                        values["model"],
                        values["question"],
                        values["answer"],
                        json.dumps(values["actions"], ensure_ascii=False),
                    ),
                )
                db.execute(
                    "INSERT INTO search(id,question,answer,review) VALUES(?,?,?,'')",
                    (identifier, values["question"], values["answer"]),
                )
        self.last_turn_id = identifier
        return identifier

    def review(self, text: str):
        if self.last_turn_id is None:
            return
        text = self.redact(text[:2000])
        with self.database(write=True) as db:
            row = db.execute("SELECT * FROM turns WHERE id=?", (self.last_turn_id,)).fetchone()
            if row:
                review = (row["review"] + "\n" + text)[-8000:]
                db.execute("UPDATE turns SET review=? WHERE id=?", (review, self.last_turn_id))
                db.execute("UPDATE search SET review=? WHERE id=?", (review, self.last_turn_id))

    def search(self, query: str, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("Busca da conversa: use de 1 a 1000 caracteres.")
        if type(limit) is not int or not 1 <= limit <= 8:
            raise ValueError("Limite da conversa deve ficar entre 1 e 8.")
        self.import_session()
        tokens = terms(query)
        if not tokens:
            return {"results": []}
        expression = " OR ".join('"' + word.replace('"', '""') + '"' for word in tokens)
        with self.database() as db:
            rows = db.execute(
                "SELECT t.id,t.created,t.model,t.question,t.answer,t.review "
                "FROM search s JOIN turns t ON t.id=s.id WHERE search MATCH ? "
                "ORDER BY bm25(search,0,3,1,2),t.rowid DESC LIMIT ?",
                (expression, limit),
            ).fetchall()
            results = []
            for row in rows:
                content = row["question"] + "\n" + row["answer"] + "\n" + row["review"]
                position = next(
                    (content.lower().find(token) for token in tokens if token in content.lower()), 0
                )
                results.append(
                    {
                        "turn_id": row["id"],
                        "created": row["created"],
                        "model": row["model"],
                        "question": row["question"][:180],
                        "snippet": content[max(0, position - 80) : position + 400],
                        "source": "conversation_not_code_evidence",
                    }
                )
            task = self.task_from_database(db)
            for item in task["items"]:
                if any(token in item["text"].casefold() for token in tokens):
                    results.insert(
                        0,
                        {
                            "turn_id": "task:" + item["id"],
                            "created": item["created"],
                            "question": item["kind"],
                            "snippet": item["text"],
                            "source": item["source"],
                            "kind": item["kind"],
                        },
                    )
            return self.redact({"results": results[:limit]})

    def import_session(self):
        if self.ephemeral:
            return
        with self.database() as db:
            if db.execute("SELECT 1 FROM meta WHERE key='session_imported'").fetchone():
                return
        try:
            turns = SessionStore(self.root).load()
        except FileNotFoundError:
            turns = []
        except (ValueError, OSError):
            # Do not import malformed/linked sessions or block a valid new archive.
            turns = []
        with self.database(write=True) as db:
            if db.execute("SELECT 1 FROM meta WHERE key='session_imported'").fetchone():
                return
            for position, turn in enumerate(turns):
                question = self.redact(turn[0]["content"][:8000])
                answer = self.redact(turn[-1]["content"][:16000])
                identifier = (
                    "legacy:"
                    + hashlib.sha256(
                        json.dumps([position, question, answer], ensure_ascii=False).encode()
                    ).hexdigest()[:32]
                )
                db.execute(
                    "INSERT OR IGNORE INTO turns(id,created,model,question,answer,actions) "
                    "VALUES(?,?,?, ?,?,'[]')",
                    (identifier, timestamp(), "legacy-session", question, answer),
                )
                db.execute(
                    "INSERT INTO search(id,question,answer,review) VALUES(?,?,?,'')",
                    (identifier, question, answer),
                )
            db.execute("INSERT INTO meta VALUES('session_imported','1')")

    def read(self, turn_id: str, offset=0, limit=2400):
        if not isinstance(turn_id, str) or len(turn_id) > 64:
            raise ValueError("Identificador de turno inválido.")
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 200 <= limit <= 4000
        ):
            raise ValueError("Página da conversa inválida.")
        with self.database() as db:
            if turn_id.startswith("task:"):
                item = next(
                    (
                        item
                        for item in self.task_from_database(db)["items"]
                        if "task:" + item["id"] == turn_id
                    ),
                    None,
                )
                if item is None:
                    raise ValueError("Item de memória não encontrado.")
                content = json.dumps(item, ensure_ascii=False)
                return self.redact(
                    {
                        "turn_id": turn_id,
                        "text": content[offset : offset + limit],
                        "offset": offset,
                        "next_offset": offset + limit if offset + limit < len(content) else None,
                        "truncated": offset + limit < len(content),
                        "source": "task_memory_not_authorization",
                    }
                )
            row = db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
            if row is None:
                raise ValueError("Turno não encontrado neste projeto.")
            archive_results = []
            archive_available = False
            try:
                identifier = str(uuid.UUID(turn_id))
                if identifier != turn_id:
                    raise ValueError("Identificador não canônico.")
                raw = private_read(
                    self.root / ".codaro" / f"run-{identifier}.jsonl",
                    MAX_EVENT_BYTES,
                    require_private=True,
                )
                archive_available = True
                for line in raw.splitlines():
                    record = json.loads(line)
                    if record.get("kind") == "tool_result":
                        archive_results.append(record["data"])
            except (ValueError, OSError, KeyError, TypeError, RecursionError):
                pass
            content = json.dumps(
                {
                    "user": row["question"],
                    "assistant": row["answer"],
                    "review": row["review"],
                    "actions": json.loads(row["actions"]),
                    "source": "historical_not_current_evidence",
                    "archive_available": archive_available,
                    "historical_tool_results": archive_results,
                },
                ensure_ascii=False,
            )
            page = content[offset : offset + limit]
            return self.redact(
                {
                    "turn_id": turn_id,
                    "created": row["created"],
                    "offset": offset,
                    "text": page,
                    "next_offset": offset + len(page)
                    if offset + len(page) < len(content)
                    else None,
                    "truncated": offset + len(page) < len(content),
                    "source": "conversation_not_code_evidence",
                }
            )

    def reset_calibration(self):
        with self.database(write=True) as db:
            db.execute("DELETE FROM meta WHERE key LIKE 'calibration:%'")

    def calibration(self, key: str, value=None):
        with self.database(write=value is not None) as db:
            if value is not None:
                db.execute(
                    "INSERT OR REPLACE INTO meta VALUES(?,?)",
                    ("calibration:" + key, json.dumps(value)),
                )
                return None
            row = db.execute(
                "SELECT value FROM meta WHERE key=?", ("calibration:" + key,)
            ).fetchone()
            if row:
                try:
                    return json.loads(row[0])
                except (ValueError, RecursionError):
                    return None
            return None

    def record_change(self, proposal, message: str):
        previous = self.last_turn_id
        try:
            return self.append(
                "event:" + uuid.uuid4().hex,
                f"Revisão local de alteração em {proposal.path}",
                message,
                "local",
                [
                    {
                        "task_id": proposal.task_id,
                        "checkpoint_id": proposal.checkpoint_id,
                        "state": proposal.state,
                        "path": proposal.path,
                        "undo_of": proposal.undo_of,
                    }
                ],
            )
        finally:
            self.last_turn_id = previous
