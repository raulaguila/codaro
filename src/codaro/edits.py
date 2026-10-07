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
    before_exists: bool = True
    after_exists: bool = True
    file_mode: int = 0o644

    @property
    def operation(self):
        return (
            "Criar" if not self.before_exists else "Remover" if not self.after_exists else "Alterar"
        )


class EditManager:
    """Prepare exact diffs; callers must obtain permission before applying."""

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
            if result.get("total_lines") == 0 and data.decode("utf-8-sig") == "":
                self.observed[result["path"]] = (data, [(0, 0)])
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

    def prepare_operations(self, operations, reason):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 8:
            raise ValueError("Use de uma a oito operações por conjunto.")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 500:
            raise ValueError("Motivo inválido.")
        prepared, paths = [], set()
        try:
            for operation in operations:
                if not isinstance(operation, dict):
                    raise ValueError("Operação inválida.")
                kind = operation.get("kind")
                allowed = {"kind", "path", "old_text", "new_text", "destination", "content"}
                if set(operation) - allowed:
                    raise ValueError("Campo de operação desconhecido.")
                path = operation.get("path")
                if not isinstance(path, str) or not path or len(path) > 2000:
                    raise ValueError("Caminho inválido.")
                canonical = self.repository._relative(self.repository.root / path).as_posix()
                if canonical in paths:
                    raise ValueError("Reúna alterações de cada arquivo numa operação.")
                paths.add(canonical)
                if kind == "edit":
                    if set(operation) != {"kind", "path", "old_text", "new_text"}:
                        raise ValueError("Edit exige somente kind/path/old_text/new_text.")
                    result = self.propose(
                        path, operation.get("old_text"), operation.get("new_text"), reason
                    )
                    prepared.append(self.proposals[result["proposal_id"]])
                    continue
                if kind == "create":
                    if set(operation) != {"kind", "path", "content"}:
                        raise ValueError("Create exige somente kind/path/content.")
                    target = self.repository.resolve_destination(path)
                    if target.exists():
                        raise ValueError("Arquivo já existe; criação bloqueada.")
                    content = operation.get("content")
                    if not isinstance(content, str) or len(content) > MAX_FILE_BYTES:
                        raise ValueError("Conteúdo inválido.")
                    prepared.append(
                        self._proposal(
                            canonical, b"", content.encode(), reason, before_exists=False
                        )
                    )
                    continue
                if kind not in {"delete", "rename"}:
                    raise ValueError("Use kind edit/create/delete/rename.")
                expected = {"kind", "path", "destination"} if kind == "rename" else {"kind", "path"}
                if set(operation) != expected:
                    raise ValueError("Campos incompatíveis com a operação.")
                target = self.repository.resolve_file(path)
                data = self.repository.read_bytes(target)
                observation = self.observed.get(canonical)
                length = len(data.decode("utf-8-sig"))
                ranges = sorted(observation[1]) if observation and observation[0] == data else []
                covered = 0
                for start, end in ranges:
                    if start > covered:
                        break
                    covered = max(covered, end)
                if covered < length or not observation:
                    raise ValueError("Leia todo o arquivo atual antes de remover ou renomear.")
                mode = stat.S_IMODE(target.stat(follow_symlinks=False).st_mode) & 0o777
                if kind == "rename":
                    destination = operation.get("destination")
                    if not isinstance(destination, str) or not destination:
                        raise ValueError("Destino inválido.")
                    dest = self.repository.resolve_destination(destination)
                    name = dest.relative_to(self.repository.root).as_posix()
                    if dest.exists() or name in paths:
                        raise ValueError("Destino existente ou repetido.")
                    paths.add(name)
                    # Create first: a partial failure leaves the original available.
                    prepared.append(
                        self._proposal(name, b"", data, reason, before_exists=False, file_mode=mode)
                    )
                prepared.append(
                    self._proposal(canonical, data, b"", reason, after_exists=False, file_mode=mode)
                )
            if len(prepared) > 8:
                raise ValueError("Conjunto excede oito arquivos; divida a alteração.")
            return prepared
        except BaseException:
            for proposal in prepared:
                if proposal.state == "pending":
                    self.reject(proposal.id)
            raise

    def _proposal(self, path, before, after, reason, **kwargs):
        if len(after) > MAX_FILE_BYTES or b"\0" in after or b"\0" in before:
            raise ValueError("Arquivo binário ou conteúdo maior que 512 KB.")
        if any(item.path == path for item in self.pending):
            raise ValueError("Arquivo já tem uma proposta pendente.")
        lines = difflib.unified_diff(
            before.decode("utf-8-sig").splitlines(keepends=True),
            after.decode("utf-8-sig").splitlines(keepends=True),
            "a/" + path,
            "b/" + path,
        )
        diff = "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in lines
        )
        if len(diff) > 60_000:
            raise ValueError("Diff grande demais.")
        if not diff:
            diff = "(arquivo vazio)\n"
        proposal = EditProposal(secrets.token_hex(6), path, reason, before, after, diff, **kwargs)
        self.proposals[proposal.id] = proposal
        return proposal

    def apply(self, identifier: str):
        with self._lock:
            proposal = self.proposals[identifier]
            if proposal.state != "pending":
                raise ValueError("A proposta já foi resolvida.")
            try:
                proposal.checkpoint_id = self.checkpoints.prepare(proposal)
                self._apply(proposal)
            except (ValueError, OSError) as exc:
                if proposal.state != "applied":
                    proposal.state = "conflict"
                    if proposal.checkpoint_id:
                        try:
                            self.checkpoints.mark(proposal.checkpoint_id, "failed")
                        except (ValueError, OSError):
                            pass
                    raise
                proposal.checkpoint_warning = (
                    "Conteúdo aplicado; sincronização/limpeza posterior falhou: " + str(exc)[:200]
                )
            proposal.state = "applied"
            try:
                self.checkpoints.mark(proposal.checkpoint_id, "applied")
                if proposal.undo_of:
                    self.checkpoints.mark(proposal.undo_of, "undone")
            except (ValueError, OSError):
                proposal.checkpoint_warning += (
                    " Edição aplicada; status do checkpoint não atualizado. Confira /changes."
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
        if not proposal.before_exists or not proposal.after_exists:
            return self._apply_operation(proposal)
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
            proposal.state = "applied"
            temporary = None
        finally:
            if source is not None:
                os.close(source)
            if temporary is not None:
                os.unlink(temporary, dir_fd=directory)
            os.close(directory)

    def _apply_operation(self, proposal):
        """Pinned parent directories; creation is exclusive and deletion checks fresh bytes."""
        target = self.repository.resolve_destination(proposal.path)
        if proposal.before_exists:
            target = self.repository.resolve_file(proposal.path)
            if self.repository.read_bytes(target) != proposal.before:
                raise RepositoryError("Arquivo mudou após a proposta.")
        elif target.exists():
            raise RepositoryError("Destino já existe; criação bloqueada.")
        relative = target.relative_to(self.repository.root)
        directory = os.open(self.repository.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        temporary = None
        try:
            for component in relative.parts[:-1]:
                if not proposal.before_exists:
                    try:
                        os.mkdir(component, 0o755, dir_fd=directory)
                    except FileExistsError:
                        pass
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = child
            if not proposal.after_exists:
                descriptor = os.open(
                    relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or stream.read(MAX_FILE_BYTES + 1) != proposal.before
                    ):
                        raise RepositoryError("Arquivo mudou ou possui links.")
                    current = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                        raise RepositoryError("Arquivo mudou durante a remoção.")
                    os.unlink(relative.name, dir_fd=directory)
                    proposal.state = "applied"
            else:
                temporary = ".codaro-edit-" + secrets.token_hex(12)
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(proposal.after)
                    stream.flush()
                    os.fchmod(stream.fileno(), proposal.file_mode & 0o777)
                    os.fsync(stream.fileno())
                os.link(
                    temporary,
                    relative.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                proposal.state = "applied"
                os.unlink(temporary, dir_fd=directory)
                temporary = None
            os.fsync(directory)
        finally:
            if temporary:
                os.unlink(temporary, dir_fd=directory)
            os.close(directory)
