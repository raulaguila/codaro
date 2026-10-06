# Auditoria do Codaro CLI

Data: 2026-10-05. Ambiente validado: Linux, Python 3.12.14, dependências em `requirements-dev.lock`.

## Resultado

A instalação editável foi concluída. Após as correções, os 103 testes passaram em aproximadamente 4 segundos. `ruff check .`, `ruff format --check .` e `python -m pip check` passaram. Os comandos `doctor`, `index`, `search --json` e `read` foram executados no projeto de exemplo.

O resultado sustenta os fluxos de investigação implementados. Não certifica a qualidade das respostas de um modelo real, a precisão semântica em grandes repositórios ou a segurança de uma futura ferramenta de edição/execução.

## Achados corrigidos

| Prioridade | Problema observado na revisão | Correção | Evidência |
|---|---|---|---|
| Alta | Símbolos podiam ser lidos usando posições antigas do índice. | Extração sobre o arquivo atual em cada leitura; nomes repetidos exigem posição para desambiguação. | Testes de deslocamento de linhas, nomes duplicados e leitura de classes. |
| Alta | Respostas malformadas de modelos podiam gerar erros de tipo/chave e quebrar a investigação. | Validação de mensagens, conteúdo, IDs, tipos, tamanho e argumentos antes da execução. JSON com aninhamento excessivo é tratado como erro. | Casos HTTP malformados e testes de argumentos/IDs inválidos. |
| Alta | Leituras normais de caminhos tinham uma janela para seguir links simbólicos trocados entre a checagem e a abertura. | Rejeição de links e, no POSIX, abertura por descritores de diretório com `O_NOFOLLOW`. Limite de bytes aplicado à leitura real. | Caminhos externos, links, arquivos grandes/binários e FIFO. |
| Média | Orçamento de resultados podia ser ultrapassado por escapes JSON ou mensagens de erro. O histórico não limitava a requisição atual. | Limites sobre JSON serializado, recorte de resultados, remoção de turnos inteiros e verificação antes de cada requisição. | Testes de escaping, lotes grandes e expulsão do histórico. |
| Média | Histórico armazenava todos os corpos das ferramentas, aumentando custo e levando código antigo aos turnos seguintes. | Retenção somente de perguntas/respostas, com 16.000 caracteres por padrão; instrução para reler evidências atuais. | Teste de histórico sem payloads de ferramentas. |
| Média | Cache de leituras podia devolver evidência antiga após edição durante o mesmo turno. | Chave do cache inclui hash do conteúdo; falhas não são armazenadas. | Testes de repetição, mudança do arquivo e nova tentativa após erro. |
| Média | Uma linha longa podia exceder o limite por causa do separador; continuação de símbolos podia pular linhas omitidas. | Limite de 6.000 caracteres incluindo separadores, `partial_line` e próxima linha de acordo com a saída real. | Testes de fronteira e truncamento por caracteres. |
| Média | Correspondências exatas de métodos não tinham prioridade garantida; resultados repetiam classes e métodos sobrepostos. | Busca exata também pelo nome final qualificado; prioridade explícita e deduplicação por família de símbolos. | Testes de posição do método e ausência da classe redundante. |
| Média | Tokenização descartava palavras depois do 40º termo da linha e não separava siglas de identificadores. | Limite aplicado à consulta, não ao conteúdo indexado; separação de camelCase, snake_case e siglas. | Termo ao final de linha longa e `HTTPRequestHandler`. |
| Média | Listagem de arquivos usava quebras de linha, expunha caracteres de controle e tinha erros pouco claros de timeout. | Saída delimitada por NUL, rejeição de caminhos com controles, sanitização dos previews e erros legíveis. | Arquivos com nomes especiais, controles no conteúdo e timeout simulado. |
| Média | API não tinha resposta limitada, tratamento específico de timeout ou novas tentativas para falhas transitórias. | Resposta de até 256 KB, saída textual limitada, timeout configurável e até três tentativas para 429/502/503/504. Corpos de erros remotos não são exibidos. | MockTransport com timeout, limites e contagem de tentativas. |
| Média | CLI podia imprimir traceback em falhas do SQLite e o diagnóstico não verificava FTS5. | Erros esperados em stderr e código 1; diagnóstico local de ripgrep, FTS5 e configuração. | Testes de índice corrompido, configuração inválida e requisito ausente. |
| Média | Interface não tinha cancelamento, adaptação a terminais estreitos ou testes de interação. | Ctrl+X, descarte de resposta cancelada, proteção contra investigações simultâneas, lateral responsiva e logs limitados. | Envio, limpeza, recuperação de erro, cancelamento e layout headless. |
| Média | O schema antigo não podia acomodar as correções do índice com segurança operacional. | Versão explícita, reconstrução do cache antigo, índices SQL por caminho/símbolo, fechamento em falhas e atualização transacional. | Migração do formato anterior e rollback após falha do parser. |
| Baixa | CI falhava em lint/formatação; dependências tinham somente intervalos amplos. | Código formatado, lint limpo, snapshot de versões e matriz de CI para Python 3.11–3.13. | Ruff, verificação de dependências e configuração do workflow. |
| Baixa | Modelo padrão anterior não era uma escolha suficientemente clara para uma integração que exige tools. | Padrão e exemplos ajustados para `qwen2.5:7b`; documentação exige suporte a ferramentas. | Configuração local e contratos simulados; execução do modelo real permanece pendente. |

