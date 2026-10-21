# Adoção nativa da proposta de UI/UX — primeira etapa

Esta entrega começa a implementação da proposta aprovada na CLI Textual.
As capturas deste diretório vêm da aplicação real em modo headless, com tarefas,
mensagens e provedores sintéticos. Não são maquetes do protótipo nem testes de
inferência contra servidores reais.

## Entregue

- Início limpo após configurar um modelo; cadastro guiado mantido no primeiro acesso.
- Composer próprio com input, uma linha de contexto/estado e ajuda contextual.
- Plano fixo junto ao input, independente da rolagem e dos turnos da conversa.
- Plano aberto inicialmente; altura limitada e rolagem própria; recolhimento manual preservado.
- Interromper geração bloqueia a tarefa para retomada; somente cancelar explicitamente
  ou concluir a tarefa remove seu plano. Arquivos aplicados são mantidos.
- Seletor compacto de modo no cabeçalho e em `/mode` / Ctrl+P; navegar não confirma.
- Aprovação automática de criação/edição permitidas, limitada à conversa e revogável.
  Remoções/renomeações, comandos e integrações continuam com as autorizações existentes.
- Comandos com setas e Enter; Tab completa; Esc preserva o texto.
- Tema Codaro com cores semânticas para manter compatibilidade com outros temas.

Foram removidos o início com botões de sugestões, seus callbacks e estilos, o plano
montado como card no histórico e o rodapé redundante do framework. Os módulos públicos
de compatibilidade foram preservados: sua remoção é uma mudança de API separada.

## Validação e reprodução

```bash
.venv/bin/pytest tests/test_ui_redesign.py -q
.venv/bin/python docs/audits/2026-10-21-native-ui/reproduce.py
```

Resultado: 686 testes passaram e 16 foram ignorados; os 14 casos de aceitação nativa
também passaram na execução dirigida. Ruff, formatação, build do wheel e smoke test
do pacote instalado passaram. Saídas em [validation.txt](validation.txt).

A matriz cobre 60×20, 80×24, 120×35 e 160×50. Inclui plano com 24 etapas, rolagem do
histórico, recolhimento durante atualizações, limpeza de mensagens, cancelamento,
aprovação automática, navegação de comandos e preservação de rascunho. Ações de
confirmação do seletor permanecem visíveis ao expandir permissões em telas pequenas.

Consulte [observations.json](observations.json) para a geometria das 24 capturas e
[plan-chat-120x35.svg](plan-chat-120x35.svg) para o plano junto ao input.
Terminais físicos de macOS/Windows, leitores de tela e provedores reais ainda precisam
de aceitação nos respectivos ambientes.

## Próximas etapas da adoção

1. Integrar perguntas estruturadas do agente ao mesmo input, com opções verticais,
   resposta livre, pausa e retomada sem resposta presumida.
2. Reunir resumos de atividade por turno, refinando a hierarquia entre ferramentas,
   raciocínio disponibilizado pelo provedor e resposta em streaming.
3. Aplicar os detalhes visuais da proposta aos seletores de provedor/modelo,
   preferências, revisões de alterações e recuperação de falhas.
4. Finalizar os cenários restantes da proposta com testes nativos e acessibilidade.

O [protótipo consolidado](../../design/ui-prototype/README.md) continua sendo a
referência desses próximos estados. A implementação atual não é declarada como adoção
completa dos 46 cenários.
