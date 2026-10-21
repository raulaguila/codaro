# Fidelidade da CLI ao protótipo aprovado

A primeira etapa reproduzia parte dos fluxos, mas ainda mantinha a composição
visual anterior. Esta revisão usa a proposta consolidada como referência direta
para a interface nativa, preservando o protótipo aprovado.

| Diferença encontrada | Correção |
| --- | --- |
| Marca e sessão em linhas separadas | Cabeçalho com marca/projeto à esquerda e modelo/modo à direita |
| Menu cobria o histórico com um fundo opaco | Fundo global restrito à tela principal; popover transparente com chat visível |
| Menu de modo grande e com ações redundantes | Três linhas, permissões recolhíveis, ajuda e uma ação Aplicar |
| Mensagem sem identificação do usuário | Rótulo Você e fundo discreto compartilhados entre envio e retomada |
| Atividade e raciocínio em barras independentes | Uma seção Etapas por turno, com até dois resumos observáveis e detalhes separados internamente |
| Geração ainda tinha aparência de card | Área Codaro própria para streaming; prévias intermediárias arquivadas nas etapas |
| Plano e input sem a divisão aprovada | Plano fixo, estados com cores semânticas, dois separadores e indicador › no input |
| Pergunta ainda não integrada | Pergunta, opções verticais e input único no mesmo componente, com Pausar e Enviar |
| Modelo escolhido em dropdown | Campo de busca e lista permanente; navegação destaca, Enter/Usar modelo confirma |
| Cadastro carregado e com botões em duas linhas | Tipo e chave prioritários, detalhes em Avançado, ações fixas em uma linha |
| Diálogos com estilos diferentes | Bordas discretas, ações planas e destaque consistente da confirmação |

## Evidências

- [Comparação lado a lado](comparison.png): maquete aprovada à esquerda, captura real Textual à direita.
- [Chat e plano](plan-chat-120x35.png).
- [Menu sobre a conversa](mode-120x35.png).
- [Pergunta no input compartilhado](question-120x35.png).
- [Busca e seleção de modelos](models-120x35.png).
- [Cadastro do provedor](provider-120x35.png).

`reproduce.py` captura 36 estados nativos em 60×20, 80×24, 120×35 e 160×50,
usando apenas dados sintéticos e HTTP simulado. `observations.json` registra a
geometria e visibilidade dos controles. As imagens nativas são exportadas do
renderizador da aplicação real; a imagem de comparação identifica a maquete
separadamente. O protótipo não foi alterado para acomodar a implementação.

## Interação e protocolo das perguntas

Na CLI interativa, a ferramenta `ask_user_question` recebe uma pergunta, explicação
opcional, até três sugestões e índice recomendado opcional. Ela aguarda a resposta
efetiva no mesmo loop de tool-calling e retorna `user_response` ao modelo.

Digitar tem prioridade sobre a opção destacada, incluindo respostas que começam
com `/`. Esc/Pausar não resolve a pergunta nem presume uma resposta. Ctrl+X
interrompe a geração, sem inventar um resultado. A espera humana não consome o
prazo de execução do agente. Responder não concede permissão de editar ou executar
comandos. O fluxo e os resultados continuam disponíveis no diagnóstico local.

## Reprodução e critérios

```bash
.venv/bin/pytest tests/test_prototype_fidelity.py tests/test_ui_redesign.py -q
.venv/bin/python docs/audits/2026-10-21-prototype-fidelity/reproduce.py
```

Os testes verificam hierarquia dos componentes, controles visíveis, pausa e resposta
real, cancelamento sem resposta presumida, busca sem consulta adicional à API,
confirmação explícita e o texto da conversa visível atrás do popover. A suíte geral
continua cobrindo streaming, aprovações, provedores e gerenciamento de contexto.

O terminal define a fonte e a resolução das células. A comparação valida organização,
cores, estados e interação; não trata a CLI como uma reprodução de pixels do HTML.
Aceitação em terminais físicos de outros sistemas e inferência com provedores reais
continuam separadas desta verificação local.
