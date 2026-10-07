"""Local composer helpers; no model calls or filesystem access."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

COMMANDS = {
    "/mode": "Ver/trocar modo: ask, plan ou execute",
    "/ask": "Trocar para Perguntar",
    "/plan": "Trocar para Planejar",
    "/execute": "Executar o plano ativo ou trocar para Executar",
    "/task": "Ver tarefa; new objetivo, list ou resume identificador",
    "/permissions": "Revisar autorização por tarefa ou voltar a action",
    "/help": "Comandos e atalhos",
    "/pwd": "Diretório da sessão",
    "/status": "Modelo, modo e contexto enviado",
    "/model": "Selecionar modelo do provedor ou trocar com /model nome",
    "/models": "Lista e seleção de modelos da API",
    "/providers": "Cadastrar um provedor e sua API key (BYOK)",
    "/clear": "Limpar a conversa atual",
    "/resume": "Retomar a última conversa salva neste projeto",
    "/compact": "Reduzir o contexto aos quatro turnos mais recentes",
    "/history": "Buscar conversa com /history termos",
    "/memory": "Ver memória ou registrar decision/constraint/pending texto",
    "/map": "Mapa atualizado do projeto",
    "/changes": "Alterações e checkpoints locais",
    "/undo": "Revisar diff para desfazer a última edição ou um checkpoint",
}


class InputHistory:
    def __init__(self, values=()):
        self.values = list(values)[-100:]
        self.position = len(self.values)
        self.draft = ""

    def push(self, value: str):
        if not self.values or self.values[-1] != value:
            self.values.append(value)
            self.values = self.values[-100:]
        self.position = len(self.values)

    def previous(self, current: str) -> str:
        if self.position == len(self.values):
            self.draft = current
        elif current != self.values[self.position]:
            self.position = len(self.values)
            self.draft = current
        if self.position:
            self.position -= 1
        return self.values[self.position] if self.values else current

    def next(self, current: str) -> str:
        if self.position >= len(self.values):
            return current
        if current != self.values[self.position]:
            self.position = len(self.values)
            return current
        self.position += 1
        return self.values[self.position] if self.position < len(self.values) else self.draft


@dataclass(frozen=True)
class Completion:
    start: int
    end: int
    value: str
    label: str


def completions(text: str, cursor: int, paths: list[str]) -> list[Completion]:
    prefix = text[:cursor]
    if re.fullmatch(r"/[a-z]*", prefix):
        return [
            Completion(0, cursor, name + " ", name + " · " + description)
            for name, description in COMMANDS.items()
            if name.startswith(prefix)
        ]
    match = re.search(r'(?:^|\s)(@(?:"([^"\n]*)|([^\s"]*)))$', prefix)
    if match is None:
        return []
    query = match[2] if match[2] is not None else match[3]
    result = []
    for path in paths:
        if path.casefold().startswith(query.casefold()):
            quoted = any(c.isspace() or c in {'"', "\\"} for c in path)
            value = "@" + (json.dumps(path, ensure_ascii=False) if quoted else path) + " "
            result.append(Completion(match.start(1), cursor, value, "@" + path))
            if len(result) == 8:
                break
    return result


def references(question: str) -> list[str]:
    result = []
    for match in re.finditer(r'(?:^|\s)@(?:"((?:\\.|[^"\\\n])*)"|([^\s"]+))', question):
        try:
            name = json.loads('"' + match[1] + '"') if match[1] is not None else match[2]
        except json.JSONDecodeError as exc:
            raise ValueError("Referência entre aspas inválida.") from exc
        if not name:
            raise ValueError("Referência vazia.")
        if name not in result:
            result.append(name)
    if len(result) > 4:
        raise ValueError("Inclua até quatro referências @arquivo por pergunta.")
    return result
