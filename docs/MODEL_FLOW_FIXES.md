# Correções do fluxo do modelo

Implementação de 8 de outubro de 2026 para os 13 achados da [auditoria original](audits/2026-10-07-model-flow/report.md), cuja base permanece `f0392a5`. As evidências originais descrevem o comportamento anterior e foram preservadas.

| Achado | Correção | Regressão verificável |
| --- | --- | --- |
| M01 | Redaction cumulativa nas mensagens, memória, sessão da UI e diagnóstico; inclui nomes de propriedades JSON. Autenticação usa a chave atual. | Trocar provedor com duas chaves no histórico e conferir corpo HTTP, turnos e arquivos locais. |
| M02 | Itens ativos do usuário ficam fora das notas recuperáveis que a compactação pode remover. | Oito restrições em janela de 4.096 permanecem no envio. |
| M03 | AGENTS.md da raiz é preservado integralmente; regras ancestrais são carregadas por caminho. Descobrir regra nova durante uma mutação exige reavaliá-la antes da execução. | Regra após a linha 80, regra de src e criação suspensa antes da aprovação. |
| M04 | Pedidos explícitos de implementação não concluem com digest inalterado. Código já correto exige leitura e validação reais via verified_no_change. | Resposta afirmando implementação fica bloqueada; assert executado sobre código já correto permite conclusão sem alteração. |
| M05 | Prazo compartilhado com adaptadores/streaming e comandos; timeout HTTP limitado ao tempo restante. | Stream lento encerra, fecha transporte e não publica deltas após o prazo. |
| M06 | Erro de memória em JSON/NDJSON HTTP 200 é classificado como erro recuperável do Ollama. | Reduzir num_ctx de 131.072 até 16.384 e aceitar apenas a resposta concluída. |
| M07 | Execução verifica o conjunto anunciado naquela requisição; carregamento vale no passo seguinte. Tarefas finalizadas rejeitam mutações. | Chamada não anunciada recebe erro; alteração posterior a finish_task não executa. |
| M08 | Adaptação comum permite argumentos até 64.000 bytes UTF-8; limites dos campos, lotes e contexto continuam ativos. | Operação com 9.000 caracteres percorre OpenAI, Anthropic e Ollama; transporte rejeita envelope acima do teto. |
| M09 | Tentativas com erro/cancelamento entram na memória; arquivos por execução sobrevivem à substituição de prompt.json. Recuperação paginada entrega resultados de ferramentas, sem raciocínio. | Buscar tentativa falha após outra pergunta e recuperar o último detalhe além da prévia de 1.200 caracteres. |
| M10 | Janela aprendida do Ollama persiste por até 24 horas, isolada por projeto/endpoint/modelo/configuração. /recalibrate restaura a configuração selecionada. | Nova instância começa na janela viável; expiração e comando da TUI reavaliam o limite. |
| M11 | OpenAI solicita stream_options.include_usage; opção rejeitada por compatível é retirada e não repetida na sessão. | Conferir payload real, consumo informado, calibração e fallback após HTTP 400. |
| M12 | length/max_tokens acionam até duas novas tentativas com instrução preservada para saída curta/operação menor. | JSON truncado não executa; segunda resposta pode concluir; três falhas encerram com histórico salvo. |
| M13 | Eventos incrementais por execução, prévias limitadas e cotas explícitas para snapshot/arquivo/retenção. | Eventos de 800 KB não aumentam o snapshot sem limite; arquivo mascara segredos; rotação e omissão por cota são identificadas. |

## Operação e limites

- `prompt.json`: snapshot privado e atômico, até 2 MB. Pode manter apenas turnos recentes.
- `run-<run_id>.jsonl`: eventos incrementais, até 8 MB por execução; arquivo privado e sem links. Prévias SSE/NDJSON no snapshot são limitadas a aproximadamente 32 mil caracteres. Não há regravação do arquivo incremental a cada fragmento.
- Retenção ao iniciar/finalizar: até 20 arquivos e 32 MB. Um arquivo ativo pode acrescentar até 8 MB antes da rotação final. Não há promessa de arquivo completo após atingir a cota: `archive.complete=false` e os contadores/erros explicitam isso.
- `read_conversation`: conteúdo histórico, paginado em até 4.000 caracteres, sem autorização para mudanças. Trechos atuais precisam ser relidos. Se o arquivo foi removido pela retenção, a prévia da memória permanece e `archive_available=false` informa a ausência.
- Restrições e orientações obrigatórias não são removidas para fazer uma chamada caber. Se o mínimo for inviável, a operação para com progresso preservado; não há como garantir conclusão em toda janela/modelo.
- Identificação de pedido de alteração usa verbos comuns em português/inglês. O digest, a revisão e os resultados de validação protegem os casos reconhecidos; não provam que qualquer solicitação em linguagem natural foi cumprida semanticamente. Um teste que passa também depende da qualidade do teste escolhido.
- A adaptação de contexto conserva os limites de tempo, etapas e resultados. Não concede aprovação para ações. Leitura síncrona em andamento pode aguardar timeout antes de liberar a conexão, mesmo com o prazo atingido.
- Chaves de provedores conhecidos na sessão são mascaradas. Outros segredos presentes no código não são identificados automaticamente. Os arquivos contêm código e devem permanecer fora do Git.

## Validação

Os testes específicos estão em [test_model_flow_fixes.py](../tests/test_model_flow_fixes.py), complementados pelas suítes de contexto, workflow, streaming, provedores, memória, diagnóstico e TUI. O workflow completo existente executa alterações reais em arquivos temporários, recebe aprovação, roda verificação com assert, corrige uma implementação que falhou e revalida antes de concluir.

A validação usa repositórios temporários, HTTP simulado e a aplicação Textual em modo de teste. Não houve inferência contra servidor Ollama real ou APIs pagas; essas integrações ainda precisam de verificação com a configuração de produção.

Validação local: **510 testes aprovados na suíte completa**, mais uma regressão adicional de negociação de usage após duas falhas transitórias (**511 cenários verificados**). A revisão final de provedores, diagnóstico e novas regressões passou em conjunto. Ruff, formatação e verificação de diff aprovados em Python 3.12.
