"""Explicit project configuration; executable integrations require trust."""

import copy
import json
import re

from codaro.storage import private_json, private_lock
from codaro.trace import atomic_write

DEFAULTS = {
    "version": 1,
    "artifacts": True,
    "semantic_compaction": False,
    "exploration": False,
    "context_reserve_ratio": 0.12,
    "recent_ratio": 0.25,
    "max_run_requests": 64,
    "max_run_tokens": 1_000_000,
    "mcp": {},
    "plugins": {},
    "lsp": {"enabled": False, "servers": {}},
}


class FeatureStore:
    def __init__(self, root):
        self.path = root / ".codaro/features.json"

    def load(self):
        try:
            saved = private_json(self.path, 256_000, require_private=True)
        except FileNotFoundError:
            return copy.deepcopy(DEFAULTS)
        self.validate(saved)
        return {**copy.deepcopy(DEFAULTS), **saved}

    @staticmethod
    def validate(data):
        if not isinstance(data, dict) or set(data) - DEFAULTS.keys() or data.get("version") != 1:
            raise ValueError("Configuração de funcionalidades inválida.")
        for key in ("artifacts", "semantic_compaction", "exploration"):
            if type(data.get(key, DEFAULTS[key])) is not bool:
                raise ValueError(f"{key} deve ser booleano.")
        for key in ("context_reserve_ratio", "recent_ratio"):
            value = data.get(key, DEFAULTS[key])
            if type(value) not in (int, float) or not 0.05 <= value <= 0.4:
                raise ValueError(f"{key} deve ficar entre 0.05 e 0.4.")
        for key, minimum, maximum in (
            ("max_run_requests", 4, 256),
            ("max_run_tokens", 16_384, 10_000_000),
        ):
            value = data.get(key, DEFAULTS[key])
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError("Orçamento global inválido.")
        for key in ("mcp", "plugins"):
            items = data.get(key, {})
            if not isinstance(items, dict) or len(items) > 12:
                raise ValueError("Até doze integrações por tipo.")
            for name, item in items.items():
                if (
                    not isinstance(name, str)
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,29}", name)
                    or len(name) > 30
                    or not isinstance(item, dict)
                    or type(item.get("enabled", False)) is not bool
                    or item.get("trusted") is not True
                ):
                    raise ValueError("Integração precisa de nome válido e confiança explícita.")
                readonly = item.get("read_only_tools", [])
                if not isinstance(readonly, list) or not all(
                    isinstance(value, str) and len(value) <= 80 for value in readonly
                ):
                    raise ValueError("Lista de ferramentas somente leitura inválida.")
                from codaro.integrations import validate_config

                validate_config(key, item)
        lsp = data.get("lsp", DEFAULTS["lsp"])
        if (
            not isinstance(lsp, dict)
            or type(lsp.get("enabled", False)) is not bool
            or not isinstance(lsp.get("servers", {}), dict)
        ):
            raise ValueError("Configuração LSP inválida.")
        if set(lsp.get("servers", {})) - {"python", "typescript"}:
            raise ValueError("Servidores LSP suportados: python e typescript.")
        for argv in lsp.get("servers", {}).values():
            if (
                not isinstance(argv, list)
                or not 1 <= len(argv) <= 32
                or not all(
                    isinstance(item, str) and 0 < len(item) <= 4096 and "\0" not in item
                    for item in argv
                )
            ):
                raise ValueError("Comando LSP deve ser lista de argumentos.")

    def save(self, data):
        self.validate(data)
        raw = json.dumps(data, ensure_ascii=False).encode()
        if len(raw) > 256_000:
            raise ValueError("Configuração excede 256 KB.")
        with private_lock(self.path.with_suffix(".lock")):
            atomic_write(self.path, raw)

    def toggle(self, feature, enabled):
        if feature not in {"artifacts", "semantic_compaction", "exploration", "lsp"}:
            raise ValueError("Funcionalidade desconhecida.")
        data = self.load()
        if feature == "lsp":
            data["lsp"]["enabled"] = enabled
        else:
            data[feature] = enabled
        self.save(data)
        return data
