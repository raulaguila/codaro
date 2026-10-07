"""Modes and explicit, session-local grants. Model output never creates a grant."""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class Mode(StrEnum):
    ASK = "ask"
    PLAN = "plan"
    EXECUTE = "execute"

    @property
    def label(self):
        return {self.ASK: "Perguntar", self.PLAN: "Planejar", self.EXECUTE: "Executar"}[self]


@dataclass
class ApprovalPolicy:
    kind: str = "action"
    task_id: str = ""
    paths: tuple[str, ...] = ()
    commands: tuple[tuple[str, ...], ...] = ()

    def grant(self, task_id, paths, commands):
        if not task_id or not isinstance(paths, list) or not isinstance(commands, list):
            raise ValueError("Escopo de autorização inválido.")
        normalized = []
        for path in paths:
            if not isinstance(path, str) or not path or len(path) > 2000:
                raise ValueError("Caminho de autorização inválido.")
            item = PurePosixPath(path)
            if item.is_absolute() or ".." in item.parts or path == ".":
                raise ValueError("Autorize caminhos relativos específicos, sem '..'.")
            if any(ord(char) < 32 for char in path):
                raise ValueError("Caminho de autorização inválido.")
            normalized.append(item.as_posix())
        from codaro.commands import validate_command

        for argv in commands:
            validate_command(argv, 60)
        if len(paths) > 32 or len(commands) > 32 or len(json.dumps([paths, commands])) > 16_000:
            raise ValueError("Escopo limitado a 32 caminhos e comandos.")
        self.kind, self.task_id = "task", task_id
        self.paths = tuple(normalized)
        self.commands = tuple(tuple(argv) for argv in commands)

    def reset(self):
        self.kind, self.task_id, self.paths, self.commands = "action", "", (), ()

    def permits_paths(self, task_id, paths):
        return (
            self.kind == "task"
            and self.task_id == task_id
            and bool(paths)
            and all(
                any(path == prefix or path.startswith(prefix + "/") for prefix in self.paths)
                for path in paths
            )
        )

    def permits_command(self, task_id, argv):
        return self.kind == "task" and self.task_id == task_id and tuple(argv) in self.commands
