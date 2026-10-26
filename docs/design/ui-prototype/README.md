# Codaro — proposta consolidada de UI/UX

Esta é a referência única para adotar a nova interface. As alternativas visuais anteriores foram removidas. Este diretório contém uma proposta navegável com dados fictícios. A implementação nativa e suas verificações estão documentadas na [revisão de adoção](../../audits/2026-10-21-ui-parity-fixes/README.md); as imagens deste diretório continuam sendo maquetes.

## Abrir

- [Protótipo navegável](prototype.html): abre offline, sem instalação ou API.
- [Galeria de todos os estados](gallery.html): início, conversa, execução, perguntas, configuração, permissões e recuperação.
- [Visão geral](overview.png): seis telas principais da mesma proposta.
- [Pacote completo](codaro-ui-prototype.zip): HTML, maquetes, especificação, fontes e verificações.
- [Revisão final e critérios de adoção](REVIEW.md).

A barra superior do protótipo escolhe cenários e apresentações Amplo, Notebook ou Compacto. Ela é um guia de validação e não faz parte da CLI. Os cenários são independentes; não há inferência, streaming de rede, armazenamento de credenciais nem execução real. Use somente valores fictícios nos formulários.

## Decisões aprovadas

| Componente | Comportamento |
| --- | --- |
| Início | Corpo vazio, cabeçalho discreto e input pronto para digitar. Primeiro acesso orienta a conexão do provedor. |
| Cabeçalho | Projeto, modelo e modo. Informações completas ficam em detalhes da sessão. |
| Conversa | Mensagens do usuário com fundo sutil; resposta do Codaro em fundo neutro; divisões discretas entre turnos. |
| Etapas | Uma seção por turno; até dois resumos curtos durante execução; detalhes de atividade e raciocínio separados e recolhidos. |
| Resposta | Área própria, fora das etapas. Streaming ocupa o lugar definitivo da resposta. |
| Pergunta ao usuário | Pergunta, opções verticais e único input compartilham fundo e borda. Digitar permite responder livremente. |
| Contexto | Uma linha no rodapé com estimativa e orçamento de entrada. Janela total, reserva, margem e origem ficam em `/status`. |
| Feedback | Estado curto próximo ao input; sem avisos flutuantes cobrindo a conversa. |
| Falhas | Mensagem objetiva, progresso preservado e ação de recuperação próxima. |

Hover e foco alteram cor e contorno, mantendo dimensões e texto dos botões. Textos secundários têm contraste maior; avisos usam texto e ícones além da cor. Formulários rolam internamente e mantêm ações acessíveis. Mudanças de seleção mantêm o item visível.

## Input e comandos

| Tecla | Ação |
| --- | --- |
| `/` | Abrir a lista de comandos e filtrar conforme digita |
| `↑` / `↓` | Navegar nas opções de comandos, modelos, perguntas e modos |
| `Enter` | Confirmar a opção destacada ou enviar texto livre |
| `Tab` no menu | Completar o comando sem executar |
| `Esc` | Fechar menu/modal ou pausar uma pergunta pendente |
| `Alt+Enter` / `Shift+Enter` | Inserir nova linha |
| `Ctrl+P` | Abrir comandos, preservando o rascunho |
| `Ctrl+X` durante execução | Interromper, preservando o progresso e o rascunho |

Um comando sem correspondência não vira mensagem ao modelo. Tab continua permitindo sair de uma lista vazia. O menu pode ser aberto durante a execução. Nas perguntas, texto como `/api/v1` é uma resposta literal; comandos são abertos explicitamente por Ctrl+P.

O protótipo web também reconhece Meta nos atalhos. Na implementação em terminal, mostrar `⌘` depende do suporte real do terminal: detectar macOS sozinho não garante que o evento chegará à aplicação. Manter Ctrl como alternativa. A ajuda do rodapé muda conforme o contexto e evita listar todos os atalhos simultaneamente.

## Perguntas e permissões

A pergunta pendente fica dentro do componente do input, com uma pergunta objetiva, explicação curta e opções em lista vertical. O usuário pode usar setas e Enter, clicar em uma opção e enviar, ou simplesmente escrever a própria resposta. Ao digitar, as opções perdem o destaque de seleção; Enter ou Enviar confirma o texto.

Pausar ou Esc mantém a pergunta pendente. Uma pergunta obrigatória não é resolvida por timeout nem por ausência de resposta. Depois do envio, pergunta e resposta entram no histórico e o input volta ao estado normal.

Uma resposta sobre o produto não autoriza automaticamente alterações ou comandos. Revisões mostram o diff ou comando exato e exigem uma ação explícita de aprovação. Rejeitar uma ação não desfaz nem apaga o progresso de etapas anteriores. Fechar a revisão não confirma a ação.

## Modos de trabalho

| Modo | Objetivo |
| --- | --- |
| Perguntar | Entender o código e esclarecer dúvidas |
| Planejar | Investigar a tarefa e propor os próximos passos |
| Executar | Implementar, testar e revisar o resultado |

O seletor é um menu compacto ancorado ao indicador do modo, mantendo a conversa visível. Cada opção ocupa uma linha. O destaque de navegação e a etiqueta Atual são independentes: setas ou clique destacam; Enter ou Aplicar confirma; Esc ou clique fora fecha. A mudança vale para o próximo pedido, preservando histórico, rascunho e pergunta pendente.

