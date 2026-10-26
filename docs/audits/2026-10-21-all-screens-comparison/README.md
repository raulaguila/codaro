# Comparação completa da CLI com o protótipo aprovado

Análise de 21/10/2026. A base visual está próxima da proposta no chat principal, mas a adoção completa ainda não terminou. As maiores diferenças estão nos formulários, recuperação, ajuda/status e transições entre estados. Há também problemas funcionais de retomada e comunicação do contexto.

## Escopo e limites

Foram mapeados os **46 estados**, com **92 capturas de referência** da aplicação Textual em 120×35 e 80×24, mais duas capturas de pergunta pausada. A verificação adicional cobre Ctrl+P, gerenciamento de provedores, fim de ferramenta, retomada de decisão e resize com pergunta/plano (seis capturas). São **100 capturas nativas**, não imagens geradas por IA.

O modelo, os eventos e o catálogo HTTP são sintéticos, em diretórios temporários. O objetivo é verificar renderização e interação da UI, sem inferência real, custos ou credenciais reais. Onde não há fluxo equivalente, a captura mostra a tela existente mais próxima e a matriz o declara. A pergunta de escopo é uma demonstração da capacidade do componente, não prova da recuperação automática. As maquetes SVG são a referência visual; comportamento foi comparado também com core.js, ui.js e README do protótipo. Não houve alteração da aplicação nem do protótipo.

Foi usado o renderizador real do Textual em modo headless. Não substitui conferência de fontes, mouse e teclas Meta num terminal físico ou inspeção do protótipo em navegador. Variações inevitáveis de fonte/grade não foram classificadas como bugs.

## Resultado

**10 aderentes**; **32 parciais**; **4 ausentes na forma prevista**. A classificação considera o comportamento e a estrutura; não é uma porcentagem de semelhança visual.

Verificações específicas: **16/21 atenderam ao esperado**. Falhas de expectativa: `(120, 35)/fallback-labelled`, `(80, 24)/fallback-labelled`, `question-decision-restored`, `resize-short-layout`, `mode-available-during-execution`. Dois resultados são o mesmo problema de fallback nos dois tamanhos.

Testes existentes da implementação: **9 passaram** em test_prototype_fidelity.py. Referência do protótipo: **37 verificações passaram, 46 estados renderizados**. Esses testes não garantem paridade visual completa — as divergências desta auditoria mostram essa limitação.

Abra a [galeria comparativa](gallery.html) para escolher qualquer tela, alternar o tamanho nativo e consultar a diferença lado a lado. [Matriz estruturada](matrix.json), [interações](interactions.json), [interações adicionais](extra-interactions.json) e [geometria](observations.json) preservam a evidência.

## O que já está bem resolvido

- Início configurado limpo e chat com largura total.
- Pergunta, escolhas e único input no mesmo componente.
- Etapas e raciocínio recolhíveis, separados da resposta em streaming/final.
- Plano fixo junto ao input, com progresso e recolhimento.
- Popover de modo com uma confirmação Aplicar e conversa ao fundo.
- Setas/Enter/Esc no menu, resposta literal com / e rascunho preservado ao cancelar nova conversa.
- Hover do indicador de modo não alterou dimensões no teste realizado.

## Achados e correções recomendadas

### F01 · P1 · Decisões intermediárias desaparecem da conversa retomada

interactions_extra.py reproduz pergunta → resposta /api/v2 → resposta final sem repetir a decisão → restore_session. O texto da decisão não reaparece. finish salva somente active_question e resposta final; restore_session reconstrói esses dois itens. Isso compromete a conferência das restrições assumidas pelo agente.

**Recomendação:** Persistir eventos visíveis de pergunta/decisão e restaurá-los em ordem; manter separado o histórico de protocolo enviado ao modelo. Não basta pedir ao modelo que repita a decisão.

**Origem:** src/codaro/ui/app.py:1130, src/codaro/ui/app.py:1360, src/codaro/ui/app.py:852.

### F02 · P1 · Orçamento de fallback parece contexto confirmado

