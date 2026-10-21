"""Validated user preferences, separate from credentials and project permissions."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from codaro.policies import Mode
from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write


@dataclass(frozen=True)
class Preferences:
    mode: str = "execute"
    resume: bool = False
    max_steps: int | None = None
    max_seconds: int = 1800
    max_corrections: int = 3
    timeout: float = 90.0

    def __post_init__(self):
        Mode(self.mode)
        if type(self.resume) is not bool:
            raise ValueError("Retomar conversa deve ser true ou false.")
        for name, low, high in (
            ("max_steps", 1, 200),
            ("max_seconds", 1, 7200),
            ("max_corrections", 1, 10),
        ):
            value = getattr(self, name)
            if name == "max_steps" and value is None:
                continue
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} deve estar entre {low} e {high}.")
        if type(self.timeout) not in (float, int) or not 1 <= self.timeout <= 300:
            raise ValueError("Timeout deve ficar entre 1 e 300 segundos.")


class PreferencesStore:
    def __init__(self, directory=None):
        parent = Path(os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")).expanduser()
        if directory is None and not parent.is_absolute():
            raise ValueError("XDG_CONFIG_HOME deve ser um caminho absoluto.")
        self.directory = Path(directory) if directory is not None else parent / "codaro"
        self.path = self.directory / "preferences.json"

    def load(self):
        try:
            data = private_json(self.path, 16_384)
        except FileNotFoundError:
            return Preferences()
        except ValueError as exc:
            raise ValueError(
                "Preferências inválidas. Use codaro config reset para restaurar."
            ) from exc
        keys = {field.name for field in fields(Preferences)}
        if (
            not isinstance(data, dict)
            or data.get("version") != 1
            or not isinstance(data.get("preferences"), dict)
            or data["preferences"].keys() - keys
        ):
            raise ValueError("Preferências inválidas. Use codaro config reset para restaurar.")
        try:
            return Preferences(**data["preferences"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Preferências inválidas. Use codaro config reset para restaurar."
            ) from exc

    def save(self, preferences):
        self.update(asdict(preferences))

    def update(self, changes):
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        with private_lock(self.directory / "preferences.lock"):
            current = asdict(self.load())
            preferences = Preferences(**{**current, **changes})
            atomic_write(
                self.path, json.dumps({"version": 1, "preferences": asdict(preferences)}).encode()
            )
        return preferences

    def reset(self):
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        with private_lock(self.directory / "preferences.lock"):
            atomic_write(
                self.path, json.dumps({"version": 1, "preferences": asdict(Preferences())}).encode()
            )


def preference_value(name, value):
    if name not in {field.name for field in fields(Preferences)}:
        raise ValueError("Preferência desconhecida: " + name)
    if name == "mode":
        return Mode(value).value
    if name == "resume":
        if value.lower() not in {"true", "false"}:
            raise ValueError("Use true ou false para resume.")
        return value.lower() == "true"
    if name == "max_steps" and value.lower() in {"auto", "automatic"}:
        return None
    try:
        return float(value) if name == "timeout" else int(value)
    except ValueError as exc:
        raise ValueError("Informe um número válido para " + name) from exc
