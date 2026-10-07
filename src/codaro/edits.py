from __future__ import annotations

import difflib
import os
import secrets
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from codaro.checkpoints import Checkpoints
from codaro.repository import MAX_FILE_BYTES, Repository, RepositoryError


@dataclass
class EditProposal:
    id: str
    path: str
    reason: str
    before: bytes
    after: bytes
    diff: str
    state: str = "pending"
    checkpoint_id: str | None = None
    undo_of: str | None = None
    checkpoint_warning: str = ""
    task_id: str = ""


class EditManager:
    """Local proposals only. Applying is a separate action owned by the UI, never a tool."""

    def __init__(self, repository: Repository):
        self.repository = repository
        self.proposals: dict[str, EditProposal] = {}
        self.observed: dict[str, tuple[bytes, list[tuple[int, int]]]] = {}
        self._lock = threading.Lock()
        self.checkpoints = Checkpoints(repository)

    @property
    def pending(self) -> list[EditProposal]:
        return [proposal for proposal in self.proposals.values() if proposal.state == "pending"]

    def observe(self, result: dict, data: bytes):
        if not result.get("content"):
            return
        path = result["path"]
        end = result["partial_line"] - 1 if result.get("partial_line") else result["end_line"]
        text = data.decode("utf-8-sig")
        reference = self.repository.render_lines(
            path, text, result["start_line"], result["end_line"]
        )
        content = result["content"]
        if not content or not reference["content"].startswith(content):
            return
        if content != reference["content"]:
            # Only complete lines count when the serialized tool budget cuts the result.
            end = min(end, result["start_line"] + content.count("\n") - 1)
        lines = text.split("\n")
        start_offset = sum(len(line) + 1 for line in lines[: result["start_line"] - 1])
        end_offset = min(len(text), sum(len(line) + 1 for line in lines[: max(0, end)]))
        previous = self.observed.get(path)
        ranges = previous[1] if previous and previous[0] == data else []
        self.observed[path] = (data, [*ranges, (start_offset, end_offset)])

    def propose(self, path: str, old_text: str, new_text: str, reason: str) -> dict:
        for name, value, limit in (
            ("path", path, 2000),
            ("old_text", old_text, 3000),
            ("new_text", new_text, 3000),
            ("reason", reason, 500),
        ):
            if (
                not isinstance(value, str)
                or len(value) > limit
                or (not value.strip() and name != "new_text")
            ):
                raise ValueError(f"{name} inválido.")
        if len(self.pending) >= 8:
            raise ValueError("Limite de oito propostas pendentes atingido.")
        target = self.repository.resolve_file(path)
        path = target.relative_to(self.repository.root).as_posix()
        data = self.repository.read_bytes(target)
        observation = self.observed.get(path)
        if not observation or observation[0] != data:
            raise ValueError("Leia novamente o trecho atual antes de propor uma edição.")
        text = data.decode("utf-8-sig")
        # Match LF text from tool output against CRLF files without changing their style.
        newline = "\r\n" if "\r\n" in text else "\n"
        old = old_text.replace("\r\n", "\n").replace("\n", newline)
        new = new_text.replace("\r\n", "\n").replace("\n", newline)
        if not old or text.count(old) != 1:
            raise ValueError("O trecho original deve ocorrer exatamente uma vez no arquivo.")
        offset = text.index(old)
        if not any(start <= offset and offset + len(old) <= end for start, end in observation[1]):
            raise ValueError(
                "O trecho precisa estar inteiramente nas linhas já lidas, sem truncamento."
            )
        if old == new:
            raise ValueError("A proposta não altera o conteúdo.")
        if any(proposal.path == path for proposal in self.pending):
            raise ValueError("Já há uma proposta pendente para esse arquivo; reúna as alterações.")
        updated = text[:offset] + new + text[offset + len(old) :]
        after = (b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b"") + updated.encode(
            "utf-8"
        )
        if len(after) > MAX_FILE_BYTES or b"\0" in after:
            raise ValueError("Conteúdo proposto inválido ou maior que 512 KB.")
        before_lines = text.splitlines(keepends=True)
        after_lines = updated.splitlines(keepends=True)
        diff = ""
        for line in difflib.unified_diff(before_lines, after_lines, f"a/{path}", f"b/{path}"):
            diff += line
            if not line.endswith("\n"):
                diff += "\n\\ No newline at end of file\n"
        if len(diff) > 60_000:
            raise ValueError("Diff grande demais. Reduza o trecho alterado.")
        identifier = secrets.token_hex(6)
        self.proposals[identifier] = EditProposal(identifier, path, reason, data, after, diff)
        return {
            "proposal_id": identifier,
            "path": path,
            "state": "pending",
            "message": "Diff preparado. Aguarda aprovação humana; nenhum arquivo foi alterado.",
        }

    def reject(self, identifier: str):
        with self._lock:
            proposal = self.proposals[identifier]
            if proposal.state != "pending":
                raise ValueError("A proposta já foi resolvida.")
            proposal.state = "rejected"

    def apply(self, identifier: str):
        with self._lock:
            proposal = self.proposals[identifier]
            if proposal.state != "pending":
                raise ValueError("A proposta já foi resolvida.")
            try:
                proposal.checkpoint_id = self.checkpoints.prepare(proposal)
                self._apply(proposal)
            except (ValueError, OSError):
                proposal.state = "conflict"
                if proposal.checkpoint_id:
                    try:
                        self.checkpoints.mark(proposal.checkpoint_id, "failed")
                    except (ValueError, OSError):
                        pass
                raise
            proposal.state = "applied"
            try:
                self.checkpoints.mark(proposal.checkpoint_id, "applied")
                if proposal.undo_of:
                    self.checkpoints.mark(proposal.undo_of, "undone")
            except (ValueError, OSError):
                proposal.checkpoint_warning = (
                    "Edição aplicada; status do checkpoint não atualizado. Confira /changes."
                )

    def propose_undo(self, identifier: str | None = None):
        if self.pending:
            raise ValueError("Revise as propostas pendentes antes de desfazer.")
        proposal = self.checkpoints.proposal(identifier)
        self.proposals[proposal.id] = proposal
        return proposal

    def _apply(self, proposal: EditProposal):
        if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
            raise RepositoryError("Aplicação segura requer POSIX com O_NOFOLLOW e dir_fd.")
        target = self.repository.resolve_file(proposal.path)
        if self.repository.read_bytes(target) != proposal.before:
            raise RepositoryError("Arquivo mudou após a proposta. Nenhuma mudança foi aplicada.")
        relative = Path(proposal.path)
        directory = os.open(self.repository.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        temporary = None
        source = None
        try:
            for component in relative.parts[:-1]:
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = child
            source = os.open(
                relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
            info = os.fstat(source)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RepositoryError("Arquivo deve ser regular e não pode ter hard links.")
            # Read from the descriptor that belongs to the destination we will replace.
            with os.fdopen(os.dup(source), "rb") as stream:
                if stream.read(MAX_FILE_BYTES + 1) != proposal.before:
                    raise RepositoryError("Arquivo mudou após a proposta.")
            temporary = f".codaro-edit-{secrets.token_hex(12)}"
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(proposal.after)
                stream.flush()
                os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode) & 0o777)
                os.fsync(stream.fileno())
            current = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
            os.lseek(source, 0, os.SEEK_SET)
            with os.fdopen(os.dup(source), "rb") as stream:
                unchanged = stream.read(MAX_FILE_BYTES + 1) == proposal.before
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino) or not unchanged:
                raise RepositoryError("Arquivo mudou durante a aplicação; proposta bloqueada.")
            os.replace(temporary, relative.name, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
        finally:
            if source is not None:
                os.close(source)
            if temporary is not None:
                os.unlink(temporary, dir_fd=directory)
            os.close(directory)
