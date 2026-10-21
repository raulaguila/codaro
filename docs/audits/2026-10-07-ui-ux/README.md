# Auditoria de UI e UX do Codaro

Revisão de 7 de outubro de 2026 do commit `aa72a222f67424ce2cfa557dcceb1b8af1bbce1e`, branch `develop`. O chat, o streaming e as aprovações têm uma base funcional consistente. As prioridades são tornar clara a semântica de limpar a conversa, preservar a posição de leitura, adaptar os formulários ao terminal e tornar os fluxos de configuração e recuperação acessíveis pela interface.

Foram identificados 17 achados: uma prioridade P1, doze P2 e quatro P3. P1 indica comportamento que merece correção antes de ampliar o uso com autonomia; P2 indica impacto relevante no uso diário; P3 indica refinamento. Os achados abaixo descrevem a versão original. As correções foram implementadas e validadas; veja [a matriz de entrega](../../UI_UX.md) e [observações posteriores](after/observations.json).

## Método e cobertura

- CLI iniciado em PTY com `codaro /tmp/codaro-ui-audit-project`, em terminal 80 × 24. Inicialização e encerramento pelo teclado observados.
- A aplicação Textual real foi executada com Pilot, com teclado, cliques, troca de temas e capturas do compositor. Matriz principal: 60 × 20, 80 × 24, 120 × 35 e 160 × 50; fluxos adicionais em 100 × 30 e 120 × 50.
- Projetos e perfis temporários. Provedores controlados com `httpx.MockTransport`; nenhuma API paga ou credencial real usada.
- Fluxos: tela inicial, paleta, comandos, referências de arquivos, rascunho, múltiplas linhas, modos, tarefa, permissões, diff, aprovação de comando, cadastro, teste de conexão, seleção de modelo, erro HTTP 401, falha de conexão, modelo incompatível, espera, streaming, raciocínio, cancelamento, limpeza e rolagem.
- **121 testes passaram nesta auditoria:** 54 em TUI/provedores/interação e 67 em workflow/contexto. O script adicional registra observações sem substituir os testes da suíte.
- Evidências: [script reproduzível](reproduce.py), [50 observações e registros de geometria](observations.json), 38 SVGs exportados pela aplicação e PNGs renderizados desses SVGs. O script desativa `NO_COLOR` do runner para capturar a interface colorida.

Chamadas a servidores reais, latência/custos de provedores externos, leitura por tecnologias assistivas e comportamento dos terminais nativos de macOS/Windows não foram validados nesta sessão. Os testes existentes de atalhos de macOS passaram, mas isso não substitui uma sessão em um Mac. A matriz não estabelece suporte a terminais menores que 60 × 20.

## Achados prioritários

### U01 P1 Limpar mistura conversa propostas tarefa e permissões

**Comportamento reproduzido.** Ctrl+L apaga os turnos e rejeita propostas pendentes, mas mantém a tarefa ativa e as autorizações por tarefa. O retorno é somente “Pronto · conversa limpa”. Na reprodução, uma proposta passou a `rejected`, o arquivo permaneceu intacto e uma autorização para `src` continuou ativa. Não houve execução sem autorização; o problema é a ausência de uma explicação clara sobre o estado mantido e o estado descartado.

**Impacto:** limpar pode ser interpretado como começar uma atividade nova, embora a atividade e suas permissões anteriores continuem em vigor. Propostas desaparecem sem um resultado de revisão visível.

**Recomendação:** separar “Limpar mensagens” de “Nova conversa/tarefa”. Exibir tarefa e escopo ativos de forma acessível. Na limpeza, informar propostas descartadas e oferecer recuperação; em uma nova atividade, tratar explicitamente o reset das permissões. A ação de limpar mensagens deve ter um significado consistente e documentado.

**Aceite:** cobertura dos quatro estados — conversa comum, proposta pendente, tarefa ativa e escopo autorizado. A UI informa o efeito antes ou imediatamente depois da ação, oferece recuperação quando aplicável e uma nova tarefa não herda silenciosamente permissões.

