# Auditoria de UI/UX por interação

Aplicação auditada: Codaro 0.1.1, `develop`, commit
`62f6318048874831a9f67bbf32eb67ca27a0d14e`.

Foram executados os quatro conjuntos de testes de interface existentes, com
**64 testes aprovados**, e uma reprodução adicional com **49 observações**.
Os cenários adicionais encontraram dois bugs prioritários: etapas de raciocínio
escapam de um grupo recolhido e repetir uma pergunta descarta o rascunho atual.
Os oito achados foram corrigidos, junto às quatro melhorias de apresentação.
As descrições abaixo preservam o diagnóstico anterior. A reprodução posterior
está em [after/](after/README.md), com os oito critérios aprovados em
[verification.json](after/verification.json).

## Como foi testado

- Aplicação Textual real em `run_test`, usando teclado, cliques e formulários.
- Terminais de 60×20, 80×24, 120×35 e 160×50 caracteres.
- Abertura, paleta Ctrl+P, autocompletar e entrada com múltiplas linhas.
- Cadastro e gestão de provedores, seleção de modelos e consultas lentas ao catálogo.
- Revisões de comandos e de escopo, com verificação da geometria dos botões.
- Streaming, raciocínio em várias etapas, resposta final, cancelamento, erro e repetição.
- Estado do foco, preservação de rascunhos e texto efetivamente renderizado no terminal.
- Inicialização adicional do executável `codaro /tmp/codaro-ui-pty-audit`, comando
  local `/pwd` e abertura da paleta em PTY Linux. Esse smoke não equivale a validar
  terminais nativos ou todas as combinações de teclado.

Os repositórios, credenciais, catálogos e respostas dos cenários são sintéticos.
Não foram usados provedores externos nem inferência real. Também não foram
validados leitores de tela, macOS/Windows nativos ou latência real do Ollama/HPC.

## Achados e correções recomendadas

P1 significa correção prioritária de comportamento; P2, fricção relevante no uso;
P3, ajuste de clareza visual. As prioridades não representam uma classificação
de vulnerabilidades de segurança.

| ID | Prioridade | Achado confirmado | Recomendação |
| --- | --- | --- | --- |
| UX01 | P1 | A segunda etapa de raciocínio aparece com o grupo recolhido. Ao terminar, seus títulos também ficam fora do recolhimento. | Montar todas as etapas no `Contents` do grupo e testar a visibilidade renderizada, além de `collapsed`. |
| UX02 | P1 | Depois de uma falha, escrever outro rascunho e clicar em **Repetir** deixa o campo vazio. | Reenviar a pergunta original independentemente do editor e preservar o texto/cursor atuais. |
| UX03 | P2 | Durante **Cadastrar e listar** e **Atualizar API**, todos os botões ficam desabilitados e Esc não libera o formulário enquanto a consulta está ativa. | Tornar a fase de consulta cancelável e mostrar progresso; separar a consulta do commit atômico da configuração. |
| UX04 | P2 | Em 60×20, `/` expande sugestões para sete linhas e deixa somente duas linhas para o chat. A dica de Tab é cortada pelas descrições que quebram linha. | Reservar uma altura mínima para a conversa, limitar a quantidade de sugestões pela altura disponível e manter a dica visível. |
| UX05 | P2 | Esc não fecha o autocompletar aberto. | Dar prioridade ao fechamento das sugestões, preservando o rascunho; manter o cancelamento da atividade quando aplicável. |
| UX06 | P2 | Na abertura em 60×20, **Configurar provedor** fica abaixo da área visível. É acessível por rolagem/Tab. | Priorizar a configuração na abertura sem provedor e compactar a mensagem de boas-vindas em pouca altura. |
| UX07 | P2 | Cancelar uma geração voluntariamente exibe o mesmo trio **Repetir / Configurar / Outro modelo** usado em falhas do provedor. | Distinguir cancelamento, indisponibilidade, autenticação e limite; mostrar somente ações úteis a cada estado. |
| UX08 | P3 | Após a repetição bem-sucedida, a mensagem antiga de falha e seus três botões desabilitados continuam ocupando espaço. | Marcar a tentativa como resolvida e recolher os controles antigos, mantendo os detalhes acessíveis. |

