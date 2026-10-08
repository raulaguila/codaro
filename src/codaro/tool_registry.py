"""Contracts shared by internal and external tools; discovery never grants access."""

import json
import math
import re
from dataclasses import dataclass


def definition(name, description, properties, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


@dataclass
class Tool:
    schema: dict
    source: str = "builtin"
    read_only: bool = True
    lazy: bool = False
    handler: object = None

    @property
    def name(self):
        return self.schema["function"]["name"]


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        if (
            not re.fullmatch(r"[A-Za-z_][\w-]{0,79}", tool.name)
            or tool.name in self.tools
            or len(self.tools) >= 128
        ):
            raise ValueError("Nome de ferramenta inválido, duplicado ou catálogo cheio.")
        if len(json.dumps(tool.schema)) > 32_000:
            raise ValueError("Schema excede 32 KB.")
        self.tools[tool.name] = tool

    def authorize(self, name, mode, advertised, *, closed=False):
        tool = self.tools.get(name)
        if tool is None or name not in advertised:
            raise ValueError("Ferramenta não anunciada nesta chamada.")
        if not tool.read_only and (mode != "execute" or closed):
            raise ValueError("Mutação não permitida neste modo ou tarefa finalizada.")
        return tool

    def validate(self, name, args):
        if name not in self.tools:
            raise ValueError("Ferramenta desconhecida.")
        if len(json.dumps(args, allow_nan=False)) > 64_000:
            raise ValueError("Argumentos excedem o limite.")
        validate_schema(self.tools[name].schema["function"]["parameters"], args)


def check_schema(spec, depth=0):
    """Supported JSON Schema subset, rejected at discovery rather than weakened."""
    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "enum",
        "title",
        "description",
        "default",
        "$schema",
    }
    if depth > 16 or not isinstance(spec, dict) or set(spec) - allowed:
        raise ValueError("Schema externo usa recursos não suportados.")
    if spec.get("type") not in {
        None,
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
        "null",
    }:
        raise ValueError("Tipo externo não suportado.")
    props = spec.get("properties", {})
    if not isinstance(props, dict) or len(props) > 100:
        raise ValueError("Propriedades inválidas.")
    required = spec.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(key, str) and key in props for key in required
    ):
        raise ValueError("Argumentos obrigatórios inválidos.")
    if "enum" in spec and (not isinstance(spec["enum"], list) or not 1 <= len(spec["enum"]) <= 100):
        raise ValueError("Enum inválido.")
    for key in ("minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum"):
        if key in spec and (type(spec[key]) not in (int, float) or not math.isfinite(spec[key])):
            raise ValueError("Limite do schema inválido.")
    for child in props.values():
        check_schema(child, depth + 1)
    for key in ("items", "additionalProperties"):
        if key in spec:
            if key == "additionalProperties" and type(spec[key]) is bool:
                continue
            check_schema(spec[key], depth + 1)


def validate_schema(spec, value, depth=0):
    """Fail closed on unsupported constructs, including remote references."""
    if depth > 16 or not isinstance(spec, dict):
        raise ValueError("Schema inválido ou profundo demais.")
    if set(spec) & {"$ref", "oneOf", "anyOf", "allOf", "not", "if", "then", "else"}:
        raise ValueError("Schema complexo não suportado; ferramenta não executada.")
    kind = spec.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    if kind and (
        not isinstance(kind, str)
        or kind not in types
        or not isinstance(value, types[kind])
        or kind in {"integer", "number"}
        and isinstance(value, bool)
    ):
        raise ValueError("Argumento incompatível com o schema.")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("Argumento fora das opções.")
    if isinstance(value, dict):
        properties = spec.get("properties", {})
        if set(spec.get("required", [])) - value.keys():
            raise ValueError("Argumentos obrigatórios ausentes.")
        if spec.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError("Argumentos desconhecidos.")
        for key, item in value.items():
            child = properties.get(key, spec.get("additionalProperties", {}))
            if isinstance(child, dict):
                validate_schema(child, item, depth + 1)
    if isinstance(value, list):
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", 1000):
            raise ValueError("Quantidade de itens fora dos limites.")
        for item in value:
            validate_schema(spec.get("items", {}), item, depth + 1)
    if isinstance(value, str):
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", 64_000):
            raise ValueError("Texto fora dos limites.")
    if type(value) in (int, float):
        if not spec.get("minimum", value) <= value <= spec.get("maximum", value):
            raise ValueError("Número fora dos limites.")
