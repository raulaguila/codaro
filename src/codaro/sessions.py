"""Bounded private storage of the last conversation in each project."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from codaro.provider import validate_message
from codaro.trace import atomic_write, timestamp

MAX_SESSION_BYTES = 512_000


def validate_turns(turns):
    if not isinstance(turns, list) or len(turns) > 50:
        raise ValueError("Histórico de sessão inválido.")
    for turn in turns:
        if (
            not isinstance(turn, list)
            or not 2 <= len(turn) <= 200
            or not all(isinstance(item, dict) for item in turn)
            or turn[0].get("role") != "user"
            or not isinstance(turn[0].get("content"), str)
            or not 1 <= len(turn[0]["content"]) <= 8000
            or turn[-1].get("role") != "assistant"
            or turn[-1].get("tool_calls")
        ):
            raise ValueError("Turno de sessão inválido.")
        pending = set()
        for message in turn[1:]:
            if message.get("role") == "assistant":
                if pending:
                    raise ValueError("Resultados de ferramentas ausentes na sessão.")
                candidate = message
                content = message.get("content")
                if not message.get("tool_calls") and isinstance(content, str):
                    if len(content) > 64_000:
                        raise ValueError("Resposta da sessão excede o limite.")
                    # Local review annotations may extend a validated model answer.
                    candidate = {**message, "content": content[:16_000]}
                validated = validate_message(candidate)
                pending = {call["id"] for call in validated.get("tool_calls", [])}
            elif message.get("role") == "tool":
                identifier = message.get("tool_call_id")
                if (
                    not isinstance(identifier, str)
                    or identifier not in pending
                    or not isinstance(message.get("content"), str)
                ):
                    raise ValueError("Resultado de ferramenta inválido na sessão.")
                pending.remove(identifier)
            else:
                raise ValueError("Papel de mensagem inválido na sessão.")
        if pending:
            raise ValueError("Sessão incompleta.")


class SessionStore:
    def __init__(self, root: Path, secret: str = ""):
        self.root = root
        self.path = root / ".codaro" / "session.json"
        self.secret = secret

    def save(self, turns: list[list[dict]], model: str):
        retained = turns[-50:]
        validate_turns(retained)
        while True:
            value = {
                "schema_version": 1,
                "repository_root": str(self.root),
                "updated_at": timestamp(),
                "model": model,
                "turns": retained,
            }
            data = json.dumps(self.redact(value), ensure_ascii=False)
            encoded = data.encode("utf-8")
            if len(encoded) <= MAX_SESSION_BYTES:
                atomic_write(self.path, encoded)
                return
            if not retained:
                raise ValueError("Sessão excede o limite de armazenamento.")
            retained = retained[1:]

    def redact(self, value):
        forms = {self.secret}
        for _ in range(2):
            forms.update(
                json.dumps(item, ensure_ascii=ascii_only)[1:-1]
                for item in list(forms)
                for ascii_only in (False, True)
            )
        variants = sorted(forms - {""}, key=len, reverse=True)

        def clean(item):
            if isinstance(item, str):
                for secret in variants:
                    item = item.replace(secret, "[REDACTED]")
                return item
            if isinstance(item, list):
                return [clean(entry) for entry in item]
            if isinstance(item, dict):
                return {key: clean(entry) for key, entry in item.items()}
            return item

        return clean(value)

    def load(self) -> list[list[dict]]:
        if self.path.parent.is_symlink():
            raise ValueError("A sessão não pode usar links simbólicos.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        directory_fd = None
        try:
            if os.name == "posix":
                directory_fd = os.open(self.path.parent, flags | os.O_DIRECTORY)
                fd = os.open(self.path.name, flags, dir_fd=directory_fd)
            else:
                if self.path.is_symlink():
                    raise ValueError("A sessão não pode usar links simbólicos.")
                fd = os.open(self.path, flags)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("Sessão deve ser um arquivo regular sem links.")
                raw = stream.read(MAX_SESSION_BYTES + 1)
            if len(raw) > MAX_SESSION_BYTES:
                raise ValueError("Sessão excede o limite de armazenamento.")
            data = json.loads(raw)
            if (
                not isinstance(data, dict)
                or data.get("schema_version") != 1
                or data.get("repository_root") != str(self.root)
            ):
                raise ValueError("Sessão incompatível com este projeto.")
            validate_turns(data.get("turns"))
            return data["turns"]
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Arquivo de sessão inválido.") from exc
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