### UX01 — recolhimento não esconde todas as etapas

Em `src/codaro/ui/widgets.py:118`, `ReasoningGroup.add_preview()` usa
`self.mount(preview)`. A primeira etapa, composta com o widget, fica dentro de
`Contents`; as seguintes ficam diretamente no grupo, fora do contêiner que o
Textual recolhe. A reprodução registrou `collapsed: true`, mas o texto
`SEGUNDA_ETAPA_OCULTA` continuou no conteúdo efetivamente renderizado.

Isso explica por que os testes atuais passam: eles verificam o estado do grupo
e a quantidade de etapas, sem afirmar que todas desaparecem da tela quando o
grupo é recolhido. A resposta final permanece fora do grupo, corretamente.

Evidências: [captura](reasoning-collapsed.png),
[texto renderizado](reasoning-collapsed.txt), [estado final](reasoning-final.txt).

Critério de aceite: com três etapas e o grupo recolhido, apenas o resumo do grupo
aparece. Expandir permite consultar todas as etapas; recolher oculta seus títulos
e conteúdos. Atualizações posteriores respeitam a escolha do usuário.

### UX02 — repetição perde trabalho digitado

Em `src/codaro/ui/app.py:1260`, o tratamento de **Repetir** substitui
`prompt.value` pela pergunta anterior; o envio limpa esse campo. Na reprodução,
o texto `Rascunho que deve ser preservado` desapareceu depois da repetição.

Evidências: observação `retry-finished` em [observations.json](observations.json)
e [captura em texto](retry-finished.txt).

Critério de aceite: repetir envia a pergunta que falhou, mantém o rascunho atual
e preserva também eventuais alterações digitadas durante a nova geração. Não
reaproveitar um snapshot antigo do editor sobre uma edição mais recente.

### UX03 — consultas sem saída imediata

Em `src/codaro/ui/providers.py:208`, salvar desabilita todos os controles antes
de consultar a rede. A atualização do catálogo no seletor usa comportamento
semelhante. As duas operações foram bloqueadas por um transporte controlado;
durante a espera, **Voltar** estava desabilitado e o formulário continuava aberto
após Esc. A atualização também mantém os rótulos comuns dos botões, dando pouco
retorno visual sobre a operação em andamento.

Essa limitação já é descrita em [UI_UX.md](../../UI_UX.md), por consistência das
gravações. A melhoria deve preservar essa consistência: cancelar uma coroutine
que chama `to_thread()` não impede uma gravação tardia dentro da thread. Separar
a leitura remota da gravação local e verificar cancelamento/geração antes de
alterar o perfil. A fase curta de commit pode permanecer indivisível.

Evidências: [salvamento](provider-save-wait.txt),
[atualização](model-refresh-wait.txt) e duas observações `slow-catalog`.

Critério de aceite: cancelar durante a consulta devolve o controle sem alterar
perfis ou a seleção atual; uma resposta tardia não aplica mudanças. Exibir
“Consultando catálogo…” e a ação de cancelamento durante essa fase.

### UX04–UX06 — pouco espaço e navegação por teclado

O menu de sugestões renderiza descrições completas que quebram linha; seu limite
de sete linhas corta o conteúdo restante. Mesmo com um campo de entrada de três
linhas, a conversa fica com duas linhas em 60×20. Já o editor com oito linhas
deixa quatro linhas para a conversa nessa dimensão, sem sugestões abertas.

Evidências: [autocompletar em 60×20](completion-60x20.png),
[editor com várias linhas](multiline-60x20.txt), [abertura](welcome-60x20.txt).

Critérios de aceite:

