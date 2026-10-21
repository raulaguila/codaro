"""Shared request hints for routing and completion checks, never authorization."""

import re
import unicodedata

_CHANGE = (
    r"altere|alterar|implemente|implementar|crie|criar|corrija|corrigir|conserte|consertar|"
    r"substitua|substituir|refatore|refatorar|ajuste|ajustar|mude|mudar|adicione|adicionar|"
    r"atualize|atualizar|remova|remover|apague|apagar|exclua|excluir|renomeie|renomear|"
    r"otimize|otimizar|simplifique|simplificar|migre|migrar|edite|editar|aplique|aplicar|"
    r"implement|create|fix|repair|change|modify|refactor|update|add|remove|delete|"
    r"rename|optimize|simplify|migrate|edit|apply"
)
_ACTION = _CHANGE + (
    r"|execute|executar|continue|continuar|valide|validar|teste|testar|run|validate|test|faca"
)
_CONSULTATION = (
    r"(?:explique|descreva|fale sobre|o que|qual|quais|quem|como|onde|"
    r"explain|describe|what|which|who|how|where)\b"
)


def normalized(text):
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text.strip().casefold())
        if not unicodedata.combining(char)
    )


def requests_changes(question):
    text = normalized(question)
    if not re.search(r"\b(?:" + _CHANGE + r")\b", text):
        return False
    if not re.match(_CONSULTATION, text):
        return True
    # A consultation may contain a separate imperative: "Como funciona? Corrija...".
    return bool(re.search(r"[?;\n]\s*(?:e\s+)?(?:" + _CHANGE + r")\b", text))


def is_information_request(question):
    text = normalized(question)
    continuation = re.match(
        r"(?:continue|continuar)\s+(?:(?:a|essa|esta|the)\s+)?"
        r"(?:resposta|explicacao|texto|answer|explanation|text)\b",
        text,
    )
    if continuation:
        text = text[continuation.end() :]
    if re.search(r"\b(?:" + _ACTION + r")\b", text):
        return False
    return bool(continuation or re.match(_CONSULTATION, text))
