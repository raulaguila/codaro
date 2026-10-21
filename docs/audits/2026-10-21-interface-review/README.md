# Revisão da interface: hover, contexto e composição da entrada

## Escopo e evidência

Foi executada a interface Textual real em modo headless no Linux/Python 3.12,
com repositórios temporários, catálogo HTTP simulado e respostas sintéticas.
O código da aplicação corresponde ao conteúdo publicado na `develop` em
`7f32d9cffe569b6f732e8e1347c45de77faf11e2`; esta revisão não modifica esse código.
Não houve conexão a Ollama/HPC nem validação em terminais nativos de macOS/Windows.

Foram registrados **89 estados** em 60×20, 80×24, 120×35 e 160×50:
abertura, hover e foco de cada botão inicial, seleção de modelo/contexto,
autocompletar, entrada multilinha, cadastro, modelos, gerenciamento de provedores,
preferências, nova conversa, revisão de edição, aprovação de comando/escopo,
paleta, streaming, resposta final, raciocínio expandido, TLS inseguro e erro 401.

Cada estado tem SVG exportado pelo Textual e transcrição do compositor.
Há 12 PNGs selecionados, renderizados com Inkscape. A moldura do SVG é decorativa;
não identifica o sistema operacional usado no teste. Medidas e visibilidade estão
em [observations.json](observations.json). Reprodução:

```bash
.venv/bin/python docs/audits/2026-10-21-interface-review/reproduce.py
```

## Achados e prioridades

| ID | Prioridade | Observação reproduzida | Mudança recomendada |
| --- | --- | --- | --- |
| UI01 | Alta | `Configurar provedor` perde o texto no hover **e no foco**, nas quatro dimensões. A altura externa passa de 1 para 2; a altura interna fica zero. | Fixar geometria entre estados. Usar botão compacto sem borda que consuma espaço, com mudança de cor/estilo para foco; ou reservar a mesma borda desde o estado normal. |
| UI02 | Alta | Em 60×20 e 80×24, os três atalhos iniciais passam de altura 1 para 3 no hover, e os textos deixam de aparecer por recorte. Em telas maiores o grupo já reserva três linhas e os rótulos continuam visíveis. | Adaptar altura dos próprios botões, não somente do contêiner. Garantir dimensões estáveis em normal, hover, foco, pressionado e desabilitado. |
| UI03 | Média | Depois de selecionar modelo e iniciar uma chamada, aparecem `Contexto: 131072 tokens` na conversa e `Contexto ≈ 13% · 122.4k` junto ao input. O nome do modelo também se repete. | Mostrar contexto somente em um local persistente. Diferenciar entrada usada/limite de entrada; deixar janela total, reservas e origem em `/status` ou tooltip. |
| UI04 | Média | Contexto, estado, editor, dicas de edição e Footer formam sete linhas permanentes. Em 60×20 isso consome 35% da tela, antes do cabeçalho. | Um componente de composição com editor, uma linha de estado/contexto e uma linha de atalhos contextuais. Buscar cinco linhas no uso normal e quatro no compacto, sem reduzir legibilidade. |
| UI05 | Média | Abrir `/` reduz o chat de 9 para 4 linhas em 60×20, e de 13 para 7 em 80×24. Menu e rodapé repetem orientações. | Limitar opções pela altura real disponível, usar descrições curtas e substituir a ajuda normal pela ajuda da seleção enquanto o menu estiver aberto. Preservar mais da conversa e sua posição de rolagem. |
| UI06 | Média | O cabeçalho pode quebrar `TLS verificação ativa` entre duas linhas. Permissão por ação é mostrada também em Perguntar. Na condição insegura todo o resumo ganha destaque de alerta. | Priorizar projeto, modelo e modo; encurtar caminhos reservando largura para os demais elementos. Exibir aprovação quando relevante e concentrar o alerta de TLS em um indicador específico. |
| UI07 | Média | A abertura oferece um botão de configuração de largura total mesmo com provedor/modelo já disponíveis. Confirmação de seleção usa o mesmo amarelo das falhas. | Priorizar o início do trabalho; trocar configuração por ação secundária `Trocar modelo` quando já configurado. Usar aviso neutro/sucesso para seleção e amarelo para situações que exigem atenção. |
| UI08 | Média | Durante a resposta há `Respondendo…` no card e novamente no status; cancelar aparece no status e no Footer. Raciocínio já fica separado da resposta final. | Consolidar fase, tempo e cancelar num local. Reservar a conversa para conteúdo e atividades recolhíveis; manter a separação correta entre raciocínio e resposta. |
| UI09 | Média | Em erro 401, título, mensagem técnica e identificação do modelo se acumulam; o botão `Configurar` ocupa toda a largura por três linhas. O status volta a `Pronto`, o que pode parecer contraditório. | Uma mensagem principal orientada à ação, detalhes expansíveis e ações compactas. Usar estado que descreva a tentativa falha sem desabilitar o próximo rascunho. |
| UI10 | Melhoria | Formulários mantêm ações visíveis nas quatro dimensões, mas no cadastro em 60×20 as ações ocupam seis linhas e só o começo dos campos cabe na tela. | Reduzir área de ações e padronizar hierarquia e rótulos entre salvar, testar e cancelar. Preservar ações fixas, rolagem dos campos e confirmação explícita para operações. |

