# Revisão final da proposta de UI/UX

Data: 21/10/2026. Escopo: proposta navegável e maquetes, incluindo os 46 cenários. A aplicação não foi modificada nesta revisão.

## Ajustes concluídos

| Ponto | Resultado |
| --- | --- |
| Versões concorrentes | Uma proposta consolidada; 24 imagens antigas removidas, com registro em `cleanup.json` |
| Navegação de configurações | Retorna à conversa de origem, mantendo rascunho e pergunta pendente |
| Modo de trabalho | Menu compacto ancorado ao indicador, descrição em uma linha e confirmação por Aplicar/Enter |
| Plano de execução | Lista fixa junto ao input, aberta inicialmente e recolhível, com rolagem própria; permanece até concluir ou cancelar explicitamente o plano |
| Aprovação de edições | Escolha explícita entre pedir autorização e aprovar edições do projeto nesta conversa; comandos continuam com confirmação |
| Modelos e contexto | Orçamento, detalhes e status acompanham o modelo selecionado; ausência de metadados explícita |
| Formulário do provedor | URLs padrão correspondem ao provedor; cancelar teste mantém os campos; retorno coerente do catálogo |
| Preferências | Campos essenciais funcionam na simulação; salvar confirma e cancelar restaura |
| Feedback | Mensagens curtas na linha de estado; sem notificações sobrepostas às respostas |
| Etapas | Conclusão deixa de exibir uma ação ainda em andamento |
| Decisões e planos | O plano respeita a resposta escolhida; rejeição de ação tem mensagem própria |
| Teclado | Comandos durante execução, Esc com restauração do rascunho, Tab em lista vazia e composição de texto tratados |
| Leitura e foco | Posição de leitura preservada entre configurações; seleção permanece visível nas listas |
| Aparência | Contraste dos textos secundários, quebra de linhas e ações em apresentações compactas refinados |
| Distribuição do protótipo | Reconstrução única atualiza catálogo, imagens, verificações e pacote sem logs temporários |

A limpeza preservou código da aplicação, anexos do usuário e evidências históricas de auditorias. Essas evidências não são alternativas ativas da proposta.

## Verificação

- `node validate.mjs`: 37 verificações passaram, incluindo renderização lógica dos 46 estados.
- Os casos cobrem comandos, modelos, modos, perguntas, aprovações, preferências, rascunho, cancelamento e credenciais fictícias.
- SVGs são analisados como XML; HTML e pacote são verificados na reconstrução.
- Textos secundários usam contraste mínimo de 4,5:1 sobre os três fundos principais da proposta.
- As imagens foram revisadas visualmente como maquetes vetoriais.
- O Chromium instalado encerrou no sandbox, incluindo erro de `setsockopt: Operation not permitted`; não houve verificação visual em navegador nem em terminais reais nesta etapa.

## Critérios de adoção na CLI

A proposta está consolidada como referência de implementação. Para concluir a adoção, verificar estes resultados na aplicação:

1. Tela inicial limpa; nenhum texto desaparece ou altera dimensões em hover/foco.
2. Pergunta, opções e input no mesmo componente; resposta livre e seleção por teclado; pausa sem resposta presumida.
3. Atividade, raciocínio recebido e resposta final separados; streaming contínuo na área da resposta.
4. Configurações preservam conversa, foco, posição de leitura, rascunho e perguntas pendentes; Cancelar não aplica preferências.
5. Modos e modelos trocam somente após confirmação e para pedidos seguintes; aprovações permanecem independentes.
6. Metadados do provedor e orçamento da sessão chegam a todas as telas pela mesma fonte; limites desconhecidos são identificados.
7. Validar a aprovação automática apenas para criação e edição permitidas no projeto. Comandos e ações destrutivas continuam com autorização; nova conversa restaura o padrão.
8. Validar o plano fixo junto ao input e o chat acessível acima, inclusive durante perguntas e em terminais pequenos. Preservar o recolhimento manual e manter a lista ativa entre turnos, configurações, erros e interrupções; remover somente após concluir ou cancelar explicitamente o plano. Atualizar progresso pelos eventos reais do agente.
9. Testar conexão tem estados de início, andamento, cancelamento, sucesso e falha, sem bloquear a interface.
10. Aprovações exibem a ação exata e resultado; rejeitar ou fechar não executa o comando nem aplica o diff.
11. Verificar 60×20, 80×24, 120×35 e 160×50, com listas longas, caminhos grandes e texto multilinha. O input e as ações devem permanecer acessíveis.
12. Verificar Linux e macOS com terminais reais; mostrar atalhos que o terminal efetivamente entrega à aplicação.
13. Confirmar rolagem do histórico e indicador de novas mensagens, sem interromper leitura nem mover o foco.
14. Validar ao menos um fluxo real completo: investigar → perguntar → planejar → aprovar → executar → testar → concluir, incluindo cancelamento e recuperação de conexão.

Não há novo bloqueio de decisão visual: os itens pendentes acima são critérios de implementação e validação nativa.
