from __future__ import annotations

import json
import re
import unicodedata


def serialize(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def is_project_overview(question: str) -> bool:
    """Recognize project overview requests, leaving general and session questions alone."""
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", question.casefold())
        if not unicodedata.combining(char)
    )
    project = re.search(r"\b(projeto|repositorio|project|repository|repo|codebase)\b", text)
    overview = re.search(r"\b(estrutura|arquitetura|structure|architecture)\b", text)
    entrypoints = re.search(r"\b(pontos? de entrada|entry[ -]?points?)\b", text)
    explanation = re.search(
        r"\b(explique|explique-me|explore|explain|describe|descreva)\b"
        r"|(?:o que|what).*(?:falar|dizer|tell|about)|fale sobre",
        text,
    )
    return bool(entrypoints or project and (overview or explanation))


def is_information_request(question: str) -> bool:
    """Only explicit consultations bypass a pending implementation task."""
    text = question.strip().casefold()
    text_continuation = re.match(
        r"(?:continue|continuar)\s+(?:(?:a|essa|esta|the)\s+)?"
        r"(?:resposta|explicação|explicacao|texto|answer|explanation|text)\b",
        text,
    )
    if text_continuation:
        text = text[text_continuation.end() :]
    if re.search(
        r"\b(implemente|implementar|crie|criar|corrija|corrigir|altere|alterar|"
        r"adicione|adicionar|remova|remover|execute|executar|continue|continuar|"
        r"valide|validar|refatore|ajuste|atualize|faça|faca|mude|substitua|"
        r"aplique|edite|editar|teste|testar|implement|create|fix|change|"
        r"add|remove|run|execute|continue|validate|refactor|update|delete|test)\b",
        text,
    ):
        return False
    return bool(
        text_continuation
        or re.match(
            r"(?:explique|descreva|fale sobre|o que|qual|quais|quem|como|onde|"
            r"explain|describe|what|which|who|how|where)\b",
            text,
        )
    )


def cites_observed_lines(answer: str, evidence: list[tuple[str, int, int]]) -> bool:
    for path, start, end in evidence:
        for match in re.finditer(r"(?<![\w./-])" + re.escape(path) + r":(\d{1,9})(?!\d)", answer):
            if start <= int(match[1]) <= end:
                return True
    return False


def textual_tool_call(content: str, *, after_error: bool = False) -> bool:
    """Detect protocol mistakes for a bounded repair, never execute text as a tool."""
    intention = any(
        phrase in content.casefold()
        for phrase in (
            "vou tentar",
            "vou chamar",
            "vou usar a ferramenta",
            "vou executar",
            "i will call",
            "i'll call",
            "let me call",
            "retry the tool",
        )
    )
    decoder = json.JSONDecoder()
    for match in list(re.finditer(r"(?m)^[ \t]*(?:<tool_call>\s*)?(?=[{\[])", content))[:8]:
        try:
            value, end = decoder.raw_decode(content, match.end())
        except (ValueError, RecursionError):
            continue
        # Invented names (e.g. read_file) are protocol mistakes too. Restrict
        # detection to call-shaped objects, but never turn them into executable calls.
        candidates = value.get("tool_calls", [value]) if isinstance(value, dict) else value
        if not isinstance(candidates, list):
            continue
        call_shaped = False
        for candidate in candidates[:8]:
            if not isinstance(candidate, dict):
                continue
            function = candidate.get("function", candidate)
            if (
                isinstance(function, dict)
                and isinstance(function.get("name"), str)
                and re.fullmatch(r"[A-Za-z_][\w.-]{0,79}", function["name"])
                and {"arguments", "parameters"}.intersection(function)
            ):
                call_shaped = True
                break
        if not call_shaped:
            continue
        prefix = content[: match.end()].strip()
        suffix = content[end:].strip()
        standalone = prefix in {"", "```", "```json", "<tool_call>"} and suffix in {
            "",
            "```",
            "</tool_call>",
        }
        if standalone or intention or after_error:
            return True
    return False
