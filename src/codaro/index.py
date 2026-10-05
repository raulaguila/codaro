from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path

from codaro.chunks import chunks_for, terms
from codaro.repository import Repository

SCHEMA_VERSION = 2


class CodeIndex:
    def __init__(self, repository: Repository):
        self.repository = repository
        storage = repository.root / ".codaro"
        if storage.is_symlink():
            raise ValueError("O diretório .codaro não pode ser um link simbólico.")
        storage.mkdir(mode=0o700, exist_ok=True)
        for name in (
            "index.sqlite3",
            "index.sqlite3-journal",
            "index.sqlite3-wal",
            "index.sqlite3-shm",
        ):
            if (storage / name).is_symlink():
                raise ValueError(
                    "O índice e seus arquivos auxiliares não podem ser links simbólicos."
                )
        self.db = sqlite3.connect(storage / "index.sqlite3", timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            (storage / "index.sqlite3").chmod(0o600)
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("Índice criado por uma versão mais nova do Codaro.")
            if version < SCHEMA_VERSION:
                with self.db:
                    self.db.executescript("""
                        DROP TABLE IF EXISTS search;
                        DROP TABLE IF EXISTS chunks;
                        DROP TABLE IF EXISTS files;
                        CREATE TABLE files(path TEXT PRIMARY KEY, digest TEXT NOT NULL);
                        CREATE TABLE chunks(
                            id INTEGER PRIMARY KEY, path TEXT NOT NULL, symbol TEXT NOT NULL,
                            start INTEGER, end INTEGER, signature TEXT, content TEXT,
                            declaration_start INTEGER, declaration_end INTEGER
                        );
                        CREATE INDEX chunks_path ON chunks(path);
                        CREATE INDEX chunks_symbol ON chunks(symbol);
                        CREATE VIRTUAL TABLE search USING fts5(
                            path, symbol, signature, content, tokenize='unicode61'
                        );
                        PRAGMA user_version = 2;
                    """)
        except Exception:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()

    def update(self) -> dict:
        paths = self.repository.files()
        existing = dict(self.db.execute("SELECT path, digest FROM files"))
        seen: set[str] = set()
        changed = skipped = removed = 0
        with self.db:
            for path in paths:
                name = str(path.relative_to(self.repository.root))
                try:
                    raw = self.repository.read_bytes(path)
                    text = raw.decode("utf-8-sig")
                except (OSError, ValueError):
                    skipped += 1
                    continue
                seen.add(name)
                digest = hashlib.sha256(raw).hexdigest()
                if existing.get(name) == digest:
                    continue
                self._remove(name)
                for chunk in chunks_for(path, text):
                    cursor = self.db.execute(
                        "INSERT INTO chunks(path,symbol,start,end,signature,content,"
                        "declaration_start,declaration_end) VALUES (?,?,?,?,?,?,?,?)",
                        (
                            name,
                            chunk.symbol,
                            chunk.start,
                            chunk.end,
                            chunk.signature,
                            chunk.content,
                            chunk.declaration_start,
                            chunk.declaration_end,
                        ),
                    )
                    self.db.execute(
                        "INSERT INTO search(rowid,path,symbol,signature,content) "
                        "VALUES (?,?,?,?,?)",
                        (
                            cursor.lastrowid,
                            " ".join(terms(name, None)),
                            " ".join(terms(chunk.symbol, None)),
                            " ".join(terms(chunk.signature, None)),
                            " ".join(terms(chunk.content, None)),
                        ),
                    )
                self.db.execute("INSERT INTO files VALUES (?,?)", (name, digest))
                changed += 1
            for name in existing.keys() - seen:
                self._remove(name)
                removed += 1
        return {
            "files": len(seen),
            "changed": changed,
            "removed": removed,
            "skipped": skipped,
            "chunks": self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        }

    def _remove(self, name: str):
        self.db.execute(
            "DELETE FROM search WHERE rowid IN (SELECT id FROM chunks WHERE path=?)", (name,)
        )
        self.db.execute("DELETE FROM chunks WHERE path=?", (name,))
        self.db.execute("DELETE FROM files WHERE path=?", (name,))

    def search(self, query: str, limit: int = 6) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("A busca deve ter entre 1 e 1000 caracteres.")
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("O limite deve ficar entre 1 e 12 resultados.")
        # Refresh so changing ignore rules or editing files cannot yield stale previews.
        self.update()
        tokens = terms(query)
        if not tokens:
            return []
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        lexical = list(
            self.db.execute(
                "SELECT rowid FROM search WHERE search MATCH ? "
                "ORDER BY bm25(search, 2.0, 8.0, 4.0, 1.0), rowid LIMIT 80",
                (expression,),
            )
        )
        needle = query.strip().lower()
        exact = list(
            self.db.execute(
                "SELECT id FROM chunks WHERE lower(symbol)=? OR lower(path)=? "
                "OR substr(lower(symbol),-length(?))=? "
                "ORDER BY symbol,path,start LIMIT 80",
                (needle, needle, "." + needle, "." + needle),
            )
        )
        scores: dict[int, float] = defaultdict(float)
        exact_ids = {row[0] for row in exact}
        for ranking in (exact, lexical):
            for rank, row in enumerate(ranking, 1):
                scores[row[0]] += 1 / (60 + rank)
        candidates = []
        if scores:
            placeholders = ",".join("?" for _ in scores)
            for row in self.db.execute(
                f"SELECT * FROM chunks WHERE id IN ({placeholders})", tuple(scores)
            ):
                candidates.append(dict(row))
        candidates.sort(
            key=lambda row: (
                row["id"] not in exact_ids,
                row["symbol"].startswith("<"),
                -scores[row["id"]],
                row["path"],
                row["start"],
            )
        )
        selected = []
        for row in candidates:
            duplicate = False
            for old in selected:
                overlap = old["path"] == row["path"] and (
                    max(old["start_line"], row["start"]) <= min(old["end_line"], row["end"])
                )
                same_family = old["symbol"].startswith(row["symbol"] + ".") or row[
                    "symbol"
                ].startswith(old["symbol"] + ".")
                if overlap and (
                    old["symbol"] == row["symbol"] or row["symbol"].startswith("<") or same_family
                ):
                    duplicate = True
                    break
            if duplicate:
                continue
            selected.append(
                {
                    "path": row["path"],
                    "symbol": row["symbol"],
                    "start_line": row["start"],
                    "end_line": row["end"],
                    "signature": safe_preview(row["signature"]),
                    "preview": safe_preview(row["content"][:240]),
                }
            )
            if len(selected) == limit:
                break
        return selected

    def read_symbol(self, path: str, symbol: str, start_line: int | None = None) -> dict:
        # Parse the current file: stale indexed line numbers must never read unrelated code.
        text = self.repository.read_text(path)
        matching = [
            chunk
            for chunk in chunks_for(Path(path), text)
            if chunk.symbol == symbol and chunk.declaration_start is not None
        ]
        spans = {(chunk.declaration_start, chunk.declaration_end) for chunk in matching}
        if start_line is not None:
            spans = {(start, end) for start, end in spans if start <= start_line <= end}
        if not spans:
            raise ValueError("Símbolo não encontrado no arquivo atual. Faça uma nova busca.")
        if len(spans) > 1:
            raise ValueError("Nome ambíguo. Informe start_line do resultado ou use read_lines.")
        start, end = spans.pop()
        result = self.repository.render_lines(path, text, start, min(end, start + 159))
        result["symbol"] = symbol
        result["symbol_end_line"] = end
        result["truncated"] = result["truncated"] or end > result["end_line"]
        if not result["truncated"]:
            result["next_start_line"] = None
        return result


def safe_preview(text: str) -> str:
    return "".join(c if c in "\n\t" or ord(c) >= 32 and ord(c) != 127 else "�" for c in text)
