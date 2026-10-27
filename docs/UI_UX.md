# Correções da auditoria de UI e UX do Codaro

## Retomada, comandos e configuração de integrações

- Oferta de retomada com título, última atividade e prévia; ações explícitas para
  retomar ou começar outra conversa. A abertura continua limpa quando não existe
  histórico. Começar outra conversa mantém os registros anteriores e arquivos,
  encerra a tarefa antiga e revoga permissões. Esc fecha sem criar ou apagar sessões.
- Confirmação de retomada no status e detalhes no tooltip, sem aviso amarelo no chat.
  Perguntas confirmadas usam Pergunta respondida, Codaro e Você; conteúdo histórico
  não é reescrito para corrigir respostas antigas do modelo.
- Menu / com moldura, colunas e seleção de alto contraste, contador e janela móvel
  de opções. Filtragem aceita hífens e maiúsculas. O plano mantém seu estado de
  recolhimento e adapta as linhas visíveis ao menu em terminais pequenos.
- Funcionalidades usam checkboxes, descrições e resumo das integrações. Limites e
  JSON somente leitura ficam em Avançado. Cancelar preserva a configuração; salvar
  aplica apenas os campos alterados, preservando cadastros concorrentes.
- Cadastro MCP/plugin com campos condicionais, rótulos permanentes, entrada compacta,
  foco visível, área rolável e rodapé fixo. Variáveis e TLS ficam em Avançado.
- Confiança explícita antes da descoberta; salvar depende de teste concluído da
  configuração atual. A lista descoberta começa sem ferramentas confiáveis de
  leitura; o usuário marca cada uma. Nome original permanece separado do identificador
  de protocolo. Alterações de configuração ou arquivo do plugin invalidam o teste.
- Cancelamento e geração de operação impedem aplicar descobertas tardias. Cancelar
  o teste mantém o formulário; um segundo Esc pode fechá-lo. A descoberta fecha os
  clientes ao terminar e nunca grava a configuração automaticamente.

## Revisão dos prints de cadastro, modos, modelos e conversa

- O cálculo da largura das opções desconta padding e scrollbar do componente.
  Contexto ausente usa —; seleção e metadados permanecem sincronizados após busca,
  resize e troca de provedor. O modelo ativo fica visível mesmo em catálogos extensos.
- Modos e política de edições têm seções distintas, sem um segundo submenu. A política
  aparece somente em Executar. A altura acompanha o conteúdo, com confirmação fixa;
  cancelar ou apenas navegar mantém as permissões anteriores.
- Provedores têm nomes amigáveis, placeholder e foco visível na credencial, retorno
  de conexão com quantidade de modelos e ação Salvar e escolher modelo em telas
  maiores. Testar conexão consulta a lista; o teste de geração continua separado.
  Credencial obrigatória ausente e URL inválida desabilitam consulta e gravação.
- Texto corrido e listas têm limite de 100 colunas, sem estreitar código/diffs.
  Mensagens do usuário usam destaque discreto. Pergunta e resposta confirmadas ficam
  num único bloco de decisão, com a mesma apresentação após retomar a sessão.
- Pedidos como me faça uma pergunta são consultas. A tool valida quando question
  repete exatamente o pedido recebido e solicita reformulação ao modelo; não inventa
  uma pergunta ou uma resposta. Ações de perguntas usam nomes legíveis e não mostram
  informações de paginação de arquivos.
- Recibos exigem alteração ou validação registrada. Ausência de verificação é neutra,
  falhas são destacadas e apenas verificações aprovadas da revisão atual recebem verde.
  A velocidade após o turno fica identificada como Última:.

## Ajustes de 26 de outubro

- Perguntas reservam uma linha por opção, mesmo com textos longos. Título limitado
  a duas linhas e botão Detalhes para conteúdo completo. A resposta enviada conserva
  o texto original da opção; a prévia abreviada não altera a decisão.
- O plano acompanha a etapa ativa; rolagem manual e recolhimento são preservados.
  Etapa atual reativa o acompanhamento. Em terminais baixos, a pergunta tem prioridade
  e o plano mantém a etapa atual sem ocupar todo o chat.
- Etapas mostram resumos durante o trabalho e tempo total ao concluir. Duração de
  ferramenta fica nos detalhes. Resposta e raciocínio mantêm áreas distintas;
  parágrafos de listas usam espaçamento compacto.