## Causa do hover

Em `src/codaro/ui/styles.tcss`, o estado inicial usa `border: none`, enquanto
`#welcome Button:hover, #welcome Button:focus` introduz `border: round $accent`.
No botão de configuração de uma linha isso elimina o espaço para o rótulo.
Nos atalhos, o tamanho natural cresce para três linhas. O modo compacto de
`update_session_header` reduz apenas `#suggestions` para uma linha, deixando os
filhos recortados. A regra geral `#welcome Button` também domina a largura
`auto` pretendida por `#configure-start` devido à especificidade do seletor.

O Textual instalado oferece `Button(..., compact=True)`. É uma alternativa
disponível, desde que as regras de hover/foco não voltem a introduzir bordas
incompatíveis. O critério de aceitação deve ser geométrico e visual, incluindo
o texto efetivamente renderizado; verificar só `region` visível não detecta
o rótulo escondido por uma área interna vazia.

## O que significa a diferença no contexto

Nesta reprodução, a janela do modelo é 131.072 tokens. A reserva interna de saída
é 8.192 e a margem é 512; o orçamento de entrada resulta em **122.368** tokens.
Com estimativa de 16.000 tokens, o indicador mostra 13%. São valores coerentes,
apresentados sem distinção suficiente. Esta reprodução não indica erro na
descoberta da janela de um provedor real, pois os metadados são sintéticos.

Uma apresentação mais clara seria:

```text
Pronto                                      Contexto 16k/122k · 13%
› Pergunte ou peça uma mudança…
Enter enviar · / comandos · @ arquivo                  Ctrl+P menu
```

Janela total, reserva de saída, margem, uso informado/estimado e origem da
informação continuam disponíveis em `/status`. A entrada permanece estimada
quando a API não oferece usage; o indicador não deve sugerir precisão maior.

## Organização proposta

1. Cabeçalho compacto com projeto, modelo e modo; detalhes administrativos em
   `/status` e paleta. Alerta de TLS inseguro continua visível.
2. Conversa ocupa o espaço flexível, com resposta final em área própria e
   atividades/raciocínio recolhidos por padrão.
3. Um único componente de composição administra editor, sugestões, estado,
   contexto e ajuda. Em vez de constantes para contar linhas, usar o espaço real
   e a altura dos componentes ao adaptar o layout.
4. Ajuda muda conforme a interação: digitação, seleção de comandos/arquivos,
   geração ou revisão. Evitar exibir todos os atalhos simultaneamente.
5. Estados normal, hover, foco, pressionado e desabilitado mantêm a mesma caixa.
   Cor e estilo identificam interação; tamanho não muda ao passar o mouse.

## Comportamentos preservados

- Todas as ações dos oito tipos de modal estavam visíveis nas dimensões medidas.
- A paleta abriu e os modais foram fechados com Esc.
- O rascunho permaneceu editável durante a geração e foi preservado após a resposta.
- A resposta final foi montada diretamente na conversa, separada do raciocínio.
- Ações de edição/comandos continuaram exigindo revisão explícita; o teste
  fechou essas telas sem aplicar alterações nem executar comandos.

## Critérios para a próxima implementação

- Hover e foco não alteram as caixas dos botões nem deslocam elementos vizinhos;
  rótulos continuam legíveis nas quatro dimensões.
- Contexto aparece uma vez; a diferença entre janela total e entrada disponível
  é explicável em detalhes sem mensagens repetidas na conversa.
- Autocompletar e entrada multilinha preservam espaço útil do chat, cursor e
  rolagem; ajuda não é duplicada entre menu e Footer.
- Estado e cancelar não se repetem durante streaming. Erros e confirmações
  possuem peso e cores diferentes.
- Testes de UI passam a incluir hover, foco e conteúdo pintado, além de geometria.
- Uma validação posterior em terminal nativo de Linux e macOS deve conferir
  fonte, contraste, mouse e atalhos do sistema; exportação SVG não substitui isso.

## Prints selecionados

- [Abertura antes do hover](welcome-60x20.png)
- [Configurar provedor sem texto durante hover](hover-configure-start-60x20.png)
- [Atalho Explorar recortado em 80×24](hover-suggest-explore-80x24.png)
- [Contexto repetido com dois limites sem explicação](context-duplicate-80x24.png)
- [Sugestões deixam quatro linhas para o chat](completion-60x20.png)
- [Editor multilinha](multiline-60x20.png)
- [Estado repetido durante streaming](stream-live-100x30.png)
- [Resposta final separada do raciocínio](stream-final-100x30.png)
- [Cadastro compacto](provider-60x20.png)
- [Preferências](preferences-60x20.png)
- [Recuperação de autenticação](authentication-error-80x24.png)
- [Alerta de TLS no cabeçalho](insecure-header-100x30.png)
