from __future__ import annotations


def schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


SCOPE = {
    "path": {
        "type": "string",
        "maxLength": 500,
        "description": "Arquivo ou diretório relativo à raiz.",
    },
    "glob": {"type": "string", "maxLength": 500},
    "language": {"type": "string", "maxLength": 80},
    "extension": {"type": "string", "maxLength": 30},
}
POSITION = {
    "line": {
        "type": "integer",
        "minimum": 1,
        "description": "Com path/column, tenta resolução LSP se habilitado.",
    },
    "column": {"type": "integer", "minimum": 0, "description": "Coluna UTF-16 começando em zero."},
}

RETRIEVAL_TOOLS = [
    schema(
        "grep_code",
        "Busca literal ou regex em arquivos permitidos. Filtre o escopo; resultados paginados.",
        {
            "query": {"type": "string", "maxLength": 1000},
            "regex": {"type": "boolean"},
            "case_sensitive": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 60},
            "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
            **SCOPE,
        },
        ["query"],
    ),
    schema(
        "get_project_map",
        "Mapa automático de diretórios, linguagens, pacotes, scripts e dependências candidatas. "
        "Não comprova implementação.",
        {
            "path": SCOPE["path"],
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 200, "maximum": 6000},
        },
        [],
    ),
    schema(
        "get_file_outline",
        "Lista funções, classes, tipos e outros símbolos sem ler o corpo inteiro.",
        {
            "path": SCOPE["path"],
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 60},
        },
        ["path"],
    ),
    schema(
        "find_definition",
        "Localiza definições estruturais por nome. Com path/line/column, tenta LSP habilitado; "
        "caso contrário, retorna candidatos por nome.",
        {"symbol": {"type": "string", "maxLength": 300}, **SCOPE, **POSITION},
        ["symbol"],
    ),
    schema(
        "find_references",
        "Com path/line/column, tenta LSP habilitado. Alternativa textual inclui comentários e "
        "homônimos, identificada por resolved=false.",
        {
            "symbol": {"type": "string", "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": 60},
            "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
            **SCOPE,
            **POSITION,
        },
        ["symbol"],
    ),
    schema(
        "record_finding",
        "Salva descoberta, hipótese ou pendência do agente com hashes de fontes. "
        "Não registra decisões do usuário nem concede permissão.",
        {
            "kind": {
                "type": "string",
                "enum": ["finding", "hypothesis", "pending"],
                "maxLength": 20,
            },
            "text": {"type": "string", "maxLength": 800},
            "sources": {
                "type": "array",
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "path": SCOPE["path"],
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path", "start_line", "end_line"],
                    "additionalProperties": False,
                },
            },
        },
        ["kind", "text"],
    ),
    schema(
        "search_findings",
        "Recupera descobertas e pendências; source_status identifica fontes alteradas. "
        "Não é evidência atual.",
        {
            "query": {"type": "string", "maxLength": 1000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12},
        },
        [],
    ),
]
RETRIEVAL_NAMES = {tool["function"]["name"] for tool in RETRIEVAL_TOOLS}

TOOLS = [
    schema(
        "get_repository_info",
        "Informa somente a raiz e as capacidades da sessão do Codaro. "
        "Não informa a estrutura, arquitetura ou pontos de entrada do projeto.",
        {},
        [],
    ),
    schema(
        "search_code",
        "Busca nomes e termos; retorna metadados e previews para localizar código.",
        {
            "query": {"type": "string", "maxLength": 1000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12},
            **SCOPE,
            "kind": {"type": "string", "maxLength": 30},
        },
        ["query"],
    ),
    schema(
        "read_symbol",
        "Lê a implementação atual de um símbolo. Use start_line se o nome for ambíguo.",
        {
            "path": {"type": "string", "maxLength": 2000},
            "symbol": {"type": "string", "maxLength": 500},
            "start_line": {"type": "integer", "minimum": 1},
        },
        ["path", "symbol"],
    ),
    schema(
        "read_lines",
        "Lê até 160 linhas. Continue uma linha longa usando next_start_line e next_column "
        "como start/column; envie file_hash para detectar mudança entre páginas.",
        {
            "path": {"type": "string", "maxLength": 2000},
            "start": {"type": "integer", "minimum": 1},
            "end": {"type": "integer", "minimum": 1},
            "column": {"type": "integer", "minimum": 0},
            "max_chars": {"type": "integer", "minimum": 200, "maximum": 6000},
            "file_hash": {"type": "string", "maxLength": 64},
        },
        ["path", "start", "end"],
    ),
    schema(
        "list_files",
        "Lista caminhos permitidos com paginação de até 60 arquivos.",
        {
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 60},
            **SCOPE,
        },
        [],
    ),
] + RETRIEVAL_TOOLS

