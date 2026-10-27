# Auditoria de UI/UX — Codaro 0.2.0

Data: 27/10/2026 (America/Manaus). Base: commit `82ed312` de `develop`.
Este relatório preserva os achados anteriores à correção. A implementação e a
validação posteriores estão em [correções e evidências](fixes.md).

## Resultado

A composição principal está consistente com a proposta: início limpo, conversa
sem painel lateral, pergunta integrada ao input, plano persistente e recolhível,
etapas agrupadas e resposta apresentada separadamente. Não recomendo redesenhar
a interface nem acrescentar um dashboard na abertura.

Foram encontrados **dois problemas de prioridade alta**, além de oito ajustes de
clareza, adaptação e apresentação. O foco deve ser a correspondência entre aquilo
que a UI comunica e o que o sistema realmente está fazendo.

## Como foi verificado

- Aplicação Textual real em modo de teste, com teclado, mouse e componentes reais.
- **167 testes de UI aprovados**, incluindo navegação, configuração, perguntas,
  geração, rolagem, cancelamento e métricas.
- **184 capturas**, cobrindo **51 estados**, com matriz principal 60×20, 80×24,
  120×35 e 160×45. Estados adicionais foram verificados nos três primeiros tamanhos.
- **33 verificações de hover**, com geometria estável em todas elas. Inspeção
  visual das capturas principais, diálogo de escopo, input, provedores e perguntas.
- Tema Codaro em truecolor, tema claro e uma passagem inicial em `NO_COLOR`.
  O ambiente inicia com `NO_COLOR=1`; as capturas finais em cores foram feitas
  com essa variável removida. Não se confundiu o resultado monocromático do
  ambiente com um defeito da paleta da aplicação.
- Provedores e mensagens sintéticos; erro HTTP simulado. Um processo Python local
  descartável confirmou o comportamento de cancelamento de jobs. Nenhuma chamada
  de inferência, API paga, chave real ou arquivo do usuário foi necessária.

Evidências geradas em `artifacts/ui-audit-2026-10-27/`, ignoradas pelo Git:
`gallery.html`, `overview.png`, `audit-manifest.json`, `probe-results.json`,
`locked-vault-result.json`, SVGs e texto renderizado.

## Achados e ações propostas

### UI-01 — Cancelar atividade/plano deixa comandos em segundo plano ativos (P1)

Reprodução: iniciar um job local de validação, solicitar cancelamento pela ação
da UI e cancelar o plano. Nos três tamanhos, o evento da interação foi marcado
como cancelado e o plano passou a `cancelled`, mas o job permaneceu `running`.
A tela passou a mostrar “Plano cancelado · alterações preservadas”, sem um
indicador persistente do processo que continuava executando.

`ui/app.py:1669` sinaliza somente o cancelamento da interação. O job tem seu
próprio evento em `jobs.py`; `cancel_execution_plan` também não o encerra.
Não há um comando/menu de usuário dedicado à lista de jobs na paleta atual.

**Recomendação:** definir o alcance do cancelamento e mostrá-lo claramente.
“Cancelar atividade”/“Cancelar plano” deve encerrar os jobs associados, ou oferecer
uma escolha explícita para mantê-los. Exibir uma linha recolhível “1 comando em
execução”, com saída e ação de parar, apenas enquanto houver jobs. Não criar painel
lateral permanente. Confirmar o término real antes de dizer que tudo parou.

**Aceite:** após cancelar, nenhum job da tarefa continua ativo sem consentimento;
saída já produzida e rascunho são preservados. Validar também cancelamento durante
pergunta e aprovação, e processo com descendentes.

Evidências: `probe-results.json`, `cancel-with-background-job-*`,
`cancelled-plan-background-job-*`.

### UI-02 — Editar provedor com cofre bloqueado pode encerrar a interface (P1)

Reprodução pelo botão “Editar / testar” do gerenciador: o cofre sintético recusa
a leitura; o callback deixa escapar `ValueError`. O teste de interação registrou
`app._exception` e a saída de `run_test` com a mesma exceção.

`ui/provider_actions.py:26` instancia `ProviderSetup` sem tratar essa falha;
`ui/providers.py:56` tenta recuperar a credencial já no construtor.

**Recomendação:** abrir o formulário com os metadados e referência da credencial,
sem exigir leitura do segredo. Tratar indisponibilidade no teste/gravação ou, se
necessário ao abrir, apresentar um erro recuperável na própria tela. Preservar
conversa, rascunho, perfil e foco. Nunca migrar para arquivo automaticamente.