**Código:** `src/codaro/tui.py:1354`. Evidência: `clear-rejects-pending` e `clear-keeps-grant` no JSON; [tarefa autorizada após limpar](clear-keeps-grant.png).

### U02 P2 Novas atividades interrompem a leitura do histórico

**Defeito reproduzido.** Depois de subir ao início de uma conversa longa, a chegada de uma atividade moveu `scroll_y` de **0 para 163**, o fim da conversa. `mount_message` sempre agenda `scroll_end`. O streaming já verifica se o usuário estava no fim, mas a montagem de novos elementos não segue essa regra.

**Recomendação:** aplicar a mesma política de acompanhamento a mensagens, ferramentas, prévias e planos. Quando o usuário estiver lendo acima, manter a posição e mostrar “Novas mensagens ↓”.

**Aceite:** chegada de ferramenta, raciocínio ou resposta preserva a posição de leitura; quem já estiver no fim continua acompanhando normalmente.

**Código:** `src/codaro/tui.py:1087` e `:1244`. Evidência: `scroll-follow`; [atividade recebida](scroll-tool-arrival.png).

### U03 P2 Ações do cadastro ficam fora da área inicial do formulário

**Comportamento reproduzido.** Em 80 × 24, “Cadastrar”, “Testar conexão” e “Voltar” ficam abaixo da área visível. Isso também ocorreu em 120 × 35. A navegação com Tab alcança “Testar conexão” e rola o formulário, portanto os controles são acessíveis, mas pouco descobríveis. Em 60 × 20, a descrição TLS excede a largura interna e “Voltar” na seleção de modelos fica parcialmente cortado.

**Recomendação:** manter as ações em um rodapé fixo e rolar apenas os campos. Reduzir margens verticais, quebrar textos longos e empilhar botões em larguras pequenas. Mostrar uma indicação clara de conteúdo adicional.

**Aceite:** os botões principais permanecem visíveis em 80 × 24; teclado e mouse chegam a todos os campos; nenhum rótulo ou botão corta horizontalmente em 60 × 20.

**Código:** `src/codaro/provider_ui.py:18` e `:145`. Evidências: [cadastro inicial em 80 × 24](provider-80x24.png), [depois de navegar com Tab](provider-tab-scrolled.png), [modelos em 60 × 20](models-60x20.png).

### U04 P2 O cabeçalho oculta modo e permissões em terminais estreitos

**Defeito visual reproduzido.** O cabeçalho tem altura de uma linha. Diretório, modelo, modo, aprovação e conexão competem pela mesma largura. Em 60 × 20, modo e aprovação não aparecem; em 80 colunas, o conteúdo depende do comprimento do caminho/modelo. A dica de entrada e o rodapé também cortam texto em 60 colunas.

**Recomendação:** priorizar modo e aprovação, abreviar caminho/modelo e permitir duas linhas conforme a largura. Acesso ao caminho completo por `/pwd` e tooltip pode ser mantido. Resumir as dicas em terminais estreitos.

**Aceite:** “Perguntar/Planejar/Executar” e a política de aprovação estão visíveis em 80 × 24 com nomes longos, sem reduzir excessivamente a área de chat.

**Código:** `src/codaro/tui.py:427` e `:609`. Evidência: [início em 60 × 20](welcome-60x20.png).

### U05 P2 O tema claro oferecido no menu prejudica o contraste

**Defeito reproduzido.** Ao selecionar `textual-light`, o fundo do chat continua `#0f172a`, definido no CSS, enquanto elementos herdam texto escuro do tema, como `#1f1f1f`. Na captura, mensagens genéricas ficam quase ilegíveis e os controles passam a usar fundos claros.

**Recomendação:** usar tokens de tema de forma consistente ou oferecer apenas temas validados com o layout atual. Definir pares de texto/fundo para mensagens, Markdown, avisos, formulários, botões e foco.

**Aceite:** cada tema oferecido mantém contraste adequado e foco distinguível em chat, formulários, diffs e estados desabilitados. Não apresentar a validação visual como certificação de acessibilidade.

