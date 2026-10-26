# Evolução das ferramentas do agente

## Implementado: continuação de leituras parciais

`read_lines` aceita `column` (índice de caractere, começando em zero, na primeira
linha pedida) e `max_chars` entre 200 e 6000. A resposta inclui `next_start_line`,
`next_column` e `file_hash`. Para continuar, envie os dois primeiros como `start`
e `column`, mantenha `end` válido e envie o hash recebido.

O hash é verificado antes de consultar o cache. Se o arquivo mudou, a leitura é
rejeitada e deve ser reiniciada. O limite de contexto pode reduzir a página ainda
mais; nesse caso, o cursor acompanha o trecho efetivamente entregue ao modelo.
O cache não considera uma resposta truncada como uma leitura completa.

Uma continuação a partir de outra coluna não autoriza edição da linha inteira.
A verificação de edições permanece conservadora: o trecho deve estar inteiramente
em linhas completas observadas no arquivo atual. Esta etapa resolve a recuperação
do conteúdo, sem ampliar permissões de escrita.

Testes: `tests/test_read_cursors.py`, incluindo linha Unicode, redução por orçamento,
mudança de arquivo, execução pelo loop nativo e proteção contra edição de texto não lido.

## Próximas etapas da proposta aprovada

Ainda não implementadas nesta etapa:

- Busca e listagem com filtros; busca literal/regex com `grep_code`.
- Catálogo pesquisável por descrição/categoria e seleção progressiva antecipada.
- Símbolos JavaScript/TypeScript, outline de arquivos e navegação por referências.
- Múltiplas substituições por arquivo e contratos específicos por operação de edição.
- Estados de atividade específicos para catálogo, diagnósticos e outras ferramentas.
- `get_changes`, `inspect_project` e `inspect_path`.
- Descoberta de verificações e validação estruturada.
- Diretório por comando, execução em segundo plano, acompanhamento e cancelamento.
- Orçamento de contexto detalhado e consulta de tarefa também no modo Perguntar.

As novas ferramentas deverão ser carregadas sob demanda e reutilizar as políticas
de arquivos, revisão, validação e cancelamento existentes.
