"""Bounded investigation notes with source fingerprints, separate from user decisions."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import nullcontext

from codaro.chunks import terms
from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write, timestamp


class InvestigationMemory:
    def __init__(self, repository, *, redact=lambda value: value, ephemeral=False):
        self.repository, self.redact, self.ephemeral = repository, redact, ephemeral
        self.path = repository.root / ".codaro/findings.json"
        self.items = []

    def load(self):
        if self.ephemeral:
            return self.items.copy()
        try:
            data = private_json(self.path, 256000, require_private=True)
        except FileNotFoundError:
            return []
        if not isinstance(data, list) or len(data) > 120:
            raise ValueError("Memória de investigação inválida.")
        for item in data:
            if (
                not isinstance(item, dict)
                or item.get("kind") not in {"finding", "hypothesis", "pending"}
                or not isinstance(item.get("text"), str)
                or len(item["text"]) > 800
                or not isinstance(item.get("sources"), list)
                or len(item["sources"]) > 6
                or item.get("source") != "assistant_note_not_code_evidence_or_authorization"
                or not isinstance(item.get("id"), str)
                or not re.fullmatch(r"[a-f0-9]{16}", item["id"])
                or not isinstance(item.get("created"), str)
            ):
                raise ValueError("Nota de investigação inválida.")
            for source in item["sources"]:
                if (
                    not isinstance(source, dict)
                    or not isinstance(source.get("path"), str)
                    or not isinstance(source.get("file_hash"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", source["file_hash"])
                    or len(source["path"]) > 500
                    or type(source.get("start_line")) is not int
                    or type(source.get("end_line")) is not int
                    or not 1 <= source["start_line"] <= source["end_line"]
                ):
                    raise ValueError("Fonte de investigação inválida.")
        return data

    def record(self, kind, text, sources=()):
        if (
            kind not in {"finding", "hypothesis", "pending"}
            or not isinstance(text, str)
            or not text.strip()
            or len(text) > 800
            or len(sources) > 6
        ):
            raise ValueError(
                "Nota inválida: finding, hypothesis ou pending; até 800 caracteres e seis fontes."
            )
        snapshots = []
        for source in sources:
            raw = self.repository.read_bytes(self.repository.resolve_file(source["path"]))
            start, end = source["start_line"], source["end_line"]
            if (
                type(start) is not int
                or type(end) is not int
                or not 1 <= start <= end <= len(raw.decode("utf-8-sig").splitlines())
            ):
                raise ValueError("Intervalo da fonte inválido.")
            snapshots.append(
                {
                    **source,
                    "file_hash": hashlib.sha256(raw).hexdigest(),
                }
            )
        item = self.redact(
            {
                "id": uuid.uuid4().hex[:16],
                "kind": kind,
                "text": text,
                "sources": snapshots,
                "created": timestamp(),
                "source": "assistant_note_not_code_evidence_or_authorization",
            }
        )
        with nullcontext() if self.ephemeral else private_lock(self.path.with_suffix(".lock")):
            data = self.load()
            data = [old for old in data if old["text"] != item["text"]]
            data = [*data, item][-120:]
            while len(json.dumps(data, ensure_ascii=False).encode()) > 256000:
                data.pop(0)
            if self.ephemeral:
                self.items = data
            else:
                atomic_write(self.path, json.dumps(data, ensure_ascii=False).encode())
        return {"saved": True, "item": item}

    def search(self, query="", limit=5):
        if (
            not isinstance(query, str)
            or len(query) > 1000
            or type(limit) is not int
            or not 1 <= limit <= 12
        ):
            raise ValueError("Busca de investigação inválida.")
        tokens = set(terms(query))
        selected = []
        for item in reversed(self.load()):
            score = len(
                tokens
                & set(
                    terms(
                        item["text"] + " " + " ".join(source["path"] for source in item["sources"]),
                        None,
                    )
                )
            )
            if tokens and not score:
                continue
            selected.append((score, item))
        selected.sort(key=lambda entry: entry[0], reverse=True)
        results, hashes = [], {}
        for _, item in selected[:limit]:
            current = bool(item["sources"])
            for source in item["sources"]:
                try:
                    if source["path"] not in hashes:
                        path = self.repository.resolve_file(source["path"])
                        hashes[source["path"]] = hashlib.sha256(
                            self.repository.read_bytes(path)
                        ).hexdigest()
                    current = current and hashes[source["path"]] == source["file_hash"]
                except (OSError, ValueError):
                    hashes[source["path"]] = None
                    current = False
            results.append(
                {
                    **item,
                    "source_status": "unchanged"
                    if current
                    else "stale"
                    if item["sources"]
                    else "unreferenced",
                }
            )
        return self.redact(
            {
                "results": results,
                "notice": "Notas do agente: fontes sem alterações não comprovam a interpretação. "
                "Releia antes de editar.",
            }
        )
