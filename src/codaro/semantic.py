"""Opt-in local embeddings, cached by model and content with lexical fallback."""

from __future__ import annotations

import hashlib
import json
import math
import os

MAX_VECTOR_CANDIDATES = 10000


class SemanticSearch:
    def __init__(self, encoder=None, model=None):
        self.model = model or os.environ.get("CODARO_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
        self.encoder = encoder
        self.status = "not_initialized"

    def search(self, index, query, allowed, *, kind=None, language=None, cancelled=None):
        try:
            if self.encoder is None:
                from fastembed import TextEmbedding

                self.encoder = TextEmbedding(model_name=self.model)
            db = index.db
            db.execute(
                "CREATE TABLE IF NOT EXISTS vectors(id INTEGER, model TEXT, digest TEXT, "
                "vector TEXT, PRIMARY KEY(id,model))"
            )
            clause = (" AND c.kind=?" if kind else "") + (" AND c.language=?" if language else "")
            parameters = (*((kind,) if kind else ()), *((language,) if language else ()))
            rows = list(
                db.execute(
                    "SELECT c.id,c.path,c.symbol,c.signature,substr(c.content,1,6000) AS content "
                    "FROM chunks c WHERE c.path IN (SELECT path FROM scope_paths)"
                    + clause
                    + " ORDER BY c.id LIMIT "
                    + str(MAX_VECTOR_CANDIDATES + 1),
                    parameters,
                )
            )
            scope_limited = len(rows) > MAX_VECTOR_CANDIDATES
            rows = rows[:MAX_VECTOR_CANDIDATES]
            cached = {
                row[0]: (row[1], row[2])
                for row in db.execute(
                    "SELECT v.id,v.digest,v.vector FROM vectors v JOIN chunks c ON c.id=v.id "
                    "WHERE v.model=? AND c.path IN (SELECT path FROM scope_paths)"
                    + clause
                    + " ORDER BY c.id LIMIT "
                    + str(MAX_VECTOR_CANDIDATES),
                    (self.model, *parameters),
                )
            }
            pending = []
            for row in rows:
                text = f"{row['path']} {row['symbol']}\n{row['signature']}\n{row['content']}"[:6000]
                digest = hashlib.sha256(text.encode()).hexdigest()
                if row["id"] not in cached or cached[row["id"]][0] != digest:
                    pending.append((row["id"], digest, text))
            # Incremental warm-up bounds work per question; subsequent searches continue it.
            for offset in range(0, min(256, len(pending)), 32):
                if cancelled is not None and cancelled.is_set():
                    from codaro.llm import RequestCancelled

                    raise RequestCancelled("Busca semântica cancelada.")
                batch = pending[offset : offset + 32]
                vectors = list(self.encoder.embed([item[2] for item in batch]))
                if len(vectors) != len(batch):
                    raise ValueError("Quantidade inválida de embeddings.")
                for (identifier, digest, _), vector in zip(batch, vectors, strict=True):
                    values = checked_vector(vector)
                    encoded = json.dumps(values, separators=(",", ":"))
                    db.execute(
                        "INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)",
                        (identifier, self.model, digest, encoded),
                    )
                    cached[identifier] = (digest, encoded)
            db.execute("DELETE FROM vectors WHERE id NOT IN (SELECT id FROM chunks)")
            db.commit()
            query_vector = checked_vector(next(iter(self.encoder.query_embed(query))))
            ranked = []
            for row in rows:
                if row["id"] not in cached:
                    continue
                text = f"{row['path']} {row['symbol']}\n{row['signature']}\n{row['content']}"[:6000]
                if cached[row["id"]][0] != hashlib.sha256(text.encode()).hexdigest():
                    continue
                vector = json.loads(cached[row["id"]][1])
                if len(vector) != len(query_vector):
                    continue
                ranked.append(
                    (sum(a * b for a, b in zip(vector, query_vector, strict=True)), row["id"])
                )
            self.status = (
                "scope_limited"
                if scope_limited
                else "ready"
                if len(pending) <= 256
                else "partial_index_warming"
            )
            return [identifier for _, identifier in sorted(ranked, reverse=True)[:80]]
        except Exception as exc:
            from codaro.llm import RequestCancelled

            if isinstance(exc, RequestCancelled):
                raise
            # Optional availability is observable without breaking ordinary retrieval.
            self.status = "unavailable:" + type(exc).__name__
            return []


def checked_vector(vector):
    values = [float(value) for value in vector]
    if not 1 <= len(values) <= 4096 or not all(math.isfinite(value) for value in values):
        raise ValueError("Embedding inválido.")
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        raise ValueError("Embedding vazio.")
    return [value / norm for value in values]