CONTEXT_TOOLS = [
    schema(
        "get_context_status", "Consulta orçamento e uso estimado do contexto desta chamada.", {}, []
    ),
    schema(
        "compact_context",
        "Libera histórico e trechos antigos antes da próxima chamada. "
        "Resultados de ações são preservados; releia código antes de editar.",
        {},
        [],
    ),
    schema(
        "request_tools",
        "Carrega ferramentas por nome para a próxima chamada, quando "
        "o contexto usa um conjunto reduzido. Não concede permissões.",
        {"names": {"type": "array", "items": {"type": "string"}, "maxItems": 8}},
        ["names"],
    ),
]

MEMORY_TOOLS = [
    schema(
        "search_conversation",
        "Busca pedidos/decisões na conversa deste projeto. "
        "Não comprova código nem autoriza comandos.",
        {
            "query": {"type": "string", "maxLength": 1000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        ["query"],
    ),
    schema(
        "read_conversation",
        "Recupera um turno por identificador com paginação; "
        "respostas antigas podem estar desatualizadas.",
        {
            "turn_id": {"type": "string", "maxLength": 64},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 200, "maximum": 4000},
        },
        ["turn_id"],
    ),
    schema(
        "remember_task",
        "Registra uma nota de continuidade da tarefa, atribuída ao agente. "
        "Não altera decisões/restrições do usuário nem autorizações.",
        {"note": {"type": "string", "maxLength": 300}},
        ["note"],
    ),
]

EDIT_TOOL = schema(
    "propose_edit",
    "Substitui trecho exato já lido; revisão humana ocorre antes da aplicação em Executar.",
    {
        "path": {"type": "string", "maxLength": 2000},
        "old_text": {"type": "string", "maxLength": 3000},
        "new_text": {"type": "string", "maxLength": 3000},
        "reason": {"type": "string", "maxLength": 500},
    },
    ["path", "old_text", "new_text", "reason"],
)

COMMAND_TOOL = schema(
    "run_command",
    "Executa argumentos separados na raiz do projeto após aprovação humana. "
    "Retorna saída, exit_code e timeout. Use para testes e validação; sem shell implícito.",
    {
        "argv": {
            "type": "array",
            "items": {"type": "string", "maxLength": 2000},
            "minItems": 1,
            "maxItems": 40,
        },
        "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
        "purpose": {"type": "string", "enum": ["validation", "operation"], "maxLength": 20},
    },
    ["argv"],
)

TASK_TOOLS = [
    schema(
        "get_task",
        "Recupera tarefa em páginas de texto JSON; offset em caracteres.",
        {
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 200, "maximum": 4000},
        },
        [],
    ),
    schema(
        "update_plan",
        "Registra/revisa o plano; não concede permissões.",
        {
            "steps": {
                "type": "array",
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "maxLength": 300},
                        "state": {"type": "string", "enum": ["todo", "doing", "done"]},
                    },
                    "required": ["title", "state"],
                    "additionalProperties": False,
                },
            },
            "criteria": {
                "type": "array",
                "maxItems": 16,
                "items": {"type": "string", "maxLength": 300},
            },
            "validation_commands": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 40,
                    "items": {"type": "string", "maxLength": 2000},
                },
            },
        },
        ["steps", "criteria"],
    ),
    schema(
        "finish_task",
        "Registra conclusão/plano/bloqueio; alterações exigem validação real. "
        "verified_no_change permite concluir sem alteração somente após ler e validar o código.",
        {
            "status": {
                "type": "string",
                "enum": ["completed", "planned", "blocked"],
                "maxLength": 20,
            },
            "summary": {"type": "string", "maxLength": 2000},
            "verified_no_change": {"type": "boolean"},
        },
        ["status", "summary"],
    ),
]

CHANGES_TOOL = schema(
    "apply_changes",
    "Revisa e aplica um conjunto de até oito arquivos. "
    "Edit exige trecho lido; delete/rename exigem arquivo inteiro lido. "
    "Resultados podem ser parciais. Argumentos JSON: máximo 64.000 bytes UTF-8; "
    "divida arquivos/conjuntos maiores em chamadas menores.",
    {
        "reason": {"type": "string", "maxLength": 500},
        "operations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["edit", "create", "delete", "rename"]},
                    "path": {"type": "string", "maxLength": 2000},
                    "old_text": {"type": "string", "maxLength": 3000},
                    "new_text": {"type": "string", "maxLength": 3000},
                    "content": {"type": "string", "maxLength": 12000},
                    "destination": {"type": "string", "maxLength": 2000},
                },
                "required": ["kind", "path"],
                "additionalProperties": False,
            },
        },
    },
    ["reason", "operations"],
)

ALL_DEFINITIONS = [
    *TOOLS,
    *MEMORY_TOOLS,
    *CONTEXT_TOOLS,
    *TASK_TOOLS,
    EDIT_TOOL,
    CHANGES_TOOL,
    COMMAND_TOOL,
]
