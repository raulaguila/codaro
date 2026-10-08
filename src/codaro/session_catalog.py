"""Private session catalog. Metadata contains no credentials or permission grants."""

import json
import re
import uuid

from codaro.sessions import SessionStore
from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write, timestamp


def validate_id(identifier):
    if identifier != "default" and not re.fullmatch(r"[a-f0-9]{12}", identifier or ""):
        raise ValueError("Identificador de sessão inválido.")
    return identifier


class SessionCatalog:
    def __init__(self, root):
        self.root = root
        self.path = root / ".codaro/sessions.json"

    def load(self):
        try:
            value = private_json(self.path, 128_000)
        except FileNotFoundError:
            return {
                "version": 1,
                "active": "default",
                "items": [{"id": "default", "title": "Conversa original", "created": timestamp()}],
            }
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or not isinstance(value.get("items"), list)
            or len(value["items"]) > 30
        ):
            raise ValueError("Catálogo de sessões inválido.")
        identifiers = set()
        for item in value["items"]:
            if not isinstance(item, dict):
                raise ValueError("Sessão inválida.")
            identifier = validate_id(item.get("id"))
            if (
                identifier in identifiers
                or not isinstance(item.get("title"), str)
                or not 1 <= len(item["title"]) <= 100
            ):
                raise ValueError("Metadados de sessão inválidos.")
            identifiers.add(identifier)
        if value.get("active") not in identifiers:
            raise ValueError("Sessão ativa não encontrada.")
        return value

    def create(self, title="Nova conversa"):
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
            raise ValueError("Nome deve ter de 1 a 100 caracteres.")
        with private_lock(self.path.with_suffix(".lock")):
            value = self.load()
            if len(value["items"]) >= 30:
                raise ValueError("Limite de 30 sessões alcançado.")
            identifier = uuid.uuid4().hex[:12]
            value["items"].append(
                {"id": identifier, "title": title.strip(), "created": timestamp()}
            )
            atomic_write(self.path, json.dumps(value, ensure_ascii=False).encode())
        return identifier

    def activate(self, identifier):
        validate_id(identifier)
        with private_lock(self.path.with_suffix(".lock")):
            value = self.load()
            if not any(item["id"] == identifier for item in value["items"]):
                raise ValueError("Sessão não encontrada.")
            value["active"] = identifier
            atomic_write(self.path, json.dumps(value, ensure_ascii=False).encode())

    def store(self, identifier, secret=""):
        validate_id(identifier)
        store = SessionStore(self.root, secret)
        if identifier != "default":
            store.path = self.root / (".codaro/session-" + identifier + ".json")
        return store

    def summary_path(self, identifier):
        validate_id(identifier)
        return self.root / (".codaro/continuity-" + identifier + ".json")

    def summary(self, identifier):
        try:
            value = private_json(self.summary_path(identifier), 8000)
        except FileNotFoundError:
            return None
        from codaro.continuity import validate_summary

        validate_summary(value, 8000)
        return value