Com modelo sem metadados, a lista informa ? tokens, mas o detalhe mostra Contexto: 16,384. Nenhuma indicação visível explica que o valor é conservador; a origem fica em tooltip. Os dois testes fallback-labelled falharam.

**Recomendação:** Exibir Contexto não informado pela API e Orçamento provisório: 16k. Disponibilizar origem e ajustes em detalhes acessíveis por teclado.

**Origem:** src/codaro/ui/providers.py:389.

### F03 · P1 · Redimensionamento deixa plano e pergunta ocuparem quase toda a tela

No fluxo 120×35 → 60×20 com plano e pergunta, a variante short-terminal não foi ativada no resize e a área do chat ficou quase sem altura. O input continuou visível, mas o rodapé foi cortado. Abertura direta em tamanho compacto se comporta melhor.

**Recomendação:** Recalcular os limites no resize efetivo da tela. Reservar altura mínima para conversa, input e ações; rolar pergunta/plano internamente sem esconder o que é necessário responder.

**Origem:** src/codaro/ui/app.py:252, src/codaro/ui/app.py:258, src/codaro/ui/styles.tcss:43.

### F04 · P2 · Rodapé não acompanha perguntas, comandos e execução

Pergunta com opções mantém Enter envia · Alt+Enter nova linha · / comandos. Não apresenta ↑↓ escolher e Esc pausar. O menu / já tem sua própria linha de instruções, deixando uma segunda linha genérica redundante. Pausar muda somente o status, sem rótulo de pergunta pendente.

**Recomendação:** Derivar uma única linha de ajuda do estado atual: chat, slash, pergunta, pausa ou execução. Atualizar também o rótulo do componente de pergunta.

**Origem:** src/codaro/ui/app.py:295, src/codaro/ui/app.py:394, src/codaro/ui/questions.py.

### F05 · P2 · Menu / difere na prioridade e no estado vazio

A lista começa por settings, integrations, undo-turn e redo; o protótipo começa por modelos/modo/provedores/plano. Em /xyz o menu fecha silenciosamente. Navegação por setas, Enter e Esc passou nos dois tamanhos.

**Recomendação:** Priorizar ações comuns, mover ações avançadas para busca/ajuda e manter uma linha Nenhum comando encontrado com Esc para fechar.

**Origem:** src/codaro/interaction.py:9, src/codaro/ui/app.py:353.

### F06 · P2 · Formulários ainda usam controles altos e hierarquia antiga

Select, Input e Checkbox do Textual usam três linhas e molduras fortes. Em 80×24, abrir Avançado pode rolar automaticamente para baixo e tirar tipo/chave do enquadramento; são campos acessíveis por rolagem, não necessariamente inacessíveis. A URL obrigatória de um provedor local divide Avançado com nome, TLS e teste.

**Recomendação:** Separar campos necessários dos opcionais; URL visível para Ollama/compatible, esconder chave em Ollama sem autenticação, reduzir bordas e altura, manter ações fixas e não deslocar o foco ao expandir.

**Origem:** src/codaro/ui/providers.py:60, src/codaro/ui/providers.py:94.

### F07 · P2 · Teste de catálogo e teste de inferência compartilham a mesma ação

Testar conexão consulta catálogo quando não há modelo e faz duas chamadas de inferência quando um modelo é escolhido no Avançado. O custo é explicado, mas o botão não muda de nome. O seletor de modelos não possui diagnóstico de ferramentas.

**Recomendação:** Manter Testar conexão para catálogo e uma ação separada Testar ferramentas e resposta com custo informado e resultado próprio, também acessível pelo seletor.

**Origem:** src/codaro/ui/providers.py:145, src/codaro/ui/providers.py:366.

### F08 · P2 · Carregamento, vazio e incompatibilidade de modelos precisam de estados próprios

Atualização mantém linhas antigas com aviso no rodapé; busca vazia deixa um retângulo em branco; embeddings são escondidos com contagem técnica. A proposta coloca feedback na lista e permite entender por que um item está indisponível.