- Status, velocidade e contexto têm posições próprias. A dica de cancelamento
  aparece uma vez. Velocidade distingue estimativa, tempo nativo do provedor e média
  da chamada; pergunta pendente e cancelamento ocultam o indicador.
- Respostas têm ações de copiar texto/código e prévia local de arquivos citados,
  acessíveis por Ctrl+O ou clique direito, sem consumir contexto ou conceder permissões.
- Conversas têm pesquisa, última atividade, retomada, arquivamento e restauração.
  Navegação preserva o rascunho e a ativação mantém a revogação de permissões.
- Lista de modelos usa uma linha por ID e contexto alinhado. ID completo nos
  detalhes. Testar modelo fica acessível no rodapé, separado de Testar conexão;
  formulário do provedor usa a altura dos campos visíveis.
- Revisão de diff reduz o título em terminais baixos para priorizar as alterações.
  Voltar continua sendo o foco inicial: Enter não aprova uma edição por acidente.
- Aprovação automática aparece como Executar · Auto no cabeçalho.

O script `scripts/capture_ui.py` reproduz 31 estados em quatro tamanhos de terminal,
com dados sintéticos e sem inferência. Os testes de interação e métricas ficam em
`tests/test_ui_experience.py` e `tests/test_ui_generation_rate.py`.

A adoção da nova proposta começou com a [primeira etapa nativa](audits/2026-10-21-native-ui/README.md):
início limpo, composer, plano persistente e seletor compacto de modo com aprovação de edições.
As evidências históricas abaixo descrevem a interface anterior; a documentação da nova etapa
identifica o que foi substituído e o que permanece pendente.

A [revisão de fidelidade ao protótipo](audits/2026-10-21-prototype-fidelity/README.md)
substitui a composição anterior de cabeçalho, mensagens, etapas, input e seletores.
Inclui perguntas com opções no campo compartilhado e capturas comparativas da CLI real.

Os 17 achados da auditoria de 7 de outubro de 2026 foram tratados nos fluxos do chat, configuração e navegação. A limpeza de mensagens preserva propostas e explica tarefa/escopo mantidos; uma nova atividade confirma o descarte de propostas e revoga permissões. O acompanhamento do chat respeita a posição de leitura.

| Achado | Comportamento entregue | Validação |
| --- | --- | --- |
| U01 Limpeza ambígua | `/clear` preserva propostas; `/restore-clear` recupera contexto; `/new` inicia outra atividade com escopo revogado | Proposta, tarefa, grant, restauração e arquivo intacto |
| U02 Rolagem forçada | Preserva posição e oferece Novas mensagens ↓ | Chegada de ferramenta e geração com leitor no início |
| U03 Formulários cortados | Campos roláveis, ações fixas e botões ajustados à largura | Geometria em 60×20, 80×24, 120×35 e 160×50 |
| U04 Cabeçalho cortado | Duas linhas quando necessário, modo/aprovação preservados e atalhos compactos | Modelos longos e matriz de terminais |
| U05 Tema claro | Cores semânticas de fundo, texto, foco e estado | Troca de tema e inspeção de capturas claras/escuras |
| U06 Foco perdido | Restaura botão/campo depois do teste; identifica campos com erro de validação | Teste, cancelamento, erro e volta ao prompt |
| U07 Teste sem cancelamento | Cancelar teste/Esc libera formulário; resultados tardios ignorados e etapas posteriores interrompidas | Transporte lento controlado; configuração alterada durante cancelamento |
| U08 Paleta incompleta | Modos, tarefa, retomada, alterações, permissões, contexto, histórico, provedores e ajuda | Descoberta por Ctrl+P e preservação de rascunho |
| U09 Gestão de perfis | Editar, renomear, trocar chave, testar e remover; invalida perfil ativo removido | Cadastro/edição/remoção via UI; falha e atualização concorrente sem sobrescrever credenciais |
| U10 Campos sem rótulos | Rótulos permanentes, obrigatório/opcional e credencial mascarada | Formulários preenchidos, rolagem e capturas |
| U11 Escopo em JSON | Caminhos/comandos por linha, prévia legível e JSON opcional | Parsing de argv com aspas; nenhuma concessão antes da decisão |
| U12 Recuperação de erros | Perfil/modelo identificados; ações conforme cancelamento, credencial, disponibilidade ou limite | Pergunta original reenviada sem substituir o rascunho |
| U13 Modelo incompatível | Modelos sem ferramentas ocultos; capacidade desconhecida informada | Catálogo com chat e embedding |
| U14 Contexto invisível | Indicador compacto persistente; contagem e origem em `/status` e tooltip | Geração, término e compactação |
| U15 Ruído no streaming | Resposta final fora dos cards; raciocínio discreto, recolhível e limitado a oito linhas visíveis; prévia Markdown estável e resumo vazio oculto | Texto parcial, ferramenta, falha, cancelamento e resposta final única |
| U16 Idioma inconsistente | Paleta e ações principais em português, aliases técnicos mantidos | Placeholder e comandos localizados |
| U17 Rascunho bloqueado | Redação durante execução, envio simultâneo bloqueado | Rascunho permanece após término e só um turno é executado |