- Seleção e dica de navegação sempre visíveis, com descrições abreviadas.
- Altura do menu adaptada ao terminal; não ocultar a maior parte da conversa.
- Esc fecha sugestões e mantém texto e cursor.
- A configuração do primeiro provedor aparece na abertura compacta, sem exigir
  descobrir a rolagem. Isso não exige fixar todas as sugestões de início na tela.

### UX07–UX08 — estados finais e recuperação

O fluxo de `finish(..., successful=False)` em `src/codaro/ui/app.py:1114` usa a
mesma apresentação para cancelamento e erro. O cancelamento funcionou e manteve
o rascunho; o problema é a interpretação visual sugerida pelas ações posteriores.
Depois de um retry bem-sucedido, os controles antigos ficam desabilitados e
visíveis na conversa.

Evidências: [cancelamento](cancel-after.txt), [repetição resolvida](retry-finished.txt).

Critério de aceite: cancelamento é uma ocorrência neutra, com opção de reenviar;
falhas de autenticação levam à configuração, e indisponibilidade oferece repetir.
Ao resolver uma falha, o histórico identifica a resolução sem manter uma barra
de ações inativa disputando atenção com a resposta.

## O que funcionou

- A resposta chega parcialmente e termina como mensagem própria, fora do card
  de raciocínio. [Captura da resposta final](stream-final.png).
- Digitar durante a geração preserva o rascunho e não envia um segundo turno.
- Cancelar não promove o texto parcial a resposta final nem grava um turno aceito.
- Os botões de provedores, modelos, comandos e escopo ficaram dentro da área
  visível nas quatro dimensões testadas.
- A paleta abriu nas quatro dimensões, e a aplicação devolveu foco ao editor
  após a geração.
- A suíte existente passou nos casos de rolagem durante chegada de conteúdo,
  estado de atividades, temas, revisão de alterações e teste cancelável de conexão.

## Melhorias de apresentação entregues

1. Simplificar o indicador normal para algo como **Contexto 36% · 32k**. A origem
   técnica da capacidade e a contagem detalhada podem continuar em `/status`.
2. Usar títulos curtos durante geração, como **Respondendo…** e **Investigando…**,
   com um indicador de tempo. Guardar mensagens de revisão e protocolo nos detalhes.
3. Agrupar URL personalizada, TLS e teste avançado de ferramentas no cadastro,
   mantendo os campos principais mais rápidos de percorrer.
4. Usar hierarquia de atenção consistente: resposta final em destaque, atividade e
   raciocínio discretos, cancelamento neutro e falhas que exigem ação em destaque.

Esses quatro itens também foram implementados. O motivo das revisões fica nos
detalhes expansíveis e no tooltip, mantendo o acesso por teclado. A resposta final
permanece uma mensagem própria, fora dos cards de raciocínio e de atividade.

## Reproduzir

Na raiz do checkout, com as dependências de desenvolvimento instaladas:

```sh
.venv/bin/pytest -q tests/test_tui.py tests/test_provider_ui.py tests/test_processing_ui.py tests/test_ux_audit_fixes.py
.venv/bin/python docs/audits/2026-10-21-ui-ux/reproduce.py
.venv/bin/python docs/audits/2026-10-21-ui-ux/reproduce.py --output docs/audits/2026-10-21-ui-ux/after --verify
```

O segundo comando grava SVGs, texto renderizado e observações JSON neste diretório.
Os PNGs selecionados foram renderizados dos SVGs com Inkscape e inspecionados
visualmente. O resultado dos testes foi `64 passed in 146.32s` em Python 3.12/Linux.
O script adicional também passou em Ruff. Os 49 registros incluem repetições da
matriz de dimensões; não representam 49 jornadas distintas.

Os testes de regressão adicionais estão em `tests/test_ux_polish.py`: verificam
texto renderizado, preservação de texto/cursor durante retry, resultados tardios
em cadastro/edição/atualização/seleção, conflito de configuração concorrente,
redimensionamento, estados de recuperação e navegação até perfis já cadastrados.
