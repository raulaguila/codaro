# Correções da comparação completa de UI/UX

Os 14 achados da comparação foram tratados. A galeria apresenta os 46 estados na versão corrigida. A comparação anterior foi preservada; não alteramos o desenho do protótipo para fazer a implementação parecer aderente.

[Galeria das 46 telas após as correções](gallery.html) · [Auditoria anterior](../2026-10-21-all-screens-comparison/README.md) · [Matriz JSON](matrix.json)

## Verificação

- **21/21 checks específicos passaram**, incluindo as cinco expectativas antes não atendidas.
- **100 capturas nativas**: 46 estados em 120×35 e 80×24, duas perguntas pausadas e seis capturas complementares.
- JSON, XML e referências das 46 telas verificados; JavaScript da galeria validado.
- A suíte completa, lint e empacotamento estão registrados em [validation.txt](validation.txt).

O renderizador é o Textual real em modo headless. Eventos, resultados e HTTP são sintéticos, sem API key real, inferência remota ou custo. A tela de diagnóstico de ferramentas percorre um protocolo simulado de chamada → resultado → resposta final. O recibo de conclusão usa um registro de validação sintético para demonstrar o componente; essa captura não representa testes executados num repositório real. A pergunta de escopo da galeria ilustra o componente; o gatilho por falha de capacidade é testado em test_ui_parity_fixes.py.

Isso verifica os fluxos e a composição nativa, sem prometer identidade de pixels entre HTML e terminal. Fontes, leitores de tela e entrega de teclas Command em terminais físicos precisam de conferência nesses ambientes.

## Correções entregues

| Achado | Correção | Captura |
| --- | --- | --- |
| F01 | Decisões persistidas imediatamente, redigidas como decisões e restauradas; JSON antigo continua compatível. Metadados visuais não são enviados como protocolo de ferramentas. | [Ver](screens/ask-followup-120x35.svg) |
| F02 | Fallback identificado como orçamento provisório; origem e janela manual acessíveis nos detalhes do modelo. | [Ver](screens/model-fallback-120x35.svg) |
| F03 | Resize recalcula a variante compacta; plano/pergunta rolam internamente e preservam chat, input e rodapé. | [Ver](screens/ask-choice-120x35.svg) |
| F04 | Rodapé contextual, pergunta pausada identificada e botão Enviar desabilitado quando não há resposta/opção. | [Ver](screens/ask-text-120x35.svg) |
| F05 | Comandos frequentes primeiro, navegação por setas/Enter/Tab/Esc e estado vazio persistente no menu. | [Ver](screens/slash-empty-120x35.svg) |
| F06 | Campos compactos, URL visível para endpoints locais/compatíveis, autenticação opcional do Ollama e aviso TLS junto à opção. | [Ver](screens/provider-custom-120x35.svg) |
| F07 | Teste de catálogo separado de inferência; teste completo também no seletor, sem ativar o modelo nem salvar o perfil. | [Ver](screens/model-tools-120x35.svg) |
| F08 | Lista tem carregamento e vazio próprios; modelos incompatíveis permanecem visíveis e desabilitados. | [Ver](screens/models-empty-120x35.svg) |
| F09 | Ajuda/status são modais com campos legíveis e diagnóstico em Avançado, sem adicionar mensagens ao chat. | [Ver](screens/status-120x35.svg) |
| F10 | Revisão do plano no chat, Ajustar/Executar com revisão e modo preparado para o próximo pedido sem alterar a execução atual. | [Ver](screens/plan-120x35.svg) |
| F11 | Diff com cores do tema e contagem por arquivo; erros com detalhes recolhidos e ações próprias; recibo baseado nas validações da revisão atual. | [Ver](screens/done-120x35.svg) |
| F12 | Primeiro acesso limpo, controle de resumos, oferta de retomada e nova conversa curta, preservando a sessão anterior. | [Ver](screens/first-run-120x35.svg) |
| F13 | Falha de capacidade oferece escopo no input, com uma nova tentativa delimitada; busca vazia oferece ações; compactação preserva o histórico completo salvo. | [Ver](screens/context-scope-120x35.svg) |
| F14 | Menos títulos de raciocínio, contexto usado/orçamento compacto, recuos e cores consistentes, política de edições junto ao input. | [Ver](screens/stages-expanded-120x35.svg) |

