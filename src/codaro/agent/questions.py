"""Optional interactive questions; a response never creates an approval grant."""

import time

from codaro.agent.progress import Phase, waiting
from codaro.intent import normalized
from codaro.tool_registry import Tool, definition
from codaro.trace import current_flow


def register_questions(agent, callback):
    existing = agent.registry.tools.get("ask_user_question")
    if existing is not None:
        if existing.source != "interaction":
            raise ValueError("Nome de ferramenta de interação já ocupado.")
        del agent.registry.tools["ask_user_question"]

    def ask(arguments):
        flow = current_flow.get()
        if flow is not None and (
            normalized(arguments["question"]).rstrip(".!? ")
            == normalized(flow.data["user_question"]).rstrip(".!? ")
        ):
            raise ValueError(
                "Formule uma pergunta para o usuário; question está repetindo o pedido recebido. "
                "Use choices para sugerir respostas à pergunta, não para substituir seu enunciado."
            )
        recommended = arguments.get("recommended")
        if recommended is not None and recommended >= len(arguments["choices"]):
            raise ValueError("Opção recomendada não existe.")
        started = time.monotonic()
        try:
            with waiting(Phase.USER):
                result = callback(arguments, agent._cancelled)
            answer = result.get("user_response") if isinstance(result, dict) else result
            if isinstance(answer, str) and answer.strip():
                model = getattr(getattr(agent.provider, "settings", None), "model", "")
                agent.conversation.confirm(arguments["question"], answer, model)
                agent.conversation.index_decisions(agent.memory, model)
            return result
        finally:
            agent._deadline += time.monotonic() - started

    agent.registry.register(
        Tool(
            definition(
                "ask_user_question",
                "Pergunta uma decisão necessária ao usuário, com até três sugestões "
                "e resposta livre. "
                "question é a pergunta formulada por você, não uma cópia do pedido do usuário. "
                "choices contém respostas possíveis para essa pergunta. "
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