**Recomendação:** Criar estados da lista e detalhes progressivos; mostrar incompatíveis desabilitados com motivo. Manter seleção existente e impedir ativação durante consulta.

**Origem:** src/codaro/ui/providers.py:352, src/codaro/ui/providers.py:443.

### F09 · P2 · Ajuda e status poluem o histórico

Ambos montam Static com classe question na conversa, parecendo texto do usuário. /status expõe None, False, IDs e caminho de sessão junto a dezenas de detalhes.

**Recomendação:** Usar modais curtos, com chave/valor e Avançado para diagnóstico. Fechar deve voltar ao rascunho e posição de leitura.

**Origem:** src/codaro/ui/app.py:693, src/codaro/ui/app.py:798, src/codaro/ui/app.py:823.

### F10 · P2 · Revisar plano e preparar o próximo modo ainda não seguem o contrato

/plan muda para Planejar; não abre a revisão do plano existente. Plano pronto expõe Executar plano, mas não Ajustar o plano. open_mode_picker retorna enquanto busy, embora a proposta permita preparar o próximo pedido.

**Recomendação:** Definir uma entrada explícita para revisar/ajustar o plano e permitir escolha de modo futuro sem alterar a tarefa em andamento.

**Origem:** src/codaro/ui/app.py:305, src/codaro/ui/app.py:639, src/codaro/ui/plan.py:63.

### F11 · P2 · Diff, erros e finalização usam padrões diferentes

Diff usa fundo monokai, grande área vazia e botões Aplicar/Rejeitar/Voltar; recuperação usa botões altos e dados técnicos no texto principal; conclusão depende apenas do Markdown gerado. São composições distintas do mesmo fluxo.

**Recomendação:** Unificar superfície, ações e tipografia; detalhes de erro recolhíveis; recibo final derivado dos resultados reais, com Ver alterações. Preservar o foco seguro atual em Voltar/Rejeitar.

**Origem:** src/codaro/ui/reviews.py:48, src/codaro/ui/recovery.py:25, src/codaro/ui/app.py:1295.

### F12 · P2 · Preferências, onboarding e nova conversa permanecem densos

Falta controle Mostrar resumos curtos; retomada é automática em vez de oferecida. Primeiro acesso abre formulário diretamente. Nova conversa inclui objetivo opcional e explicação extensa.

**Recomendação:** Adotar as versões curtas aprovadas e deslocar limites para Avançado; não perder informações sobre descarte de propostas e cancelamento do plano.

**Origem:** src/codaro/ui/preferences.py:35, src/codaro/ui/app.py:232, src/codaro/ui/dialogs.py:23.

### F13 · P2 · Alguns estados dependem do modelo ou não estão integrados

Pergunta de escopo pode ser renderizada pela tool, mas a recuperação de contexto não dispara esse fluxo automaticamente. Busca sem resultados e recibo de conclusão não possuem ações estruturadas. /compact reduz histórico local e imprime caracteres; não reproduz a transição aprovada completa.

**Recomendação:** Conectar recuperação/escopo e ações de acompanhamento a eventos reais. Exigir confirmação humana somente quando houver uma decisão necessária; não fabricar confirmação de validação.

**Origem:** src/codaro/ui/app.py:789, src/codaro/ui/streaming.py:28, src/codaro/ui/recovery.py:39.

### F14 · P3 · Refino visual e orientações ainda divergem

Há camadas extras nos detalhes de raciocínio, botão de modo sempre com fundo, cores fortes no diff, contexto em porcentagem sem numerador e abreviação cortada de /status em 80 colunas. A política auto aparece no cabeçalho em vez da linha de estado.

**Recomendação:** Padronizar recuos, reduzir uma camada de títulos, tornar números compactos e manter informação de orçamento acessível sem tooltip. Não buscar identidade de pixels entre HTML e grade do terminal.

**Origem:** src/codaro/ui/styles.tcss, src/codaro/ui/streaming.py:21, src/codaro/ui/app.py:285.

## Matriz de todas as telas