**Código:** `src/codaro/tui.py:425` e CSS dos modais. Evidência: [tema claro](light-theme.png).

## Configuração e navegação

### U06 P2 Testar conexão perde o foco do teclado

**Defeito reproduzido.** O teste desabilita todos os campos e botões. Depois do HTTP 401, os controles são reabilitados, mas o foco é `None`, sem retornar ao botão acionado nem ao campo que precisa de correção.

**Recomendação:** guardar e restaurar o foco; em erros locais de validação, focar o campo correspondente. Manter a mensagem acessível junto ao controle e preservar valores digitados.

**Aceite:** depois de sucesso/erro, o próximo Tab e Enter têm destino previsível; a API key continua mascarada.

**Código:** `src/codaro/provider_ui.py:75`. Evidência: `provider-error-visible`; [erro 401](provider-401.png).

### U07 P2 Não há cancelamento do teste de provedor

**Comportamento reproduzido.** Durante a consulta, “Voltar” fica desabilitado e Esc mantém o modal aberto. A interface permite esperar ou sair da aplicação. O catálogo tem timeout HTTP de 15 segundos; a inferência usa o timeout configurado, cujo padrão é 90 segundos para operações HTTP, e o teste de ferramentas pode fazer duas requisições. Esses valores não constituem um prazo total para toda a verificação.

**Recomendação:** oferecer “Cancelar teste”, com descarte de resultados tardios e retorno ao formulário. Mostrar a etapa atual: catálogo, chamada de ferramenta e resposta. Cancelar o cliente em andamento quando possível.

**Aceite:** um teste lento pode ser interrompido, os campos voltam a funcionar e uma resposta antiga não altera uma configuração nova.

**Código:** `src/codaro/provider_ui.py:75` e `:109`; `src/codaro/provider.py:128`. Evidência: `provider-test-cancellation`; [estado de espera](provider-waiting.png).

### U08 P2 A paleta não concentra as ações principais do produto

**Lacuna funcional confirmada em execução.** Ctrl+P oferece dois comandos do Codaro: cadastrar provedor e selecionar provedor/modelo. Modos, tarefas, permissões, contexto, histórico e alterações dependem de descobrir os comandos `/`. O menu dá espaço a ações genéricas do framework, como Theme, Maximize e Screenshot.

**Recomendação:** incluir Perguntar, Planejar, Executar, Nova tarefa, Retomar, Revisar alterações, Permissões, Contexto e Ajuda. Exibir o comando `/` equivalente e respeitar estados de execução/aprovação.

**Aceite:** os fluxos essenciais podem ser iniciados por Ctrl+P sem memorizar comandos; o rascunho é preservado e ações indisponíveis explicam o motivo.

**Código:** `src/codaro/tui.py:465`. Evidência: [paleta atual](command-palette.png).

### U09 P2 Falta gerenciamento de perfis de provedor na UI

**Lacuna confirmada pela tela e pelo código.** A UI cadastra e seleciona perfis, mas não oferece lista de gerenciamento para editar, renomear, remover ou trocar a API key. O cadastro rejeita nomes duplicados e a mensagem orienta remover o perfil, operação disponível no CLI.

**Recomendação:** adicionar “Gerenciar provedores”, com perfil ativo, URL, condição TLS, teste e edição da credencial sem exibir o valor salvo. Tratar remoção do perfil ativo e alteração concorrente.

**Aceite:** um usuário corrige endpoint e credencial, testa e troca de modelo sem sair do chat nem editar arquivos de configuração.

**Código:** `src/codaro/provider_ui.py`, `src/codaro/providers.py:293` e comandos `providers` em `src/codaro/cli.py`.

### U10 P2 Campos preenchidos perdem sua identificação

**Problema visual confirmado.** Nome, URL e API key dependem de placeholders, que desaparecem quando há valor. Provedor e modelo também não têm rótulos persistentes. Em janelas pequenas, o título e os textos auxiliares podem sair da área visível.