**Aceite:** editar perfil com cofre bloqueado/ausente não termina o app. A UI
informa como desbloquear/tentar de novo e permite voltar ao gerenciador.

Evidências: `locked-vault-result.json`; `reproduce_locked_vault.py` nesta pasta.

### UI-03 — Conexão HTTP aparece como “TLS verificado” (P2)

O gerenciador mostra `http://localhost:11434/v1` e “TLS verificado” na mesma linha
de perfil. `ui/dialogs.py:86` escolhe o texto apenas pelo booleano `tls_insecure`.
Não verifica se a URL sequer usa TLS nem se houve um teste de conexão.

**Recomendação:** distinguir “HTTP”, “HTTPS · verificação habilitada” e
“HTTPS · sem verificar certificado”. Reservar “conexão testada” para uma consulta
real concluída, sem prometer estado permanente de conectividade.

**Aceite:** HTTP nunca recebe selo de TLS; HTTPS não testado não aparece como
conexão já verificada. Cobrir localhost e endpoint remoto.

Evidências: `provider-manager-http-*` e `probe-results.json`.

### UI-04 — Plano + pergunta quase eliminam a área de leitura em 60×20 (P2)

No cenário de pergunta longa com três opções e plano ativo, header, plano e
componente de resposta deixam apenas poucas linhas para o histórico. Os controles
continuam acessíveis; o problema é a pouca área útil para consultar o chat.

**Recomendação:** preservar plano e pergunta junto ao input, conforme a proposta,
mas usar uma composição mais compacta enquanto aguarda resposta: uma linha para
resumo do plano, expansão acessível, input inicialmente de uma linha e menos
espaço interno. Manter a preferência manual de expansão; oferecer altura mínima
útil para o histórico, sem esconder o plano ou mover a pergunta para um modal.

**Aceite:** em 60×20, o usuário consegue ler pelo menos uma mensagem curta do chat
junto da pergunta e responder por opções ou texto. Ao terminar a pergunta, restaurar
a expansão escolhida pelo usuário.

Evidências: `long-question-60x20.svg` e `long-question-80x24.svg`.

### UI-05 — Opções longas ficam difíceis de distinguir (P2)

Cada sugestão usa uma linha com reticências. Quando as alternativas compartilham
um começo longo, o trecho que as diferencia pode não aparecer. O botão “Detalhes”
permite consultar tudo, mas interrompe a escolha no componente principal.

**Recomendação:** permitir duas linhas para a opção selecionada, ou mostrar uma
prévia completa da opção focada. Continuar com a lista vertical e resposta livre
no mesmo componente. Não acrescentar outro campo para responder.

**Aceite:** três alternativas com prefixo idêntico e finais distintos podem ser
identificadas por teclado sem abrir um diálogo separado; o valor enviado permanece
integral. A altura deve respeitar a reserva de chat do UI-04.

Evidências: `long-question-*`; `ui/questions.py:93`.

### UI-06 — Autorização de escopo ainda usa uma composição densa (P2)

Em 60×20, título, objetivo, explicação e aviso de revogação ocupam a maior parte
do diálogo; o editor de caminhos fica com pouca altura e os comandos exigem
navegação adicional. Os botões altos e o editor com moldura destoam dos formulários
compactos atuais. Não foi constatada concessão sem confirmação.

**Recomendação:** adotar o mesmo componente visual dos demais formulários: título
curto, resumo “2 caminhos · 0 comandos”, corpo rolável, ações fixas de uma linha e
texto de revogação em ajuda/detalhes. Manter foco inicial seguro em Cancelar.

**Aceite:** todos os caminhos e comandos podem ser revisados em 60×20, por teclado,
sem reduzir os campos a uma linha útil. Cancelar não concede escopo.

Evidências: `scope-review-*`; `ui/reviews.py:142`.

### UI-07 — Placeholder do input tem contraste baixo, especialmente no tema claro (P2)

Pelas cores exportadas no SVG, o placeholder usa aproximadamente `#868686` sobre
`#e0e0e0` no tema claro (**2,76:1**). No tema padrão, aproximadamente `#707479` sobre
`#111821` (**3,79:1**). São medições das cores renderizadas, não certificação de
acessibilidade de terminais/fontes reais.

