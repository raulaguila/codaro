# Correções da auditoria de UI e UX do Codaro

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
| U12 Recuperação de erros | Perfil/modelo identificados; Repetir, Configurar e Outro modelo | Pergunta original recuperada e enviada novamente |
| U13 Modelo incompatível | Modelos sem ferramentas ocultos; capacidade desconhecida informada | Catálogo com chat e embedding |
| U14 Contexto invisível | Indicador persistente de estimativa, orçamento e origem | Geração, término e compactação |
| U15 Ruído no streaming | Prévia Markdown estável, raciocínio recolhível e resumo vazio oculto | Texto parcial, ferramenta, falha, cancelamento e resposta final única |
| U16 Idioma inconsistente | Paleta e ações principais em português, aliases técnicos mantidos | Placeholder e comandos localizados |
| U17 Rascunho bloqueado | Redação durante execução, envio simultâneo bloqueado | Rascunho permanece após término e só um turno é executado |

## Verificações

A suíte completa, Ruff, formatação e diff são executados na entrega. As reproduções visuais usam a aplicação Textual real, teclado, cliques e respostas controladas de provedores; não realizam chamadas pagas. Os testes de regressão estão em `tests/test_ux_audit_fixes.py`, além dos cenários existentes de TUI/provedores/workflow.

O cancelamento do teste de provedor libera a interface e evita aplicar resultados antigos. Uma operação HTTP síncrona que já aguarda dados ainda depende do retorno/timeout para liberar sua thread; não há promessa de encerramento imediato do socket. Operações que gravam perfis aguardam o resultado para preservar a consistência da configuração.

As capturas locais anteriores e posteriores permanecem em `docs/audits/2026-10-07-ui-ux/`. Terminais nativos de macOS/Windows, leitores de tela e inferência com provedores externos reais permanecem fora desta validação. A auditoria de segurança e maturidade geral tem escopo separado; esta entrega trata UI e UX.