A revisão do plano fica na conversa, mantendo o acompanhamento fixo junto ao input. Executar com revisão inicia a tarefa e continua respeitando as permissões; Ajustar seleciona Planejar. Uma seleção de modo durante geração é aplicada somente ao próximo pedido; uma ação explícita posterior de executar/ajustar o plano prevalece sobre essa seleção pendente.

A nova conversa recebe uma sessão independente. A anterior continua disponível em /sessions e /session ID. Cancelar a confirmação preserva o rascunho. O formato anterior de session.json continua aceito; decisões e decisões pendentes usam metadados locais limitados e redigidos com a mesma proteção de credenciais do restante da sessão.

A recuperação de contexto pode pedir escopo após esgotar a recuperação do agente. Uma resposta permite uma tentativa com escopo delimitado. Se instruções/ferramentas ainda não couberem, ficam disponíveis configuração e troca de modelo, evitando repetição infinita de perguntas. Não é possível prometer que qualquer tarefa caberá em qualquer modelo.

## Todas as telas

| Estado | Captura normal | Compacta | Referência |
| --- | --- | --- | --- |
| Início com modelo configurado | [120×35](screens/welcome-120x35.svg) | [80×24](screens/welcome-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/welcome.svg) |
| Primeiro acesso | [120×35](screens/first-run-120x35.svg) | [80×24](screens/first-run-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/first-run.svg) |
| Menu / com navegação | [120×35](screens/slash-120x35.svg) | [80×24](screens/slash-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/slash.svg) |
| Comandos filtrados | [120×35](screens/slash-filter-120x35.svg) | [80×24](screens/slash-filter-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/slash-filter.svg) |
| Comando não encontrado | [120×35](screens/slash-empty-120x35.svg) | [80×24](screens/slash-empty-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/slash-empty.svg) |
| Modelo analisando | [120×35](screens/thinking-120x35.svg) | [80×24](screens/thinking-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/thinking.svg) |
| Ferramenta em execução | [120×35](screens/tool-running-120x35.svg) | [80×24](screens/tool-running-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/tool-running.svg) |
| Atividade e raciocínio expandidos | [120×35](screens/stages-expanded-120x35.svg) | [80×24](screens/stages-expanded-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/stages-expanded.svg) |
| Resposta em streaming | [120×35](screens/streaming-120x35.svg) | [80×24](screens/streaming-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/streaming.svg) |
| Resposta final | [120×35](screens/answer-120x35.svg) | [80×24](screens/answer-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/answer.svg) |
| Conversa com várias mensagens | [120×35](screens/conversation-120x35.svg) | [80×24](screens/conversation-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/conversation.svg) |
| Pergunta com opções | [120×35](screens/ask-choice-120x35.svg) | [80×24](screens/ask-choice-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/ask-choice.svg) |
| Pergunta com resposta livre | [120×35](screens/ask-text-120x35.svg) | [80×24](screens/ask-text-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/ask-text.svg) |
| Resposta do usuário e continuação | [120×35](screens/ask-followup-120x35.svg) | [80×24](screens/ask-followup-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/ask-followup.svg) |
| Plano para revisar | [120×35](screens/plan-120x35.svg) | [80×24](screens/plan-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/plan.svg) |
| Plano de execução recolhido | [120×35](screens/plan-collapsed-120x35.svg) | [80×24](screens/plan-collapsed-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/plan-collapsed.svg) |
| Plano de execução expandido | [120×35](screens/plan-expanded-120x35.svg) | [80×24](screens/plan-expanded-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/plan-expanded.svg) |
| Plano fixo durante a conversa | [120×35](screens/plan-chat-120x35.svg) | [80×24](screens/plan-chat-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/plan-chat.svg) |
| Aprovação de alterações | [120×35](screens/review-diff-120x35.svg) | [80×24](screens/review-diff-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/review-diff.svg) |
| Aprovação de comando | [120×35](screens/command-approval-120x35.svg) | [80×24](screens/command-approval-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/command-approval.svg) |
| Validação em andamento | [120×35](screens/validating-120x35.svg) | [80×24](screens/validating-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/validating.svg) |
| Tarefa concluída | [120×35](screens/done-120x35.svg) | [80×24](screens/done-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/done.svg) |
| Cadastro de provedor | [120×35](screens/provider-120x35.svg) | [80×24](screens/provider-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/provider.svg) |
| OpenAI compatible e TLS | [120×35](screens/provider-custom-120x35.svg) | [80×24](screens/provider-custom-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/provider-custom.svg) |
| Ollama local | [120×35](screens/provider-ollama-120x35.svg) | [80×24](screens/provider-ollama-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/provider-ollama.svg) |
| Teste de conexão em andamento | [120×35](screens/provider-testing-120x35.svg) | [80×24](screens/provider-testing-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/provider-testing.svg) |
| Conexão verificada | [120×35](screens/provider-success-120x35.svg) | [80×24](screens/provider-success-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/provider-success.svg) |
| Erro ao testar conexão | [120×35](screens/provider-error-120x35.svg) | [80×24](screens/provider-error-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/provider-error.svg) |
| Busca e seleção de modelo | [120×35](screens/models-120x35.svg) | [80×24](screens/models-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/models.svg) |
| Atualização do catálogo | [120×35](screens/models-loading-120x35.svg) | [80×24](screens/models-loading-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/models-loading.svg) |
| Catálogo sem resultados | [120×35](screens/models-empty-120x35.svg) | [80×24](screens/models-empty-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/models-empty.svg) |
| Contexto não informado pela API | [120×35](screens/model-fallback-120x35.svg) | [80×24](screens/model-fallback-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/model-fallback.svg) |
| Diagnóstico de ferramentas | [120×35](screens/model-tools-120x35.svg) | [80×24](screens/model-tools-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/model-tools.svg) |
| Preferências | [120×35](screens/preferences-120x35.svg) | [80×24](screens/preferences-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/preferences.svg) |
| Escolha do modo | [120×35](screens/mode-120x35.svg) | [80×24](screens/mode-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/mode.svg) |
| Aprovação de edições | [120×35](screens/edit-approval-120x35.svg) | [80×24](screens/edit-approval-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/edit-approval.svg) |
| Detalhes da sessão e contexto | [120×35](screens/status-120x35.svg) | [80×24](screens/status-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/status.svg) |
| Ajuda e atalhos | [120×35](screens/help-120x35.svg) | [80×24](screens/help-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/help.svg) |
| Reorganização automática do contexto | [120×35](screens/context-compacting-120x35.svg) | [80×24](screens/context-compacting-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/context-compacting.svg) |
| Conexão interrompida | [120×35](screens/connection-error-120x35.svg) | [80×24](screens/connection-error-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/connection-error.svg) |
| Credencial inválida | [120×35](screens/auth-error-120x35.svg) | [80×24](screens/auth-error-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/auth-error.svg) |
| Cancelamento com progresso preservado | [120×35](screens/cancelled-120x35.svg) | [80×24](screens/cancelled-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/cancelled.svg) |
| Retomada da sessão | [120×35](screens/resumed-120x35.svg) | [80×24](screens/resumed-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/resumed.svg) |
| Busca sem resultados | [120×35](screens/no-results-120x35.svg) | [80×24](screens/no-results-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/no-results.svg) |
| Escolha de escopo para continuar | [120×35](screens/context-scope-120x35.svg) | [80×24](screens/context-scope-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/context-scope.svg) |
| Nova conversa sem perder rascunho | [120×35](screens/new-session-120x35.svg) | [80×24](screens/new-session-80x24.svg) | [Protótipo](../../design/ui-prototype/screens/new-session.svg) |

## Reproduzir

```bash
.venv/bin/python docs/audits/2026-10-21-ui-parity-fixes/reproduce.py
.venv/bin/python docs/audits/2026-10-21-ui-parity-fixes/interactions_extra.py
.venv/bin/python docs/audits/2026-10-21-ui-parity-fixes/build_report.py
.venv/bin/pytest -q
```
