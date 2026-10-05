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