**Recomendação:** aumentar o contraste do placeholder e revisar o estado de foco
em ambos os temas. Usar 4,5:1 como referência para texto pequeno, sem dar ao
placeholder o mesmo destaque do texto digitado.

**Aceite:** campo vazio identificável em claro, escuro e sem cores; texto digitado,
placeholder e seleção continuam distintos.

Evidências: `welcome-120x35.svg`, `light-theme-120x35.svg`.

### UI-08 — Estado e requisitos do cofre não estão claros no formulário (P2)

“Guardar API key no cofre do sistema” é um checkbox simples. O formulário não
mostra se o extra está instalado, se existe backend ou onde a chave ficará quando
a opção estiver desligada. A mensagem geral só diz que a chave fica fora do chat
 e do projeto.

**Recomendação:** ajuda contextual curta para arquivo privado versus cofre nativo;
mostrar indisponibilidade e instrução de instalação sem tentar desbloquear o cofre
somente para renderizar a tela. Concentrar essa informação em Avançado.

**Aceite:** usuário entende o armazenamento escolhido e pode recuperar erro de
cofre mantendo os dados. Nenhuma chave aparece em texto ou diagnóstico.

Evidências: `provider-vault-*`. Relacionado ao UI-02, mas trata clareza do formulário.

### UI-09 — Alguns diálogos têm espaço vazio e hierarquia inconsistentes (P3)

Gerenciador de um único provedor ocupa quase toda a altura; o título usa estilo
menos destacado que o cadastro. A aprovação de um comando curto também mantém
muito espaço vazio. Isso não impede uso, mas deixa o acabamento menos uniforme.

**Recomendação:** altura pelo conteúdo com teto e rolagem, título/rodapé e foco
compartilhados. Separar comando e diretório na aprovação, mantendo argv completo
 e a consequência da autorização visíveis.

**Aceite:** um perfil/comando curto usa um modal compacto; listas e comandos longos
continuam revisáveis sem cortar ações nem modificar dimensões no hover.

Evidências: `provider-manager-http-80x24.svg`, `command-cwd-80x24.svg`.

### UI-10 — Espaçamento do chat pode ser mais eficiente em telas baixas (P3)

Parágrafos, listas e um bloco curto de código usam uma quantidade significativa
de linhas vazias. A leitura está clara, mas um exemplo de uma linha ocupa uma
fração grande de 80×24 quando há plano ativo.

**Recomendação:** reduzir espaçamento vertical de listas/blocos em terminais
baixos, mantendo largura e rolagem do código. Não voltar a colocar a resposta em
cards de raciocínio e não remover todo o respiro de mensagens longas.

**Aceite:** código e listas continuam legíveis, com mais contexto visível em 80×24
 e sem compactar excessivamente o layout de 120×35.

Evidências: `chat-final-*`, `active-plan-80x24.svg`.

## Comportamentos que devem ser preservados

- Início limpo; nenhum painel lateral redundante.
- Navegação `/` com setas, Enter, Tab e Esc.
- Pergunta, sugestões e resposta livre integradas ao mesmo input.
- Separação visual entre etapas, raciocínio recebido e resposta.
- Lista do plano junto ao input, com recolhimento e rolagem manual preservados.
- Cadastro simples, detalhes avançados recolhidos e testes de catálogo/inferência
  apresentados como operações diferentes.
- Rejeitar/Voltar como foco inicial nas aprovações; navegação não concede permissão.
- Indicadores compactos de contexto e velocidade, com detalhes sob demanda.

## Ordem de trabalho recomendada

1. UI-01 e UI-02: cancelamento confiável e exceção do cofre.
2. UI-03, UI-06 e UI-08: precisão e clareza das configurações e autorizações.
3. UI-04, UI-05 e UI-07: leitura, escolha e contraste nos tamanhos pequenos.
4. UI-09 e UI-10: consistência final e densidade.

Acrescentar os casos reproduzidos à suíte de regressão ao implementar as correções.
Os 167 testes existentes passaram, mas não cobriam essas combinações novas de
jobs, cofre bloqueado e rótulo de transporte.

## Limites

Não foram feitas chamadas de inferência, testes de voz, avaliação por leitor de
tela nem validação manual de teclado em terminais macOS/Windows nesta auditoria.
A inferência foi substituída por eventos/HTTP controlados para verificar a UI.
O resultado não certifica todos os estados nem desempenho em terminal remoto.
