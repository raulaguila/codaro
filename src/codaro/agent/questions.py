"""Optional interactive questions; a response never creates an approval grant."""

import time

from codaro.tool_registry import Tool, definition


def register_questions(agent, callback):
    existing = agent.registry.tools.get("ask_user_question")
    if existing is not None:
        if existing.source != "interaction":
            raise ValueError("Nome de ferramenta de interação já ocupado.")
        del agent.registry.tools["ask_user_question"]

    def ask(arguments):
        recommended = arguments.get("recommended")
        if recommended is not None and recommended >= len(arguments["choices"]):
            raise ValueError("Opção recomendada não existe.")
        started = time.monotonic()
        try:
            return callback(arguments, agent._cancelled)
        finally:
            agent._deadline += time.monotonic() - started

    agent.registry.register(
        Tool(
            definition(
                "ask_user_question",
                "Pergunta uma decisão necessária ao usuário, com até três sugestões "
                "e resposta livre. "
                "Aguarde a resposta real. Não use para conceder permissões de edição/comandos.",
                {
                    "question": {"type": "string", "minLength": 1, "maxLength": 300},
                    "explanation": {"type": "string", "maxLength": 500},
                    "choices": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 200,
                        },
                    },
                    "recommended": {"type": "integer", "minimum": 0, "maximum": 2},
                },
                ["question", "choices"],
            ),
            source="interaction",
            handler=ask,
        )
    )