**Recomendação:** rótulos permanentes curtos, indicação de obrigatório/opcional e mensagens junto ao campo. Mostrar opções avançadas separadamente; manter TLS Insecure explícito e com estado claro.

**Aceite:** todos os campos continuam identificáveis após preenchimento, erro e rolagem. Permitir revelar a API key apenas por ação explícita, caso essa opção seja implementada.

**Código:** `src/codaro/provider_ui.py:34`. Evidência: [formulário completo](provider-160x50.png).

### U11 P2 Autorizar escopo exige escrever JSON

**Problema de usabilidade confirmado.** O modal expõe diretamente `{"paths": [...], "commands": [...]}` em um editor. Para uma ação com efeito sobre arquivos e comandos, o usuário precisa entender formato, sintaxe e alcance dos prefixos.

**Recomendação:** campos separados para caminhos e comandos, adicionar/remover entradas e uma prévia legível do alcance. JSON pode permanecer como opção avançada. Mostrar tarefa vinculada e como revogar.

**Aceite:** configurar um escopo comum sem editar JSON; exibir exatamente quais operações e comandos serão autorizados. Manter o foco inicial em Cancelar.

**Código:** `src/codaro/tui.py:175`. Evidência: [modal de escopo](scope-80x24.png).

### U12 P2 Erros de conexão não conduzem à recuperação pela UI

**Comportamento reproduzido.** Uma falha de conexão retorna ao chat com uma mensagem que cita `CODARO_BASE_URL`, mesmo com `provider_id="Local"`. Não há ação junto ao erro para testar, configurar, selecionar outro modelo ou repetir a pergunta. A pergunta pode ser recuperada pelo histórico, mas isso depende de conhecimento prévio.

**Recomendação:** erro com provedor/modelo identificados, categoria e ações de recuperação. No primeiro uso sem configuração funcional, oferecer configurar ou testar provedor. “Pronto” deve significar disponibilidade do compositor, sem sugerir verificação bem-sucedida da conexão.

**Aceite:** falha de conexão, 401, 404 e incompatibilidade de ferramentas têm instruções pertinentes ao perfil ativo e um caminho curto para tentar novamente, preservando a pergunta.

**Código:** tratamento em `src/codaro/tui.py:1143` e erros de `src/codaro/provider.py`. Evidência: [falha de conexão](connection-failure.png).

### U13 P2 Modelos incompatíveis são apresentados como selecionáveis

**Comportamento reproduzido.** Um modelo com `tools=False` no catálogo aparece no seletor e exibe informações normais. O bloqueio ocorre somente depois de clicar “Usar modelo”. O backend impede a seleção, mas a UI não antecipa a incompatibilidade.

**Recomendação:** indicar “Sem ferramentas/chat” no catálogo, desabilitar seleção incompatível ou filtrá-la por padrão. Distinguir suporte desconhecido de suporte ausente; oferecer teste quando a API não informa capacidade.

**Aceite:** o usuário entende a condição antes de tentar usar o modelo. Suporte desconhecido não é apresentado como confirmado.

**Código:** `src/codaro/provider_ui.py:183` e `src/codaro/providers.py:372`. Evidência: `incompatible-model-selectable`; [modelo incompatível](incompatible-model.png).

## Refinamentos da experiência

### U14 P3 Contexto fica pouco visível depois da geração

**Lacuna visual confirmada.** Durante a chamada, a linha de estado mostra estimativa e limite. Ao terminar, ela volta a “Pronto” ou ao estado da tarefa. O detalhamento existe em `/status`, mas não há um indicador compacto de orçamento/compactação acessível continuamente.

**Recomendação:** indicador discreto de contexto, fonte do limite e compactações, com detalhes sob demanda. Separar estimativa, contagem do servidor e orçamento aprendido; não prometer medição exata quando há fallback.

**Aceite:** consultar o estado atual em uma ação; recuperação de contexto aparece como progresso compreensível, sem despejar payloads no chat.

### U15 P3 Streaming ainda tem ruído e mudança de apresentação

