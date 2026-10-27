from __future__ import annotations

import hashlib
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from codaro.chunks import chunks_for, terms
from codaro.dependencies import imports_for
from codaro.repository import Repository
from codaro.retrieval import matches_scope, query_terms, scope_options

SCHEMA_VERSION = 6


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
        self.last_search = {}
        self.semantic = None
        self.cancelled = None
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
                        DROP TABLE IF EXISTS imports;
                        CREATE TABLE files(
                            path TEXT PRIMARY KEY, digest TEXT NOT NULL, stamp TEXT, verified REAL
                        );
                        CREATE TABLE chunks(
                            id INTEGER PRIMARY KEY, path TEXT NOT NULL, symbol TEXT NOT NULL,
                            start INTEGER, end INTEGER, signature TEXT, content TEXT,
                            declaration_start INTEGER, declaration_end INTEGER,
                            kind TEXT, language TEXT
                        );
                        CREATE INDEX chunks_path ON chunks(path);
                        CREATE INDEX chunks_symbol ON chunks(symbol);
                        CREATE TABLE imports(path TEXT, target TEXT, line INTEGER, language TEXT);
                        CREATE VIRTUAL TABLE search USING fts5(
                            path, symbol, signature, content, tokenize='unicode61'
                        );
                        DROP TABLE IF EXISTS vectors;
                        PRAGMA user_version = 6;
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

    def update(self, *, force=False, cancelled=None) -> dict:
        cancelled = cancelled if cancelled is not None else self.cancelled
        paths = self.repository.files()
        existing = {row["path"]: dict(row) for row in self.db.execute("SELECT * FROM files")}
        seen: set[str] = set()
        changed = skipped = removed = reused = 0
        now = time.time()
        with self.db:
            for path in paths:
                if cancelled is not None and cancelled.is_set():
                    from codaro.llm import RequestCancelled

                    raise RequestCancelled("Indexação cancelada.")
                name = path.relative_to(self.repository.root).as_posix()
                try:
                    info = path.stat(follow_symlinks=False)
                    stamp = repr(
                        (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    )
                    old = existing.get(name)
                    if (
                        not force
                        and old
                        and old["stamp"] == stamp
                        and 0 <= now - (old["verified"] or 0) < 60
                    ):
                        seen.add(name)
                        reused += 1
                        continue
                    raw = self.repository.read_bytes(path)
                    text = raw.decode("utf-8-sig")
                except (OSError, ValueError):
                    skipped += 1
                    continue
                seen.add(name)
                digest = hashlib.sha256(raw).hexdigest()
                if old and old["digest"] == digest:
                    self.db.execute(
                        "UPDATE files SET stamp=?,verified=? WHERE path=?", (stamp, now, name)
                    )
                    continue
                self._remove(name)
                self.db.executemany(
                    "INSERT INTO imports VALUES (?,?,?,?)",
                    (
                        (name, item["target"], item["line"], item["language"])
                        for item in imports_for(path, text)
                    ),
                )
                for chunk in chunks_for(path, text):
                    cursor = self.db.execute(
                        "INSERT INTO chunks(path,symbol,start,end,signature,content,"
                        "declaration_start,declaration_end,kind,language) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            name,
                            chunk.symbol,
                            chunk.start,
                            chunk.end,
                            chunk.signature,
                            chunk.content,
                            chunk.declaration_start,
                            chunk.declaration_end,
                            chunk.kind,
                            chunk.language,
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
                self.db.execute("INSERT INTO files VALUES (?,?,?,?)", (name, digest, stamp, now))
                changed += 1
            for name in existing.keys() - seen:
                self._remove(name)
                removed += 1
        return {
            "files": len(seen),
            "changed": changed,
            "removed": removed,
            "skipped": skipped,
            "reused": reused,
            "chunks": self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        }

    def _remove(self, name: str):
        self.db.execute("DELETE FROM imports WHERE path=?", (name,))
        self.db.execute(
            "DELETE FROM search WHERE rowid IN (SELECT id FROM chunks WHERE path=?)", (name,)
        )
        self.db.execute("DELETE FROM chunks WHERE path=?", (name,))
        self.db.execute("DELETE FROM files WHERE path=?", (name,))

    def search(
        self,
        query: str,
        limit: int = 6,
        *,
        path=None,
        glob=None,
        language=None,
        extension=None,
        kind=None,
    ) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError("A busca deve ter entre 1 e 1000 caracteres.")
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("O limite deve ficar entre 1 e 12 resultados.")
        # Refresh so changing ignore rules or editing files cannot yield stale previews.
        started = time.monotonic()
        self.update()
        filters = scope_options(path, glob, language, extension)
        if kind is not None and (not isinstance(kind, str) or len(kind) > 30):
            raise ValueError("Tipo de símbolo inválido.")
        allowed = [
            row[0]
            for row in self.db.execute("SELECT path FROM files")
            if matches_scope(row[0], filters)
        ]
        # A temporary scope table avoids SQLite's parameter-count limit in monorepos.
        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS scope_paths(path TEXT PRIMARY KEY)")
        self.db.execute("DELETE FROM scope_paths")
        self.db.executemany("INSERT INTO scope_paths VALUES (?)", ((p,) for p in allowed))
        scope = "c.path IN (SELECT path FROM scope_paths)" + (" AND c.kind=?" if kind else "")
        scope_params = (kind,) if kind else ()
        if filters.get("language"):
            scope += " AND c.language=?"
            scope_params = (*scope_params, filters["language"])
        tokens = query_terms(query)
        if not tokens:
            self.last_search = {
                "query": query,
                "filters": filters,
                "candidates": 0,
                "returned": 0,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "strategy": "lexical_exact_coverage",
                "semantic": "not_used",
            }
            return []
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        lexical = list(
            self.db.execute(
                "SELECT search.rowid FROM search JOIN chunks c ON c.id=search.rowid "
                "WHERE search MATCH ? AND "
                + scope
                + " ORDER BY bm25(search, 2.0, 8.0, 4.0, 1.0), search.rowid LIMIT 120",
                (expression, *scope_params),
            )
        )
        needle = query.strip().lower()
        identifiers = [
            word.strip("`(),;")
            for word in query.split()
            if "_" in word or any(char.isupper() for char in word[1:])
        ][:40]
        exact_values = list(dict.fromkeys([needle, *[word.lower() for word in identifiers]]))
        placeholders = ",".join("?" for _ in exact_values)
        exact = list(
            self.db.execute(
                "SELECT id FROM chunks c WHERE (lower(symbol) IN ("
                + placeholders
                + ") OR lower(path)=? "
                "OR substr(lower(symbol),-length(?))=?) AND "
                + scope
                + " ORDER BY symbol,path,start LIMIT 120",
                (*exact_values, needle, "." + needle, "." + needle, *scope_params),
            )
        )
        scores: dict[int, float] = defaultdict(float)
        exact_ids = {row[0] for row in exact}
        rankings = [exact, lexical]
        hybrid = False
        if self.semantic is not None:
            rankings.append(
                [
                    (identifier,)
                    for identifier in self.semantic.search(
                        self,
                        query,
                        allowed,
                        kind=kind,
                        language=filters.get("language"),
                        cancelled=self.cancelled,
                    )
                ]
            )
            hybrid = bool(rankings[-1])
        for ranking in rankings:
            for rank, row in enumerate(ranking, 1):
                scores[row[0]] += 1 / (60 + rank)
        candidates = []
        if scores:
            placeholders = ",".join("?" for _ in scores)
            for row in self.db.execute(
                f"SELECT * FROM chunks c WHERE id IN ({placeholders}) AND " + scope,
                (*scores, *scope_params),
            ):
                candidates.append(dict(row))
        candidates.sort(
            key=lambda row: (
                row["id"] not in exact_ids,
                row["symbol"].startswith("<"),
                -(
                    scores[row["id"]]
                    + 0.012
                    * len(set(tokens) & set(terms(row["signature"] + " " + row["content"], None)))
                    / max(1, len(tokens))
                    + (0.002 if row["kind"] in {"function", "method", "procedure"} else 0)
                ),
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
                    "start_line": max(row["start"], row["declaration_start"] or row["start"]),
                    "end_line": row["end"],
                    "signature": safe_preview(row["signature"]),
                    "preview": safe_preview(row["content"][:240]),
                    "kind": row["kind"],
                    "language": row["language"],
                    "file_hash": self.db.execute(
                        "SELECT digest FROM files WHERE path=?", (row["path"],)
                    ).fetchone()[0],
                }
            )
            if len(selected) == limit:
                break
        self.last_search = {
            "query": query,
            "filters": filters,
            "candidates": len(candidates),
            "returned": len(selected),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "strategy": "hybrid" if hybrid else "lexical_exact_coverage",
            "semantic": getattr(self.semantic, "status", "disabled"),
        }
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
