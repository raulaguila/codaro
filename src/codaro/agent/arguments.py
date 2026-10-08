from __future__ import annotations

import re

from codaro.agent.schemas import ALL_DEFINITIONS
from codaro.commands import validate_command
from codaro.tasks import TaskStore


def validate_arguments(name: str, args: dict):
    definition = next(
        (tool["function"] for tool in ALL_DEFINITIONS if tool["function"]["name"] == name),
        None,
    )
    if not definition:
        raise ValueError("Ferramenta desconhecida.")
    parameters = definition["parameters"]
    if set(args) - parameters["properties"].keys():
        raise ValueError("Argumentos desconhecidos.")
    if set(parameters["required"]) - args.keys():
        raise ValueError("Argumentos obrigatórios ausentes.")
    for key, value in args.items():
        spec = parameters["properties"][key]
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{key} fora das opções permitidas.")
        if spec["type"] == "string":
            if not isinstance(value, str) or (not value.strip() and key != "new_text"):
                raise ValueError(f"{key} deve ser texto não vazio.")
            if len(value) > spec["maxLength"]:
                raise ValueError(f"{key} excede o limite permitido.")
        if spec["type"] == "integer":
            # Some local models emit decimal integer strings despite the numeric schema.
            # Normalize only canonical, bounded values; no expression evaluation or floats.
            if isinstance(value, str) and re.fullmatch(r"-?(0|[1-9][0-9]{0,11})", value):
                value = args[key] = int(value)
            if type(value) is not int:
                raise ValueError(f"{key} deve ser inteiro.")
            if value < spec.get("minimum", value) or value > spec.get("maximum", value):
                raise ValueError(f"{key} fora dos limites.")

    if name == "run_command":
        validate_command(args["argv"], args.get("timeout", 60))
    if name == "update_plan":
        TaskStore.validate_plan(args["steps"], args["criteria"])
        if "validation_commands" in args:
            TaskStore.validate_checks(args["validation_commands"])
    if name == "apply_changes":
        operations = args["operations"]
        if not isinstance(operations, list) or not 1 <= len(operations) <= 8:
            raise ValueError("Use de uma a oito operações.")
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("Operação inválida.")
            for key, value in operation.items():
                if not isinstance(value, str) or len(value) > (12000 if key == "content" else 3000):
                    raise ValueError("Campo da operação inválido ou grande demais.")
    if name == "request_tools":
        names = args["names"]
        if (
            not isinstance(names, list)
            or not 1 <= len(names) <= 8
            or not all(isinstance(name, str) and 1 <= len(name) <= 80 for name in names)
        ):
            raise ValueError("Solicite de uma a oito ferramentas por nome.")