Aderente = estrutura principal corresponde; ainda pode haver diferenças pontuais. Parcial = tela existe com diferenças relevantes. Ausente na forma prevista = o comando/capacidade pode existir, mas a apresentação/entrada aprovada não foi implementada.

| Tela | Avaliação | Prioridade | Diferença e evidência |
| --- | --- | --- | --- |
| Início com modelo configurado (`welcome`) | Aderente | P3 | Corpo limpo, cabeçalho compacto e campo pronto. Refinar somente alinhamento e respiro. [CLI](screens/welcome-120x35.svg) · [80×24](screens/welcome-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/welcome.svg) |
| Primeiro acesso (`first-run`) | Parcial | P2 | Abre diretamente o cadastro; falta a introdução breve com a única ação Conectar provedor. [CLI](screens/first-run-120x35.svg) · [80×24](screens/first-run-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/first-run.svg) |
| Menu / com navegação (`slash`) | Parcial | P2 | Setas, Enter e Esc funcionam. Lista começa por opções avançadas e repete instruções no rodapé; o protótipo prioriza ações frequentes. [CLI](screens/slash-120x35.svg) · [80×24](screens/slash-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/slash.svg) |
| Comandos filtrados (`slash-filter`) | Aderente | P3 | Filtro /mo, navegação e Enter abrem o seletor destacado; Tab permite completar. [CLI](screens/slash-filter-120x35.svg) · [80×24](screens/slash-filter-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/slash-filter.svg) |
| Comando não encontrado (`slash-empty`) | Ausente na forma prevista | P2 | Sem correspondências, a lista desaparece. Falta o estado Nenhum comando encontrado junto ao input; o comando desconhecido é tratado localmente. [CLI](screens/slash-empty-120x35.svg) · [80×24](screens/slash-empty-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/slash-empty.svg) |
| Modelo analisando (`thinking`) | Parcial | P2 | Uma seção Etapas com resumo. Analisando o projeto é genérico; não informa a etapa investigada com a precisão do protótipo. [CLI](screens/thinking-120x35.svg) · [80×24](screens/thinking-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/thinking.svg) |
| Ferramenta em execução (`tool-running`) | Aderente | P3 | Ação atual e última ação concluída têm pouco peso, com detalhes recolhidos. [CLI](screens/tool-running-120x35.svg) · [80×24](screens/tool-running-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/tool-running.svg) |
| Atividade e raciocínio expandidos (`stages-expanded`) | Parcial | P2 | Atividade e raciocínio estão separados. Há uma camada extra de Collapsible por geração; reduzir o excesso de títulos e recuos. [CLI](screens/stages-expanded-120x35.svg) · [80×24](screens/stages-expanded-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/stages-expanded.svg) |
| Resposta em streaming (`streaming`) | Aderente | P3 | Conteúdo incremental aparece com identificação Codaro e fora dos detalhes de raciocínio. [CLI](screens/streaming-120x35.svg) · [80×24](screens/streaming-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/streaming.svg) |
| Resposta final (`answer`) | Aderente | P3 | Resposta final ocupa sua própria área; não fica no card de pensamento. [CLI](screens/answer-120x35.svg) · [80×24](screens/answer-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/answer.svg) |
| Conversa com várias mensagens (`conversation`) | Parcial | P2 | Turnos têm rótulos e hierarquia, mas faltam resumo compacto de atividades passadas e espaçamento padronizado entre turnos. [CLI](screens/conversation-120x35.svg) · [80×24](screens/conversation-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/conversation.svg) |
| Pergunta com opções (`ask-choice`) | Parcial | P1 | Pergunta, opções e input compartilham componente. Rodapé continua genérico; decisão não reaparece no histórico após retomar a sessão. [CLI](screens/ask-choice-120x35.svg) · [80×24](screens/ask-choice-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/ask-choice.svg) |
| Pergunta com resposta livre (`ask-text`) | Parcial | P2 | Usa o mesmo input e permite texto livre. Falta orientação contextual de envio/pausa e estado visual específico de pergunta pausada. [CLI](screens/ask-text-120x35.svg) · [80×24](screens/ask-text-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/ask-text.svg) |
| Resposta do usuário e continuação (`ask-followup`) | Parcial | P1 | Decisão aparece ao enviar, mas não é identificada como Você · decisão e não é restaurada com a sessão. [CLI](screens/ask-followup-120x35.svg) · [80×24](screens/ask-followup-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/ask-followup.svg) |
| Plano para revisar (`plan`) | Parcial | P2 | Plano pronto tem Executar plano no painel fixo. Faltam a apresentação para revisão e a ação Ajustar o plano do protótipo; /plan troca de modo. [CLI](screens/plan-120x35.svg) · [80×24](screens/plan-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/plan.svg) |
| Plano de execução recolhido (`plan-collapsed`) | Aderente | P3 | Progresso e etapa atual continuam visíveis junto ao input com lista recolhida. [CLI](screens/plan-collapsed-120x35.svg) · [80×24](screens/plan-collapsed-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/plan-collapsed.svg) |
| Plano de execução expandido (`plan-expanded`) | Aderente | P3 | Lista fixa, estados por etapa e rolagem própria. Cabeçalho pode truncar nomes longos. [CLI](screens/plan-expanded-120x35.svg) · [80×24](screens/plan-expanded-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/plan-expanded.svg) |
| Plano fixo durante a conversa (`plan-chat`) | Aderente | P3 | Plano fica fora da rolagem da conversa e junto ao input; convive com múltiplos turnos. [CLI](screens/plan-chat-120x35.svg) · [80×24](screens/plan-chat-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/plan-chat.svg) |
| Aprovação de alterações (`review-diff`) | Parcial | P2 | Revisão explícita funciona. Diff usa monokai, modal muito alto e ações em ordem/ênfase diferentes; falta agrupamento visual por arquivo e contagem de linhas. [CLI](screens/review-diff-120x35.svg) · [80×24](screens/review-diff-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/review-diff.svg) |
| Aprovação de comando (`command-approval`) | Aderente | P3 | Comando, diretório, timeout e escopo da autorização são claros. Esc/rejeição são seguros. [CLI](screens/command-approval-120x35.svg) · [80×24](screens/command-approval-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/command-approval.svg) |
| Validação em andamento (`validating`) | Parcial | P2 | Execução dos testes aparece como atividade. Falta uma apresentação consistente de validação e vínculo visível com os resultados ao concluir. [CLI](screens/validating-120x35.svg) · [80×24](screens/validating-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/validating.svg) |
| Tarefa concluída (`done`) | Parcial | P2 | Finalização existe, mas recibo e atalhos Ver alterações/Nova conversa não são um componente estruturado. Mostrar somente validações realmente realizadas. [CLI](screens/done-120x35.svg) · [80×24](screens/done-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/done.svg) |
| Cadastro de provedor (`provider`) | Parcial | P2 | BYOK e teste existem. Select/Input de três linhas, formulário alto e três botões deixam a composição mais pesada que a proposta. [CLI](screens/provider-120x35.svg) · [80×24](screens/provider-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/provider.svg) |
| OpenAI compatible e TLS (`provider-custom`) | Parcial | P2 | URL e TLS disponíveis, porém dentro do Avançado junto a campos secundários. Falta explicar ao lado da opção que a identidade do servidor não será verificada. [CLI](screens/provider-custom-120x35.svg) · [80×24](screens/provider-custom-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/provider-custom.svg) |
| Ollama local (`provider-ollama`) | Parcial | P2 | URL local configurável e chave opcional. A chave permanece como campo visível; URL e perfil/teste avançado disputam espaço. [CLI](screens/provider-ollama-120x35.svg) · [80×24](screens/provider-ollama-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/provider-ollama.svg) |
| Teste de conexão em andamento (`provider-testing`) | Parcial | P2 | Há progresso, bloqueio dos campos e cancelamento. Usa o campo de erro também como status e muda o significado de Testar conexão conforme o modelo escolhido. [CLI](screens/provider-testing-120x35.svg) · [80×24](screens/provider-testing-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/provider-testing.svg) |
| Conexão verificada (`provider-success`) | Parcial | P2 | Resultado do catálogo é explícito, mas abre Avançado e aumenta densidade; separar sucesso de catálogo e teste opcional de inferência. [CLI](screens/provider-success-120x35.svg) · [80×24](screens/provider-success-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/provider-success.svg) |
| Erro ao testar conexão (`provider-error`) | Parcial | P2 | Erro mantém dados e permite tentar novamente. Texto técnico está no mesmo componente dos estados de sucesso/progresso, sem hierarquia própria. [CLI](screens/provider-error-120x35.svg) · [80×24](screens/provider-error-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/provider-error.svg) |
| Busca e seleção de modelo (`models`) | Parcial | P2 | Busca e teclado existem. Select/Input grandes e números sem compactação; incompatíveis são ocultados, em vez de aparecerem desabilitados com motivo. [CLI](screens/models-120x35.svg) · [80×24](screens/models-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/models.svg) |
| Atualização do catálogo (`models-loading`) | Parcial | P2 | Seleção é preservada e controles bloqueiam. Catálogo anterior continua parecendo normal; carregamento aparece só no rodapé do modal. [CLI](screens/models-loading-120x35.svg) · [80×24](screens/models-loading-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/models-loading.svg) |
| Catálogo sem resultados (`models-empty`) | Parcial | P2 | Usar modelo fica desabilitado corretamente. Mensagem fica abaixo de uma área vazia, em vez de ocupar a própria lista. [CLI](screens/models-empty-120x35.svg) · [80×24](screens/models-empty-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/models-empty.svg) |
| Contexto não informado pela API (`model-fallback`) | Parcial | P1 | Lista mostra ? tokens, mas detalhe apresenta Contexto: 16,384 sem identificar fallback. Origem só no tooltip, pouco acessível pelo teclado. [CLI](screens/model-fallback-120x35.svg) · [80×24](screens/model-fallback-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/model-fallback.svg) |
| Diagnóstico de ferramentas (`model-tools`) | Ausente na forma prevista | P2 | Não há ação nem resultado do teste de ferramentas no seletor; essa capacidade existe no Avançado do cadastro de provedores. [CLI](screens/model-tools-120x35.svg) · [80×24](screens/model-tools-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/model-tools.svg) |
| Preferências (`preferences`) | Parcial | P2 | Salva modo, retomada e limites. Falta Mostrar resumos curtos; Retomar automaticamente difere de Oferecer retomada. [CLI](screens/preferences-120x35.svg) · [80×24](screens/preferences-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/preferences.svg) |
| Escolha do modo (`mode`) | Aderente | P3 | Popover ancorado, conversa ao fundo, três modos e uma ação Aplicar. Preparar próximo modo enquanto executa ainda é bloqueado. [CLI](screens/mode-120x35.svg) · [80×24](screens/mode-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/mode.svg) |
| Aprovação de edições (`edit-approval`) | Parcial | P2 | Política recolhível e confirmação explícita existem. Estado auto vai para o cabeçalho, enquanto o protótipo o coloca na linha de estado. [CLI](screens/edit-approval-120x35.svg) · [80×24](screens/edit-approval-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/edit-approval.svg) |
| Detalhes da sessão e contexto (`status`) | Ausente na forma prevista | P2 | Comando despeja diagnóstico no histórico do chat. Falta modal chave/valor com detalhes progressivos e Fechar. [CLI](screens/status-120x35.svg) · [80×24](screens/status-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/status.svg) |
| Ajuda e atalhos (`help`) | Ausente na forma prevista | P2 | Ajuda é um bloco longo no histórico; faltam modal curto e prioridade aos atalhos da interação atual. [CLI](screens/help-120x35.svg) · [80×24](screens/help-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/help.svg) |
| Reorganização automática do contexto (`context-compacting`) | Parcial | P2 | Há eventos de compactação automática e /compact local. Falta a apresentação transitória completa da proposta; /compact mostra contagem de caracteres no chat. [CLI](screens/context-compacting-120x35.svg) · [80×24](screens/context-compacting-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/context-compacting.svg) |
| Conexão interrompida (`connection-error`) | Parcial | P2 | Recuperação oferece Repetir e Configurar. Falta explicar preservação do rascunho e oferecer Trocar modelo nesse estado; botões são grandes. [CLI](screens/connection-error-120x35.svg) · [80×24](screens/connection-error-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/connection-error.svg) |
| Credencial inválida (`auth-error`) | Parcial | P2 | Direciona à configuração, mas mostra identificador técnico do provedor e diagnóstico sem separar detalhes; rodapé retorna Pronto apesar da falha. [CLI](screens/auth-error-120x35.svg) · [80×24](screens/auth-error-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/auth-error.svg) |
| Cancelamento com progresso preservado (`cancelled`) | Parcial | P2 | Distingue cancelamento de erro e mantém plano. Há apenas Repetir no card; faltam Revisar e retomar e Ver alterações. [CLI](screens/cancelled-120x35.svg) · [80×24](screens/cancelled-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/cancelled.svg) |
| Retomada da sessão (`resumed`) | Parcial | P1 | Retoma mensagem inicial/final e plano persistido, mas decisões intermediárias da pergunta somem da conversa restaurada. [CLI](screens/resumed-120x35.svg) · [80×24](screens/resumed-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/resumed.svg) |
| Busca sem resultados (`no-results`) | Parcial | P2 | Resposta textual orienta o usuário. Atalhos Buscar pelo fluxo/Indicar arquivo dependem de texto gerado; não existem como ações do componente. [CLI](screens/no-results-120x35.svg) · [80×24](screens/no-results-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/no-results.svg) |
| Escolha de escopo para continuar (`context-scope`) | Parcial | P2 | O componente de pergunta comporta escolhas de escopo; não há acionamento automático desse fluxo quando o contexto não cabe. Captura é demonstração do componente, não recuperação integrada. [CLI](screens/context-scope-120x35.svg) · [80×24](screens/context-scope-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/context-scope.svg) |
| Nova conversa sem perder rascunho (`new-session`) | Parcial | P2 | Confirmação preserva rascunho ao cancelar e explica fim do plano. Texto denso e campo Objetivo extra destoam da confirmação curta aprovada. [CLI](screens/new-session-120x35.svg) · [80×24](screens/new-session-80x24.svg) · [Protótipo](../../design/ui-prototype/screens/new-session.svg) |

