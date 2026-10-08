"""Example plugin: register explicitly; Codaro never scans/imports this automatically."""


def somar(arguments):
    return {"resultado": arguments["a"] + arguments["b"]}


def register():
    return {
        "api_version": 1,
        "tools": [
            {
                "name": "somar",
                "description": "Soma dois inteiros, sem alterar arquivos ou serviços.",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
                "handler": somar,
            }
        ],
    }