## Verificações

A suíte completa, Ruff, formatação e diff são executados na entrega. As reproduções visuais usam a aplicação Textual real, teclado, cliques e respostas controladas de provedores; não realizam chamadas pagas. Os testes de regressão estão em `tests/test_ux_audit_fixes.py`, além dos cenários existentes de TUI/provedores/workflow.

O cancelamento de consultas de provedores libera a interface e evita aplicar resultados antigos. Isso inclui teste, cadastro, edição, atualização do catálogo e seleção de modelos. A consulta remota prepara uma alteração sem gravá-la; a UI verifica se a operação continua ativa antes de realizar o commit local atômico. Uma operação HTTP síncrona que já aguarda dados ainda depende do retorno/timeout para liberar sua thread; não há promessa de encerramento imediato do socket.

## Revisão por interação de 21 de outubro

Os oito achados da [nova auditoria](audits/2026-10-21-ui-ux/README.md) foram tratados:
todas as etapas do raciocínio respeitam o grupo recolhido; repetir preserva o
rascunho e o cursor; consultas remotas são canceláveis; autocompletar e editor
se adaptam à altura do terminal; Esc fecha sugestões; a configuração inicial
permanece visível em 60×20; cancelamento e falhas oferecem ações distintas;
tentativas resolvidas recolhem os detalhes e removem controles antigos.

O status mostra etapa e tempo decorrido. O contexto usa um indicador compacto,
com contagem e origem detalhadas em `/status` e no tooltip. O cadastro agrupa
URL, TLS e teste de ferramentas. A validação visual adicional lê o conteúdo
efetivamente renderizado, além de verificar propriedades dos componentes.

As capturas locais anteriores e posteriores permanecem em `docs/audits/2026-10-07-ui-ux/`. Terminais nativos de macOS/Windows, leitores de tela e inferência com provedores externos reais permanecem fora desta validação. A auditoria de segurança e maturidade geral tem escopo separado; esta entrega trata UI e UX.


## Adoção após a comparação das 46 telas

A [revisão completa](audits/2026-10-21-ui-parity-fixes/README.md) trata os 14 achados
da comparação. A galeria nova registra as capturas posteriores; os relatórios
anteriores preservam as evidências históricas.

- Decisões do usuário são persistidas imediatamente, redigidas como decisões no
  histórico e restauradas separadamente do protocolo do modelo.
- Pergunta, plano e input se adaptam ao resize. Ajuda do rodapé é contextual;
  pausa e comando sem correspondência têm estados explícitos.
- Ajuda/status usam modais; a revisão do plano permanece no chat, com Ajustar
  e Executar com revisão. Seleções de modo durante geração ficam para o próximo pedido.
- Cadastro tem campos compactos, URL local visível, autenticação opcional do Ollama
  e consequência de TLS ao lado da opção. Testes de catálogo e inferência são separados.
- Modelos têm busca, carregamento, vazio e incompatibilidade visível. Fallback é
  identificado; origem e janela manual ficam em detalhes acessíveis pelo teclado.
- A conclusão deriva validações e arquivos do registro real da tarefa. Resultados
  declarados pelo modelo não viram contagens de testes da aplicação.
- Uma nova conversa preserva a sessão anterior e volta às permissões por ação.

A validação automatizada usa Textual real em modo headless, HTTP simulado e
eventos sintéticos. Não comprova inferência real nem atalhos de um terminal físico.