## Sequência sugerida

1. **Confiança e legibilidade:** persistência de decisões, fallback explícito e resize com plano/pergunta (F01–F03).
2. **Interação principal:** rodapé contextual, slash vazio/prioridades, pausa e revisão/ajuste do plano (F04, F05, F10).
3. **Configuração:** formulários compactos, testes separados, estados do seletor e preferências (F06–F08, F12).
4. **Consistência do fluxo:** ajuda/status em modal, recuperação, recibo real e refinos (F09, F11, F13, F14).

Critério de aceite: comparar novamente os 46 estados; manter chat, input e ações utilizáveis em 80×24 e no resize para 60×20; retomar decisões sem perda; nenhum valor provisório aparentar metadado confirmado; nenhum resultado de teste ser inventado. A reprodução de cada estado deve continuar declarando quando usa fixtures.

## Reproduzir

```bash
.venv/bin/python docs/audits/2026-10-21-all-screens-comparison/reproduce.py
.venv/bin/python docs/audits/2026-10-21-all-screens-comparison/interactions_extra.py
.venv/bin/python docs/audits/2026-10-21-all-screens-comparison/build_report.py
.venv/bin/pytest -q tests/test_prototype_fidelity.py
node docs/design/ui-prototype/validate.mjs
```

A captura de carregamento solta o transporte simulado e aguarda sua conclusão antes de desmontar a aplicação. Uma primeira execução do harness desmontou antes do fim da atualização e produziu NoMatches no finally; isso não foi tratado como falha comprovada no fluxo do usuário. A reprodução final, com o worker concluído, não tem erros de captura.