## Escopo dos testes

- Recuperação textual e estrutural, decoradores, async, assinaturas multilinha e nomes qualificados.
- Atualização, remoção, regras de ignore, migração e rollback do índice.
- Arquivos vazios, BOM, CRLF, UTF-8 inválido, conteúdo binário e limites de leitura.
- Validação de ferramentas, repetição, atualização de evidências, histórico, contexto e cancelamento.
- API OpenAI-compatible via `httpx.MockTransport`, incluindo um ciclo integrado de busca → leitura → resposta.
- CLI com `CliRunner` e comandos executados diretamente.
- Interface Textual com teclado e workers reais em modo headless.

O executor restrito deste ambiente apresentou bloqueio no encerramento de asyncio após trabalho em threads. Um timer no harness headless mantém o loop dos testes progredindo. O código da aplicação não foi alterado para substituir o loop de asyncio, e os testes não fazem chamadas de rede externas.

## Limitações e próximos passos

1. **Modelo real:** não há Ollama ativo nem credencial de API configurada nesta sessão. Verificar qualidade de tool calling, latência, consumo de contexto e respostas com um modelo real antes de divulgação ampla. Os testes de HTTP usam respostas simuladas.
2. **Busca semântica:** esta versão combina texto e símbolos. Embeddings, reranking e LSP ainda não existem; não apresentar o produto como busca por vetores. Medir Recall@5 em perguntas reais antes de escolher modelos e ranking adicional.
3. **Tokens:** os orçamentos são em caracteres de JSON serializado, não tokens do provedor. Eles controlam volume, mas não garantem caber na janela de todo modelo.
4. **Cancelamento e prazo:** Ctrl+X cancela entre operações e fragmentos recebidos; uma conexão sem dados ainda pode aguardar o timeout. O timeout é por operação HTTP. Até três tentativas podem ampliar o tempo total de uma chamada.
5. **Escala:** atualização e busca verificam hashes dos arquivos. Não foi realizado benchmark representativo em repositórios grandes. Limite de 20.000 arquivos não equivale a uma garantia de desempenho; ignore dependências e dados desnecessários.
6. **Linguagens:** somente Python tem extração estrutural com Tree-sitter. Outras extensões usam janelas de arquivo. Extração sintática não é análise de tipos, resolução de chamadas ou garantia de validade do programa.
7. **Plataformas:** execução local validada em Linux/Python 3.12. O CI inicial passou em 3.11, 3.12 e 3.13 no [run de publicação](https://github.com/raulaguila/codaro/actions/runs/37333295862). O fallback sem `O_NOFOLLOW` não tem as mesmas garantias do caminho POSIX contra mudanças concorrentes.
8. **Dados privados:** as exclusões padrão não detectam todos os segredos. Perguntas e trechos consultados são enviados ao endpoint escolhido; o índice guarda conteúdo local em `.codaro`. Revise `.codaroignore` antes de usar um provedor externo.
9. **Capacidades:** as ferramentas implementadas são de investigação. Não há edição, execução de comandos/testes pelo agente ou isolamento para essas futuras operações. O prompt sobre conteúdo não confiável não é uma defesa completa contra prompt injection.
10. **Armazenamento:** corrupção do SQLite é reportada, não reparada automaticamente. Como o índice é derivado, feche processos, renomeie `.codaro` e reindexe para recuperação.

## Reprodução

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.lock
pip install -e '.[dev]'
pytest -q
ruff check .
ruff format --check .
python -m pip check
codaro doctor
codaro index --repo examples/demo
codaro search can_edit --repo examples/demo --json
```

## Atualização: streaming e interface Markdown

A entrega seguinte adiciona streaming SSE em `chat` e `ask`, respostas Markdown com código destacado, cartões de ferramentas com alvo/resultado/duração e volume de contexto na barra de status.

Validação local: **135 testes aprovados**, lint e formatação aprovados. Os novos casos cobrem UTF-8 dividido entre chunks, montagem incremental de argumentos de ferramentas, cancelamento, EOF prematuro, limites, fallback JSON, falha de conexão sem repetição, roundtrip SSE com ferramentas e renderização parcial/final na interface. Os modelos continuam simulados: esta entrega não comprova a qualidade de um modelo real.

O streaming limita a resposta a 2 MB de transporte e 16.000 caracteres de conteúdo, com as mesmas validações de ferramentas. Eventos de conclusão inválidos e conteúdo depois da conclusão são rejeitados. Atualizações da interface são agrupadas para reduzir o custo de renderização; o histórico mantém somente respostas concluídas.

## Edição com aprovação — 5 de outubro de 2026

Entrega: ferramenta `propose_edit`, propostas em memória, diff com destaque de sintaxe, revisão no chat e comando `codaro edit`. O modelo não tem acesso à aplicação: cada arquivo exige aprovação na interface. `codaro ask` e `chat --read-only` preservam o modo de investigação.

Controles verificados: leitura prévia do trecho inteiro, substituição exata e única, limites de proposta/diff, descarte ao cancelar ou falhar, bloqueio de novos turnos enquanto há revisão pendente, validação atual de ignores, comparação de conteúdo e identidade do arquivo antes da escrita, rejeição de links simbólicos/hard links, escrita temporária seguida de substituição atômica e limpeza em falhas. BOM UTF-8, LF/CRLF e permissões usuais preservados.

Validação local: **161 testes passaram**, Ruff lint e formatação passaram, `pip check` sem dependências quebradas e `git diff --check` sem erros. Testes de edição cobrem confirmação padrão negativa/EOF na CLI, revisão e aprovação/rejeição no Textual, conflito durante a revisão e durante a escrita temporária, falha de substituição sem perda do original, mudanças nas regras de ignore, conteúdo truncado, resposta interrompida e cancelamento. Modelos são simulados; a qualidade de propostas de modelos reais ainda depende de avaliação com o provedor escolhido.

Limitações: aplicação requer POSIX com `dir_fd` e `O_NOFOLLOW`. Propostas são separadas por arquivo, não uma transação do conjunto; não há criação/exclusão de arquivos, undo automático, execução de testes ou recuperação de propostas entre sessões. A operação preserva bits de permissão usuais, não promete preservar ACLs/metadados estendidos. As verificações de concorrência não constituem proteção contra todo processo hostil que altera caminhos no intervalo mínimo entre verificação e substituição.

## Contexto do diretório selecionado

Corrigida a ausência da raiz do projeto nas mensagens do modelo: a UI selecionava a pasta correta, mas o agente não informava essa raiz à API, permitindo respostas com diretórios inventados. Cada consulta agora inclui contexto da sessão com a raiz absoluta, capacidades e escopo das ferramentas; `get_repository_info` permite consultar esses dados. `/pwd` no chat e `codaro pwd --repo CAMINHO` mostram o diretório diretamente, sem depender do modelo.

Validação: **172 testes passaram** localmente, incluindo raiz fornecida em cada chamada, histórico com caminho desatualizado, ferramentas lendo/listando arquivos da pasta selecionada em vez da pasta de instalação e consulta local de diretório sem chamar IA. Ruff e formatação passaram. As respostas de modelos nos testes são simuladas; as instruções e ferramentas fornecem fatos, mas respostas em linguagem natural continuam dependendo do modelo.

## Interface, TLS e recuperação do protocolo de ferramentas

Entregues boas-vindas compactas com sugestões que preenchem o rascunho, lateral recolhível com Ctrl+B, caminho abreviado com acesso ao valor completo por /pwd, bordas discretas, entrada multilinha (Enter envia; Alt+Enter insere nova linha), atividades concluídas expansíveis e estados de sessão mais claros. Texto intermediário de fases de ferramentas é removido antes da resposta final; campos separados de reasoning não entram no conteúdo exibido.

TLS: verificação ativa por padrão em todas as conexões HTTPX do provedor. `--tls-insecure`, `--tls-verify` e `CODARO_TLS_INSECURE` permitem configurar a verificação de certificados/hostname explicitamente. Flags sobrescrevem o ambiente; valores ambíguos são rejeitados. O modo efetivo aparece na sessão e no diagnóstico.

Protocolo: o agente continua executando somente `tool_calls` estruturados e enviando resultados com `role: tool` e o `tool_call_id` correspondente. Strings de inteiros decimais canônicos são normalizadas antes da validação, mantendo limites, rejeição de booleanos/floats/expressões e política de arquivos. Tentativas reconhecidas de chamada escrita como texto recebem uma única correção; o texto nunca é executado como ferramenta. Persistência do erro produz diagnóstico explícito e não salva uma resposta falsa. `codaro doctor --check-tools` faz uma chamada ao provedor para verificar uma ferramenta de teste sem executar ações de arquivo.

Validação: **207 testes passaram**, além de Ruff, formatação, `pip check` e `git diff --check`. Testes cobrem o caso de `list_files` com `"limit":"10"`/`"offset":"0"` pelo contrato HTTP real do cliente com transporte simulado, resultado vinculado ao mesmo ID, correção limitada de chamadas em texto, descarte de prosa intermediária, isolamento de reasoning, precedência de configuração TLS e sua aplicação ao cliente em respostas normais/streaming, entrada multilinha, rascunhos longos, lateral responsiva e expansão de ações. Modelos e respostas HTTP nos testes são simulados; um diagnóstico positivo de um modelo real confirma aquela chamada, sem garantir a qualidade de todas as suas decisões.

## Fluxo JSON e comparação com Thoth — 6 de outubro de 2026

Comparação com o `thoth-backend`: [registro do fluxo](https://github.com/raulaguila/thoth-backend/blob/4375307787b535c3161dfae478282ab1cf8f3d3f/internal/core/usecase/chat_dump.go), [serialização OpenAI](https://github.com/raulaguila/thoth-backend/blob/4375307787b535c3161dfae478282ab1cf8f3d3f/internal/adapter/outbound/llm/chat_helpers.go), [planejamento e síntese](https://github.com/raulaguila/thoth-backend/blob/4375307787b535c3161dfae478282ab1cf8f3d3f/internal/core/usecase/chat_agent.go). Ambos usam definições de ferramentas nativas, mensagens de assistente com `tool_calls` e resultados `role: tool` vinculados por ID. O Thoth separa planejamento JSON e síntese por streaming; o Codaro mantém suporte a ferramentas durante SSE. O dump do Thoth é opcional; o do Codaro é sempre ativo, conforme solicitado.

Cada investigação grava `.codaro/prompt.json` na pasta explorada: identificação, metadados, pergunta, mensagens e schemas enviados, orçamento, respostas, tentativas HTTP, JSON/SSE original, motivo de parada e usage disponível, argumentos normalizados e resultados de ferramentas, eventos, resposta final ou erro/cancelamento. Checkpoints antes da requisição e após respostas/ferramentas precedem o fechamento final. A gravação é atômica, com temporário exclusivo e `0600` em POSIX; links no destino são bloqueados, e a escrita usa descritores de diretório em POSIX. Falha de gravação produz aviso sem substituir a resposta ou exceção original. Cabeçalhos de autenticação não são capturados; a chave configurada é removida também de conteúdo e representações escapadas em JSON.

Correções adicionais: construção compartilhada do payload entre envio, orçamento e rastreamento; resultados com `name` além do ID; um erro no lote não é encoberto pelo sucesso de uma ferramenta seguinte; rejeição controlada de `function_call` legado e conclusão `tool_calls` sem chamadas. O diagnóstico agora verifica um ciclo completo em duas requisições: chamada nativa, resultado com marcador aleatório e resposta final pelo caminho de streaming/fallback JSON.

Validação: **231 testes passaram**, Ruff e formatação passaram, dependências consistentes e `git diff --check` sem erros. Casos novos incluem correspondência entre dump e payload HTTP real do cliente, normalização sem alteração dos argumentos originais registrados, falhas de parsing e ferramentas, retorno de resultados vinculados por ID, erros HTTP limitados e sem exposição na resposta, retries, reasoning/usage em SSE, cancelamento parcial ou antes da chamada, troca do fluxo anterior, permissões, limpeza e preservação do arquivo anterior em falhas atômicas, bloqueio de links, mascaramento de chave com escapes e isolamento entre threads/projetos. Respostas de modelos e transporte HTTP são simulados; a execução de `doctor --check-tools` contra o endpoint do usuário continua necessária para confirmar a configuração real.

Limites: um encerramento forçado pode deixar o último checkpoint com status `running`; na mesma pasta, sessões simultâneas sobrescrevem o arquivo pela ordem das gravações. O dump contém perguntas e código, não apenas metadados: mascarar a chave do provedor não remove outros segredos desses conteúdos. Inclua `.codaro/` no `.gitignore` de projetos externos. Revisões/aplicações de propostas após a resposta pertencem à interface e não são chamadas do fluxo de IA registrado.

## Explicação do projeto a partir de evidências — 6 de outubro de 2026

Reprodução relatada: ao pedir a estrutura e os pontos de entrada do projeto, o modelo chamava somente `get_repository_info` e tratava `list_files`, `search_code` e outras capacidades do Codaro como pontos de entrada do código. A chamada estruturada funcionava, mas sua informação não respondia à pergunta. Conferido novamente o adaptador OpenAI do Thoth: `openaiTools` transforma as definições internas planas em `type: function` com o objeto `function`, exatamente o envelope usado pelo Codaro em `/chat/completions`. A diferença no dump não identifica uma diferença nesse payload.

O prompt, a descrição e o resultado de `get_repository_info` agora distinguem metadados da sessão e estrutura do projeto. Perguntas reconhecidas sobre estrutura, arquitetura e pontos de entrada precisam de leitura de arquivos no turno atual e citação de uma linha efetivamente lida. Metadados, previews, listagens, leitura com erro e linhas parcialmente truncadas não qualificam. Uma conclusão sem essas evidências recebe uma única correção; persistência ou orçamento esgotado geram erro explícito sem salvar a explicação falsa no histórico. O texto rejeitado fica apenas no dump, sem ser reenviado como fatos. Escopo vazio identificado pelo índice retorna uma limitação concreta.

Nessas perguntas, a investigação usa JSON até obter uma leitura válida; depois fica disponível o streaming. Conteúdo de chamadas de ferramentas em respostas JSON não é emitido como resposta final. O dump registra `evidence_repair` e os caminhos/intervalos observados. Consultas de diretório/capacidades, listagens e perguntas conceituais gerais preservam o fluxo anterior.

Validação: **249 testes aprovados** entre a suíte completa e a nova regressão de interface; Ruff, formatação, dependências e `git diff --check` aprovados. Casos incluem reprodução da foto com recuperação via manifesto, repetição limitada sem histórico falso, ausência de citações, citação fora do intervalo ou com nome de arquivo inventado, número de linha excessivamente longo, leitura parcial, evidências de turnos anteriores, escopo vazio, perguntas locais/conceituais e planejamento JSON seguido de SSE com payloads reais do cliente HTTP sob transporte simulado. A regressão Textual confirma que a explicação rejeitada não vira uma mensagem final e que os cartões de ferramentas permanecem visíveis.

Limites: o reconhecimento de perguntas é por padrões em português e inglês; não cobre toda formulação possível. Leitura e citação válidas não demonstram relevância de todas as evidências nem correção semântica de toda conclusão. A qualidade do planejamento continua dependente do modelo e do servidor; o teste reproduz a falha observada com respostas simuladas.