Ao destacar Executar, o menu mostra o controle de aprovação de edições. Seus detalhes ficam recolhidos até serem solicitados. Pedir autorização é o padrão; Aprovar automaticamente permite criar e editar arquivos do projeto nesta conversa. A alteração só é confirmada em Aplicar. Comandos e ações destrutivas continuam exigindo autorização. Uma nova conversa volta ao padrão de pedir autorização. Quando ativa, a aprovação automática é identificada na linha de estado.

A aplicação deve validar o escopo de cada edição antes de aplicá-la automaticamente, respeitando os arquivos permitidos e as demais restrições do projeto. A opção não concede aprovação a comandos nem aplica uma proposta pendente por si só.

## Acompanhamento do plano

Enquanto existe um plano ativo, sua lista fica fixa junto ao input, fora da área de rolagem da conversa. Ela começa aberta, mostrando a quantidade de etapas concluídas, a etapa atual e o que falta, com status Concluída, Em andamento e Pendente. A altura é limitada, com rolagem própria, para manter o chat acessível acima.

Recolher mantém uma linha de progresso visível junto ao campo. A escolha manual de expansão é preservada durante atualizações e configurações. Etapas novas não reabrem a lista. O plano continua visível durante novas mensagens, perguntas do modelo, respostas finais de um turno, erros de conexão e interrupções da geração. Perguntas continuam integradas ao único input; o plano não cria outro campo de resposta.

Somente a conclusão efetiva de todas as etapas ou a ação explícita Cancelar plano remove a lista dessa área. Interromper uma geração mantém o plano para retomada. Cancelar o plano preserva as alterações já aplicadas; começar outra conversa exige a confirmação já prevista e informa que encerra o plano atual. O histórico permanece disponível para consulta.

Os cenários plan-chat, plan-collapsed e plan-expanded permitem validar a convivência entre conversa, plano e input; edit-approval mostra os controles de aprovação. O protótipo simula esses estados; a adoção deve ligar o ciclo do plano aos eventos reais do agente, independentemente da conclusão de cada turno.

## Configuração e contexto

Provedores: OpenAI, Anthropic, Gemini, Groq, Ollama e OpenAI compatible. O formulário prioriza tipo e chave; Ollama mostra a URL sem exigir chave. Nome do perfil, URL personalizada e TLS ficam em Avançado. A consequência de TLS Insecure aparece junto da opção; uma conexão sem verificação permanece identificada no cabeçalho após sua seleção.

Testar conexão consulta o catálogo, sem inferência. É possível cancelar o teste e voltar ao formulário preenchido. Verificar resposta e tool-calling é uma ação separada, identificada como capaz de consumir créditos. O diagnóstico exibido no protótipo é fictício.

A seleção de modelo tem busca e teclado. O contexto apresentado acompanha o modelo selecionado. Metadados ausentes são identificados como desconhecidos, com orçamento conservador explicado nos detalhes. Modelos de embeddings ficam indisponíveis para conversa.

As configurações e preferências retornam à conversa de origem. Preferências só são aplicadas ao salvar; Cancelar preserva os valores anteriores. O input e a posição de leitura são preservados ao abrir e fechar configurações.

Os valores de contexto e consumo são exemplos para validar a apresentação. A implementação deve consumir metadados e uso reais de uma única fonte de estado da sessão. A reserva de saída usada para planejar o orçamento não implica enviar um limite de saída ao provedor.

## Contrato da conversa para implementação

- Preservar a escolha manual de abrir ou recolher detalhes por turno; eventos novos não reabrem a seção.
- Derivar resumos de ações observáveis. Raciocínio só aparece quando explicitamente disponibilizado pelo provedor, sem inventar conteúdo.
- Não apagar texto mostrado ao trocar entre atividade e resposta. A resposta em streaming permanece na mesma área.
- Manter foco e rascunho durante atualizações; ignorar Enter enquanto houver composição de texto.
- Respeitar quem está lendo o histórico: sem rolagem forçada; oferecer indicador discreto de novas mensagens.
- Ao compactar contexto, preservar decisões e resultados e continuar quando possível. Pedir escopo quando uma decisão do usuário for necessária.
- Aplicar mudanças de modelo e modo somente a pedidos seguintes, sem alterar a semântica de uma tarefa já em execução.
- Uma nova conversa preserva o histórico para retomada e inicia um novo escopo de tarefa. A implementação deve explicitar o descarte de propostas ainda pendentes.

## Fontes e reconstrução

`core.js` contém cenários, dados de exemplo e transições. `ui.js` renderiza e conecta as interações. `style.css` define a aparência. `template.html` contém o guia de revisão. `render_screens.py` gera maquetes vetoriais. `build.py` reconstrói os artefatos a partir dessas fontes e valida o pacote.

```bash
# Requer Python 3, Node.js e Inkscape
python3 build.py

# Apenas o HTML, sem dependências de renderização
python3 build.py --html-only

# Verificar as interações do protótipo
node validate.mjs
```

A reconstrução completa atualiza catálogo, testes, HTML, galeria, maquetes, visão geral e ZIP. Não mantenha versões paralelas desses artefatos. `cleanup.json` registra os arquivos antigos removidos, sem manter cópias das propostas anteriores.

As imagens SVG/PNG são maquetes, não screenshots da aplicação. Os testes executam as transições e os bindings com um DOM mínimo, sem medir layout. A captura via Chromium foi bloqueada pelas restrições do ambiente; validação visual em navegador e terminal real permanece necessária na adoção.
