"""Interaction-sized reversals with full preflight and an inspectable partial journal."""

import base64
import difflib
import json
import re
import secrets

from codaro.edits import EditProposal
from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write, timestamp


class UndoHistory:
    def __init__(self, edits, session_id="default"):
        self.edits, self.session_id = edits, session_id
        self.path = edits.repository.root / ".codaro/reversals.json"

    def load(self):
        try:
            value = private_json(self.path, 128_000)
        except FileNotFoundError:
            return []
        if not isinstance(value, list) or len(value) > 20:
            raise ValueError("Histórico de reversões inválido.")
        for item in value:
            if (
                not isinstance(item, dict)
                or item.get("state") not in {"prepared", "applied", "partial", "redone"}
                or not isinstance(item.get("checkpoint_ids"), list)
                or not 1 <= len(item["checkpoint_ids"]) <= 20
            ):
                raise ValueError("Reversão inválida.")
            from codaro.session_catalog import validate_id

            validate_id(item.get("session_id"))
            if not re.fullmatch(r"[a-f0-9]{12}", item.get("id", "")) or not all(
                isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{12}", identifier)
                for identifier in item["checkpoint_ids"]
            ):
                raise ValueError("Identificadores de reversão inválidos.")
        return value

    def preview(self, run_id=None, *, redo=False):
        items = self.edits.checkpoints.load()
        if redo:
            reversals = [
                item
                for item in self.load()
                if item["state"] == "applied" and item.get("session_id") == self.session_id
            ]
            if not reversals:
                raise ValueError("Nenhuma interação disponível para refazer.")
            record = reversals[-1]
            selected = [item for item in items if item["id"] in record["checkpoint_ids"]]
            if len(selected) != len(record["checkpoint_ids"]):
                raise ValueError("Snapshots removidos pela retenção; refazer indisponível.")
        else:
            eligible = [
                item
                for item in items
                if item["state"] == "applied"
                and item.get("session_id", "default") == self.session_id
                and item.get("run_id")
                and not item.get("reversal")
            ]
            chosen = run_id or (eligible[-1]["run_id"] if eligible else None)
            selected = [item for item in eligible if item["run_id"] == chosen]
            record = {
                "id": secrets.token_hex(6),
                "run_id": chosen,
                "session_id": self.session_id,
                "created": timestamp(),
                "checkpoint_ids": [item["id"] for item in selected],
            }
        if not selected:
            raise ValueError("Nenhuma interação disponível para desfazer.")
        # Consolidate repeated writes to one file: original before and final after.
        files = {}
        for item in selected:
            first, _ = files.get(item["path"], (item, item))
            files[item["path"]] = first, item
        proposals = []
        for path, (first, last) in files.items():
            source, target = (first, last) if redo else (last, first)
            before_key, after_key = ("before", "after") if redo else ("after", "before")
            before = base64.b64decode(source[before_key], validate=True)
            after = base64.b64decode(target[after_key], validate=True)
            before_exists = source.get(before_key + "_exists", True)
            after_exists = target.get(after_key + "_exists", True)
            resolved = self.edits.repository.resolve_destination(path)
            if (
                self.edits.repository.read_bytes(resolved) != before
                if before_exists
                else resolved.exists()
            ):
                raise ValueError("Arquivo mudou após a interação: " + path + ". Nada aplicado.")
            diff = "".join(
                difflib.unified_diff(
                    before.decode("utf-8-sig").splitlines(keepends=True),
                    after.decode("utf-8-sig").splitlines(keepends=True),
                    "a/" + path,
                    "b/" + path,
                )
            )
            proposals.append(
                EditProposal(
                    secrets.token_hex(6),
                    path,
                    "Refazer interação" if redo else "Desfazer interação",
                    before,
                    after,
                    diff,
                    before_exists=before_exists,
                    after_exists=after_exists,
                    file_mode=target.get("file_mode", 0o644),
                )
            )
        return record, proposals

    def apply(self, record, proposals, *, redo=False):
        if self.edits.pending:
            raise ValueError("Revise as propostas pendentes primeiro.")
        # All files must still match the reviewed state before the first mutation.
        for proposal in proposals:
            target = self.edits.repository.resolve_destination(proposal.path)
            if (
                self.edits.repository.read_bytes(target) != proposal.before
                if proposal.before_exists
                else target.exists()
            ):
                raise ValueError("Arquivo mudou após a revisão. Nada aplicado.")
        with private_lock(self.path.with_suffix(".lock")):
            records = self.load()
            transaction = {**record, "state": "prepared", "applied_paths": []}
            if not redo:
                records.append(transaction)
            else:
                position = next(
                    (i for i, item in enumerate(records) if item.get("id") == record["id"]), None
                )
                if position is None or records[position]["state"] != "applied":
                    raise ValueError("Reversão não está disponível para refazer.")
                records[position] = transaction
            records = records[-20:]
            atomic_write(self.path, json.dumps(records).encode())
            for proposal in proposals:
                self.edits.proposals[proposal.id] = proposal
                try:
                    self.edits.apply(proposal.id)
                    transaction["applied_paths"].append(proposal.path)
                    # Generated checkpoints cannot be picked as a normal interaction.
                    with private_lock(self.edits.checkpoints.path.with_suffix(".lock")):
                        checkpoints = self.edits.checkpoints.load()
                        for item in checkpoints:
                            if item["id"] == proposal.checkpoint_id:
                                item["reversal"] = record["id"]
                        self.edits.checkpoints.save(checkpoints)
                except (OSError, ValueError):
                    transaction["state"] = "partial"
                    atomic_write(self.path, json.dumps(records).encode())
                    raise
                atomic_write(self.path, json.dumps(records).encode())
            transaction["state"] = "redone" if redo else "applied"
            atomic_write(self.path, json.dumps(records).encode())
            for identifier in record["checkpoint_ids"]:
                self.edits.checkpoints.mark(identifier, "applied" if redo else "undone")
        return transaction