**Comportamento observado.** Raciocínio e resposta ficam separados corretamente, mas uma resposta simples pode produzir três blocos: raciocínio, “Geração concluída” e resposta final. A prévia é texto simples e mostra Markdown literal; no fim ela se transforma em Markdown renderizado, alterando o layout.

**Recomendação:** remover o resumo vazio quando não houve ferramentas; permitir recolher raciocínio por preferência; renderizar Markdown parcial quando estável e preservar o ponto de leitura durante a promoção. Manter chamadas de ferramentas e texto intermediário distintos da resposta aceita.

**Aceite:** resposta final aparece uma vez; texto intermediário continua identificável; títulos/código longos não provocam saltos evitáveis; cancelamento preserva somente a prévia interrompida.

**Código:** `src/codaro/tui.py:272`, `:352`, `:1244` e `:1298`. Evidências: [stream parcial](stream-partial.png) e [resposta final](stream-final.png).

### U16 P3 Idioma e nomes de ações são inconsistentes

**Problema observado.** O menu mistura comandos em português e inglês, incluindo “Search for commands”, “Quit” e “Keys”. A ajuda usa valores `ask/plan/execute` e `action/task`, enquanto o cabeçalho mostra nomes em português.

**Recomendação:** usar português consistente nos rótulos e descrições, preservando comandos técnicos como aliases. Nomear estados pelo resultado percebido pelo usuário.

**Aceite:** fluxo principal em um idioma; aliases existentes continuam funcionando. Evidência: [paleta](command-palette.png).

### U17 P3 O compositor não permite preparar a próxima mensagem durante a execução

**Comportamento reproduzido.** Durante streaming, `Prompt.disabled=True`. É um controle válido contra envio simultâneo, mas impede redigir esclarecimentos ou a próxima pergunta.

**Recomendação:** permitir escrever um rascunho enquanto o agente trabalha e bloquear somente o envio. Se houver envio em fila ou orientação durante a execução, defini-los explicitamente e preservar a ordem das autorizações.

**Aceite:** texto digitado durante uma tarefa não dispara uma segunda investigação, não altera a aprovação em andamento e permanece disponível ao término.

**Código:** `src/codaro/tui.py:1095`. Evidência: `stream-live` e [stream parcial](stream-partial.png).

## Comportamentos que passaram

- API key mascarada; erro simulado 401 não exibiu a credencial nem gravou um perfil.
- Teste de catálogo distinto do teste do modelo, com chamadas de ferramentas e resposta verificada nos testes existentes.
- Fechar cadastro com Esc devolveu o foco ao compositor nos quatro tamanhos.
- Aprovações de comando começam em Rejeitar; diffs começam em Voltar; escopo começa em Cancelar. Os exemplos curtos couberam nos quatro tamanhos.
- Referência `@ma` completou para `@main.py`; comandos `/` abriram sugestões; compositor cresceu até oito linhas.
- Raciocínio e resposta parcial ficaram em blocos distintos. A resposta final foi promovida e o foco voltou ao prompt. Cancelamento exibiu prévia interrompida, sem resposta final falsa.
- A suíte de modos, autorização, workflow e recuperação de contexto passou. Isso valida os cenários automatizados, não a qualidade de respostas de um modelo externo real.

## Ordem de implementação recomendada

1. **Previsibilidade e leitura:** U01, U02 e U05. Definir limpeza/nova tarefa, preservar scroll e corrigir os temas oferecidos.
2. **Configuração acessível:** U03, U04, U06, U07, U10, U12 e U13. Rodapé fixo, cabeçalho adaptável, foco, cancelamento e recuperação.
3. **Fluxos completos pela interface:** U08, U09 e U11. Paleta central, gerenciamento de provedores e escopo visual.
4. **Acabamento:** U14 a U17. Contexto discreto, streaming com menos ruído, idioma e rascunho durante execução.

Antes de entregar cada etapa, exercitar 80 × 24 e 120 × 35, navegação apenas por teclado, erro/repetição, redimensionamento, caminho/modelo longo e retorno ao prompt. Acrescentar snapshots dos estados críticos à CI, sem depender de animações ou de chamadas pagas.
