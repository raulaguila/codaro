"""Private bounded tool outputs, paginated by ID, never edit authorization."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from codaro.storage import private_json, private_lock, private_read
from codaro.tool_registry import definition
from codaro.trace import atomic_write, timestamp

MAX_ARTIFACT_BYTES = 2_000_000
MAX_TOTAL_BYTES = 32_000_000
MAX_ARTIFACTS = 100
RETENTION_DAYS = 7
ID = {"type": "string", "maxLength": 32}
ARTIFACT_TOOLS = [
    definition(
        "get_artifact_info",
        "Metadados do resultado salvo; não comprova código atual.",
        {"artifact_id": ID},
        ["artifact_id"],
    ),
    definition(
        "read_artifact",
        "Página do resultado histórico salvo, sem reler o projeto.",
        {
            "artifact_id": ID,
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 200, "maximum": 4000},
        },
        ["artifact_id"],
    ),
    definition(
        "search_artifact",
        "Busca literal no resultado salvo; retorna trechos limitados.",
        {
            "artifact_id": ID,
            "query": {"type": "string", "maxLength": 200},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        ["artifact_id", "query"],
    ),
]


class ArtifactStore:
    def __init__(self, root, *, redact=lambda x: x, session_id="default"):
        self.directory = root / ".codaro/artifacts"
        self.index_path = root / ".codaro/artifacts.json"
        self.redact, self.session_id = redact, session_id

    def index(self):
        try:
            value = private_json(self.index_path, 256_000)
        except FileNotFoundError:
            return []
        if not isinstance(value, list) or len(value) > MAX_ARTIFACTS:
            raise ValueError("Índice de artefatos inválido.")
        identifiers = set()
        for item in value:
            if (
                not isinstance(item, dict)
                or not re.fullmatch(r"[0-9a-f]{32}", item.get("id", ""))
                or item["id"] in identifiers
                or type(item.get("bytes")) is not int
                or not 0 <= item["bytes"] <= MAX_ARTIFACT_BYTES
                or not isinstance(item.get("session_id"), str)
                or not isinstance(item.get("created"), str)
            ):
                raise ValueError("Metadados do artefato inválidos.")
            created = datetime.fromisoformat(item["created"])
            if created.tzinfo is None:
                raise ValueError("Data do artefato sem fuso.")
            identifiers.add(item["id"])
        return value

    def save(self, text, *, source, run_id="", complete=True):
        identifier = uuid.uuid4().hex
        raw = self.redact(text).encode("utf-8", errors="replace")
        size = len(raw)
        raw = raw[:MAX_ARTIFACT_BYTES].decode("utf-8", errors="ignore").encode("utf-8")
        with private_lock(self.index_path.with_suffix(".lock")):
            previous = self.index()
            if self.directory.is_symlink():
                raise ValueError("Diretório de artefatos não pode ser link.")
            self.directory.mkdir(mode=0o700, exist_ok=True)
            atomic_write(self.directory / (identifier + ".txt"), raw)
            item = {
                "id": identifier,
                "session_id": self.session_id,
                "run_id": run_id,
                "source": source[:100],
                "created": timestamp(),
                "bytes": len(raw),
                "original_bytes": size,
                "complete": complete and size <= MAX_ARTIFACT_BYTES,
            }
            items = [*previous, item]
            cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
            kept, total = [], 0
            for existing in reversed(items):
                if not re.fullmatch(r"[0-9a-f]{32}", existing.get("id", "")):
                    raise ValueError("Identificador de artefato inválido.")
                expired = datetime.fromisoformat(existing["created"]) < cutoff
                if (
                    expired
                    or len(kept) >= MAX_ARTIFACTS
                    or total + existing["bytes"] > MAX_TOTAL_BYTES
                ):
                    (self.directory / (existing["id"] + ".txt")).unlink(missing_ok=True)
                else:
                    total += existing["bytes"]
                    kept.append(existing)
            atomic_write(self.index_path, json.dumps(list(reversed(kept))).encode())
            # Failed writes/crashes may have left unindexed files. Never follow links.
            retained_ids = {existing["id"] for existing in kept}
            for candidate in self.directory.iterdir():
                if (
                    re.fullmatch(r"[0-9a-f]{32}\.txt", candidate.name)
                    and candidate.stem not in retained_ids
                    and not candidate.is_symlink()
                ):
                    candidate.unlink(missing_ok=True)
        return item

    def info(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Identificador de artefato inválido.")
        item = next((item for item in self.index() if item.get("id") == identifier), None)
        if not item or item.get("session_id") != self.session_id:
            raise ValueError("Artefato não encontrado nesta sessão ou removido pela retenção.")
        return {**item, "source_kind": "historical_tool_output_not_current_evidence"}

    def text(self, identifier):
        self.info(identifier)
        return self.redact(
            private_read(
                self.directory / (identifier + ".txt"), MAX_ARTIFACT_BYTES, require_private=True
            ).decode("utf-8")
        )

    def read(self, identifier, offset=0, limit=2400):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 200 <= limit <= 4000
        ):
            raise ValueError("Página inválida.")
        text = self.text(identifier)
        return {
            "artifact_id": identifier,
            "text": text[offset : offset + limit],
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(text) else None,
            "complete": self.info(identifier)["complete"],
            "source": "historical_tool_output_not_current_evidence",
        }

    def search(self, identifier, query, limit=5):
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 200
            or type(limit) is not int
            or not 1 <= limit <= 8
        ):
            raise ValueError("Busca inválida.")
        text = self.text(identifier)
        found, start = [], 0
        while len(found) < limit:
            position = text.lower().find(query.lower(), start)
            if position < 0:
                break
            found.append(
                {"offset": position, "snippet": text[max(0, position - 100) : position + 300]}
            )
            start = position + max(1, len(query))
        return {
            "artifact_id": identifier,
            "matches": found,
            "complete": self.info(identifier)["complete"],
            "source": "historical_tool_output_not_current_evidence",
        }
