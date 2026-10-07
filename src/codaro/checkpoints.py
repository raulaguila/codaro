"""Exact local snapshots for approved edits and conflict-checked undo proposals."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import secrets

from codaro.repository import MAX_FILE_BYTES, Repository
from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write, timestamp

MAX_CHECKPOINT_BYTES = 12_000_000


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


class Checkpoints:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.path = repository.root / ".codaro" / "checkpoints.json"

    def load(self):
        try:
            data = private_json(self.path, MAX_CHECKPOINT_BYTES)
        except FileNotFoundError:
            return []
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or data.get("root") != str(self.repository.root)
            or not isinstance(data.get("items"), list)
            or len(data["items"]) > 20
        ):
            raise ValueError("Checkpoints incompatíveis com este projeto.")
        ids = set()
        for item in data["items"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or len(item["id"]) != 12
                or item["id"] in ids
                or not isinstance(item.get("path"), str)
                or len(item["path"]) > 2000
                or not isinstance(item.get("created"), str)
                or len(item["created"]) > 80
                or item.get("state") not in {"ready", "applied", "failed", "undone"}
            ):
                raise ValueError("Checkpoint inválido.")
            ids.add(item["id"])
            if any(
                type(item.get(key, True)) is not bool for key in ("before_exists", "after_exists")
            ):
                raise ValueError("Estado de existência inválido no checkpoint.")
            if (
                type(item.get("file_mode", 0o644)) is not int
                or not 0 <= item.get("file_mode", 0o644) <= 0o777
            ):
                raise ValueError("Permissões inválidas no checkpoint.")
            for key in ("before", "after"):
                try:
                    raw = base64.b64decode(item[key], validate=True)
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError("Snapshot inválido.") from exc
                if (
                    len(raw) > MAX_FILE_BYTES
                    or b"\0" in raw
                    or digest(raw) != item.get(key + "_hash")
                ):
                    raise ValueError("Snapshot inválido.")
        return data["items"]

    def save(self, items):
        retained = items[-20:]
        while True:
            data = json.dumps(
                {"version": 1, "root": str(self.repository.root), "items": retained},
                ensure_ascii=False,
            ).encode()
            if len(data) <= MAX_CHECKPOINT_BYTES:
                atomic_write(self.path, data)
                return
            if len(retained) <= 1:
                raise ValueError("Checkpoint excede o limite.")
            retained.pop(0)

    def prepare(self, proposal):
        with private_lock(self.path.with_suffix(".lock")):
            return self._prepare(proposal)

    def _prepare(self, proposal):
        items = self.load()
        item = {
            "id": secrets.token_hex(6),
            "path": proposal.path,
            "created": timestamp(),
            "state": "ready",
            "reason": proposal.reason[:500],
            "task_id": proposal.task_id,
            "before_exists": proposal.before_exists,
            "after_exists": proposal.after_exists,
            "file_mode": proposal.file_mode,
        }
        for name in ("before", "after"):
            raw = getattr(proposal, name)
            item[name] = base64.b64encode(raw).decode("ascii")
            item[name + "_hash"] = digest(raw)
        self.save([*items, item])
        return item["id"]

    def mark(self, identifier, state):
        with private_lock(self.path.with_suffix(".lock")):
            return self._mark(identifier, state)

    def _mark(self, identifier, state):
        items = self.load()
        item = next((item for item in items if item["id"] == identifier), None)
        if item is None:
            raise ValueError("Checkpoint não encontrado.")
        item["state"] = state
        self.save(items)

    def list(self):
        output = []
        for item in reversed(self.load()):
            can_undo = False
            try:
                target = self.repository.resolve_destination(item["path"])
                can_undo = item["state"] in {"ready", "applied"} and (
                    digest(self.repository.read_bytes(target)) == item["after_hash"]
                    if item.get("after_exists", True)
                    else not target.exists()
                )
            except (ValueError, OSError):
                pass
            output.append(
                {key: item[key] for key in ("id", "path", "created", "state")}
                | {"task_id": item.get("task_id", "")}
                | {"can_undo": can_undo}
            )
        return output

    def proposal(self, identifier=None):
        from codaro.edits import EditProposal

        items = self.load()
        eligible = [item for item in reversed(items) if item["state"] in {"ready", "applied"}]
        item = next(
            (item for item in eligible if identifier is None or item["id"] == identifier), None
        )
        if item is None:
            raise ValueError("Nenhuma alteração disponível para desfazer.")
        target = self.repository.resolve_destination(item["path"])
        before = base64.b64decode(item["after"], validate=True)
        after = base64.b64decode(item["before"], validate=True)
        matches = (
            self.repository.read_bytes(target) == before
            if item.get("after_exists", True)
            else not target.exists()
        )
        if not matches:
            raise ValueError("Arquivo mudou após a edição; desfazer bloqueado.")
        lines = difflib.unified_diff(
            before.decode("utf-8-sig").splitlines(keepends=True),
            after.decode("utf-8-sig").splitlines(keepends=True),
            f"a/{item['path']}",
            f"b/{item['path']}",
        )
        diff = "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in lines
        )
        return EditProposal(
            secrets.token_hex(6),
            item["path"],
            "Desfazer edição aprovada " + item["id"],
            before,
            after,
            diff,
            undo_of=item["id"],
            before_exists=item.get("after_exists", True),
            after_exists=item.get("before_exists", True),
            file_mode=item.get("file_mode", 0o644),
        )
