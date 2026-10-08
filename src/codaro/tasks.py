"""Bounded task journal, separate from conversation and from permission grants."""

import copy
import json
import re
import uuid
from contextlib import nullcontext

from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write, timestamp

STATES = {
    "investigating",
    "planning",
    "executing",
    "validating",
    "awaiting_approval",
    "blocked",
    "completed",
    "cancelled",
    "planned",
}


class TaskStore:
    def __init__(self, root, *, ephemeral=False, redact=lambda value: value):
        self.root = root
        self.path = root / ".codaro/tasks.json"
        self.ephemeral = ephemeral
        self.redact = redact
        self._data = {"version": 1, "root": str(root), "active": None, "tasks": []}

    def load(self):
        if self.ephemeral:
            return copy.deepcopy(self._data)
        try:
            data = private_json(self.path, 4_000_000)
        except FileNotFoundError:
            return copy.deepcopy(self._data)
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or data.get("root") != str(self.root)
            or not isinstance(data.get("tasks"), list)
            or len(data["tasks"]) > 20
        ):
            raise ValueError("Arquivo de tarefas incompatível.")
        ids = set()
        for task in data["tasks"]:
            if (
                not isinstance(task, dict)
                or not isinstance(task.get("id"), str)
                or len(task["id"]) != 12
                or task["id"] in ids
                or task.get("state") not in STATES
                or not isinstance(task.get("objective"), str)
                or len(task["objective"]) > 8000
                or not isinstance(task.get("plan"), list)
                or len(task["plan"]) > 24
                or not isinstance(task.get("events"), list)
                or len(task["events"]) > 200
                or type(task.get("requires_changes", False)) is not bool
                or type(task.get("revision")) is not int
                or task["revision"] < 0
                or type(task.get("plan_revision")) is not int
                or task["plan_revision"] < 0
                or not isinstance(task.get("summary"), str)
                or len(task["summary"]) > 2000
                or not isinstance(task.get("created"), str)
                or len(task["created"]) > 80
                or not isinstance(task.get("validations"), list)
                or len(task["validations"]) > 32
            ):
                raise ValueError("Tarefa persistida inválida.")
            ids.add(task["id"])
            self.validate_plan(task["plan"], task.get("criteria", []))
            if "validation_commands" in task:
                self.validate_checks(task["validation_commands"])
            if task.get("workspace_digest") is not None and not re.fullmatch(
                r"[0-9a-f]{64}", str(task["workspace_digest"])
            ):
                raise ValueError("Digest da tarefa inválido.")
            for key in ("initial_digest", "verified_no_change_digest"):
                if task.get(key) is not None and not re.fullmatch(r"[0-9a-f]{64}", str(task[key])):
                    raise ValueError("Digest inicial/verificado inválido.")
            from codaro.commands import validate_command

            for result in task["validations"]:
                if (
                    not isinstance(result, dict)
                    or type(result.get("exit_code")) is not int
                    or type(result.get("timed_out")) is not bool
                    or type(result.get("revision")) is not int
                    or not 0 <= result["revision"] <= task["revision"]
                ):
                    raise ValueError("Validação persistida inválida.")
                validate_command(result.get("argv"), 60)
            for event in task["events"]:
                if (
                    not isinstance(event, dict)
                    or not isinstance(event.get("kind"), str)
                    or not isinstance(event.get("id"), str)
                    or len(event["id"]) != 12
                    or len(json.dumps(event)) > 60_000
                ):
                    raise ValueError("Evento persistido inválido.")
        if data.get("active") is not None and data["active"] not in ids:
            raise ValueError("Tarefa ativa inválida.")
        return data

    def mutate(self, function):
        lock = nullcontext() if self.ephemeral else private_lock(self.path.with_suffix(".lock"))
        with lock:
            data = self.load()
            result = function(data)
            data["tasks"] = data["tasks"][-20:]
            data = self.redact(data)
            encoded = json.dumps(data, ensure_ascii=False).encode()
            while len(encoded) > 4_000_000 and len(data["tasks"]) > 1:
                data["tasks"].pop(0)
                encoded = json.dumps(data, ensure_ascii=False).encode()
            if len(encoded) > 4_000_000:
                raise ValueError("Limite de armazenamento da tarefa atingido.")
            if self.ephemeral:
                self._data = data
            else:
                atomic_write(self.path, encoded)
            return copy.deepcopy(result)

    def current(self):
        data = self.load()
        return next((item for item in data["tasks"] if item["id"] == data["active"]), None)

    def select(self, identifier):
        def change(data):
            task = next((item for item in data["tasks"] if item["id"] == identifier), None)
            if task is None:
                raise ValueError("Tarefa não encontrada neste projeto.")
            data["active"] = identifier
            task["state"] = "blocked"
            task["summary"] = "Retomada solicitada; conferir arquivos e operações interrompidas."
            return task

        return self.mutate(change)

    def start(self, objective, *, new=False):
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 8000:
            raise ValueError("Objetivo deve conter de 1 a 8.000 caracteres.")

        def change(data):
            current = next((item for item in data["tasks"] if item["id"] == data["active"]), None)
            if current and not new and current["state"] not in {"completed", "cancelled"}:
                return current
            task = {
                "id": uuid.uuid4().hex[:12],
                "objective": objective,
                "created": timestamp(),
                "state": "investigating",
                "plan": [],
                "plan_revision": 0,
                "criteria": [],
                "requires_changes": bool(
                    re.search(
                        r"\b(altere|alterar|implemente|implementar|crie|criar|corrija|corrigir|"
                        r"substitua|refatore|ajuste|ajustar|mude|adicione|adicionar|atualize|"
                        r"implement|create|fix|change|refactor|update|add)\b",
                        objective.casefold(),
                    )
                )
                and not bool(re.match(r"(?:como|explique|how|explain)\b", objective.casefold())),
                "revision": 0,
                "validations": [],
                "events": [],
                "summary": "",
            }
            data["active"] = task["id"]
            data["tasks"].append(task)
            return task

        return self.mutate(change)

    def update(self, function):
        def change(data):
            task = next((item for item in data["tasks"] if item["id"] == data["active"]), None)
            if task is None:
                raise ValueError("Nenhuma tarefa ativa.")
            function(task)
            task["updated"] = timestamp()
            return task

        return self.mutate(change)

    def state(self, state, summary=None):
        if state not in STATES:
            raise ValueError("Estado de tarefa inválido.")

        def change(task):
            task["state"] = state
            if summary is not None:
                task["summary"] = summary[:2000]

        return self.update(change)

    @staticmethod
    def validate_plan(steps, criteria):
        if not isinstance(steps, list) or not 0 <= len(steps) <= 24:
            raise ValueError("Plano deve ter até 24 etapas.")
        for step in steps:
            if (
                not isinstance(step, dict)
                or set(step) != {"title", "state"}
                or not isinstance(step["title"], str)
                or not 1 <= len(step["title"]) <= 300
                or step["state"] not in {"todo", "doing", "done"}
            ):
                raise ValueError("Etapa inválida: use title e state todo/doing/done.")
        if (
            not isinstance(criteria, list)
            or len(criteria) > 16
            or not all(isinstance(item, str) and 1 <= len(item) <= 300 for item in criteria)
        ):
            raise ValueError("Critérios de aceite inválidos.")

    @staticmethod
    def validate_checks(commands):
        from codaro.commands import validate_command

        if not isinstance(commands, list) or not 1 <= len(commands) <= 16:
            raise ValueError("Defina de um a dezesseis comandos de validação.")
        for argv in commands:
            validate_command(argv, 60)

    def plan(self, steps, criteria, validation_commands=None):
        self.validate_plan(steps, criteria)
        if validation_commands is not None:
            self.validate_checks(validation_commands)

        def change(task):
            task["plan"], task["criteria"] = steps, criteria
            task["plan_revision"] += 1
            task["state"] = "planning"
            if validation_commands is not None:
                task["validation_commands"] = validation_commands

        return self.update(change)

    def event(self, kind, value):
        if not isinstance(value, dict) or len(json.dumps(value)) > 55_000:
            raise ValueError("Evento grande demais.")

        def change(task):
            item = {"id": uuid.uuid4().hex[:12], "kind": kind, "at": timestamp(), **value}
            task["events"] = [*task["events"], item][-200:]

        return self.update(change)

    def changed(self, path, checkpoint):
        def change(task):
            task["revision"] += 1
            task["state"] = "executing"

        self.update(change)
        return self.event("change", {"path": path, "checkpoint": checkpoint})

    def validation(self, result):
        def change(task):
            entry = {
                "argv": result["argv"],
                "exit_code": result["exit_code"],
                "timed_out": result["timed_out"],
                "revision": task["revision"],
                "at": timestamp(),
            }
            task["validations"] = [*task["validations"], entry][-32:]
            task["state"] = "validating"

        return self.update(change)

    def validation_ready(self):
        task = self.current()
        if (
            task
            and task.get("requires_changes")
            and not (
                task.get("workspace_digest") is not None
                and task.get("verified_no_change_digest") == task.get("workspace_digest")
            )
            and (
                task.get("initial_digest") is None
                or task.get("workspace_digest") == task.get("initial_digest")
            )
        ):
            return False
        if not task or (
            not task["revision"] and not task["validations"] and not task.get("validation_commands")
        ):
            return True
        latest = {}
        required = (
            {tuple(argv) for argv in task["validation_commands"]}
            if "validation_commands" in task
            else {tuple(item["argv"]) for item in task["validations"]}
        )
        for result in task["validations"]:
            if result["revision"] == task["revision"]:
                latest[tuple(result["argv"])] = result
        return (
            bool(latest)
            and required.issubset(latest)
            and all(
                latest[argv]["exit_code"] == 0 and not latest[argv]["timed_out"]
                for argv in required
            )
        )

    def projection(self):
        task = self.current()
        if task is None:
            return None
        return {
            "id": task["id"],
            "objective": task["objective"][:400],
            "state": task["state"],
            "plan": [
                {"title": step["title"][:100], "state": step["state"]} for step in task["plan"][:6]
            ],
            "criteria": [item[:150] for item in task["criteria"][:3]],
            "revision": task["revision"],
            "validations": [
                {
                    "argv": json.dumps(item["argv"])[:300],
                    "exit_code": item["exit_code"],
                    "revision": item["revision"],
                }
                for item in task["validations"][-2:]
            ],
            "remaining_steps": max(0, len(task["plan"]) - 6),
            "recent_operations": [
                {
                    "kind": event["kind"],
                    "detail": json.dumps(
                        {
                            key: event[key]
                            for key in ("path", "argv", "exit_code", "approved")
                            if key in event
                        }
                    )[:250],
                }
                for event in task["events"][-3:]
            ],
        }

    def page(self, offset=0, limit=2400):
        task = self.current()
        if not task:
            return {"state": "no_task"}
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 200 <= limit <= 4000
        ):
            raise ValueError("Página de tarefa inválida.")
        value = json.dumps(task, ensure_ascii=False)
        text = value[offset : offset + limit]
        end = offset + len(text)
        return {
            "task_id": task["id"],
            "text": text,
            "offset": offset,
            "next_offset": end if end < len(value) else None,
            "truncated": end < len(value),
        }
