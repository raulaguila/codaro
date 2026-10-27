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

## Implementado: recuperação multilíngue e mapa automático

Busca/listagem com filtros, `grep_code`, `get_project_map`, `get_file_outline`,
`find_definition`, `find_references`, `record_finding` e `search_findings` estão
disponíveis. As ferramentas adicionais são carregadas sob demanda. O mapa é
construído automaticamente durante a interação, com uma projeção pequena no
contexto; detalhes podem ser recuperados por diretório e por páginas.

Os parsers abrangem diversas linguagens; referências LSP dependem de servidor
instalado/habilitado e alternativa textual é identificada. Veja
[Recuperação de código](RETRIEVAL.md) para contratos, níveis de suporte, memória
com hashes, índice incremental e embeddings locais opcionais.

## Próximas etapas ainda pendentes

Ainda não implementadas nesta etapa:

- Estados de atividade específicos para catálogo, diagnósticos e outras ferramentas.
- `get_changes`, `inspect_project` e `inspect_path`.
- Orçamento de contexto detalhado e consulta de tarefa também no modo Perguntar.

As novas ferramentas deverão ser carregadas sob demanda e reutilizar as políticas
de arquivos, revisão, validação e cancelamento existentes.

## Implementado na versão 0.2

- `get_tools_catalog`: busca por descrição/nome e categoria. Seleção antecipada
  respeita o orçamento; o registro completo continua acessível sob demanda.
- `apply_changes`: operações `edit` podem fornecer `replacements` (até 16 pares
  old_text/new_text sem sobreposição, observados no mesmo snapshot). Lotes usam
  `atomic: true` por padrão: preflight e tentativa de rollback com checkpoints,
  sem sobrescrever conflitos externos. Não há atomicidade contra queda do processo.
- `discover_checks`: comandos derivados dos manifestos Python, Node, Go, Rust,
  Java e .NET. `update_plan.criterion_checks` associa critérios aos IDs descobertos.
- `run_check`: executa um comando descoberto após aprovação; `get_validation_report`
  distingue critérios aprovados, pendentes ou indisponíveis na revisão atual.
- `start_command`, `get_command_output`, `cancel_command`: jobs próprios da sessão,
  saída paginada, até quatro ativos e cancelamento. IDs antigos não são restaurados
  como PIDs. `cwd` é relativo à raiz e aparece para o revisor.
- `get_language_support`: inventário local de parsers, fixtures estruturais e LSP.

LSP e compiladores não são instalados automaticamente. O teste de uma linguagem
no parser não representa validação de build. Veja [RELEASES.md](RELEASES.md) para
limites de rollback, processos, sandbox e atualização.
