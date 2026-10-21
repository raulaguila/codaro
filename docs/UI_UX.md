# Correções da auditoria de UI e UX do Codaro

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
