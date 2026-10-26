# Correções de confiabilidade — 2026-10-26

Trabalho sobre o workspace mais recente, incluindo as alterações locais que ainda
não estavam na branch `develop` do GitHub. Este relatório acompanha a publicação
dessas alterações na branch `develop`.

## Sessões e decisões

- A UI e o agente compartilham `Conversation` e a mesma instância de `SessionStore`.
  A conclusão do agente não apaga as decisões salvas pela UI nem duplica turnos.
- O histórico durável tem seu próprio limite de 50 turnos/512 KB, independente do
  histórico de trabalho reduzido para enviar ao modelo. O formato anterior continua aceito.
- Decisões confirmadas entram no contexto com orçamento limitado. Respostas longas
  permanecem completas no armazenamento e podem ser recuperadas em páginas.
  A busca/leitura consulta também as decisões da sessão, mesmo após a retenção do
  arquivo de memória remover seu registro do índice. Respostas não concedem permissões.
- A importação de memória usa a sessão selecionada; uma conversa adicional não
  importa acidentalmente o histórico da conversa original.
- A criação prepara e valida a sessão de destino antes de revogar permissões ou
  rejeitar propostas. Falhas de capacidade, gravação e ativação preservam o estado atual.
  Se encerrar a tarefa antiga falhar depois da troca, a UI continua ligada à nova
  sessão e informa o problema de encerramento.
- Arquivamento preserva os dados e libera vagas: até 30 sessões disponíveis e 300
  entradas no catálogo. A sessão ativa precisa ser trocada antes de ser arquivada.

Os testes de regressão estão em
[`test_conversation_lifecycle.py`](../../../tests/test_conversation_lifecycle.py).

## Organização e qualidade

- `conversation.py`: escrita compartilhada, decisões e projeção limitada no contexto.
- `agent/capabilities.py`: seleção progressiva de ferramentas e instruções.
- `agent/context_recovery.py`: compactação de lotes completos do protocolo.
- `ui/conversations.py`: retomada, limpeza e navegação de conversas.

O controlador continua responsável pela coordenação principal. Essa extração reduz
a concentração sem mover o loop inteiro para outro arquivo ou remover compatibilidade.

A avaliação de busca passou de três para seis casos. Os três benchmarks de
implementação cobrem autorização, paginação e mesclagem de configuração. O replay
com respostas simuladas executa o agente, aprovações, correção e testes nas cópias
descartáveis, incluindo uma validação inicial que falha antes da correção.

O workflow de release depende dos mesmos checks do CI. As novas capturas da UI
são geradas como artifacts, preservando evidências históricas em disco. Procedimentos
e regras de proteção recomendadas estão em [`QUALITY.md`](../../QUALITY.md).

## Validação executada

Ambiente local: Linux, Python 3.12.

- Suíte completa: **721 passaram, 18 pulados**, em 180,28 segundos.
- Ruff e formatação: aprovados.
- Avaliação de busca: **6/6** com índice real, sem chamadas ao modelo.
- Wheel e sdist: build e `twine check` aprovados.
- Pacote instalado fora do checkout: CLI, imports, CSS, agente, diagnóstico e UI aprovados.
- Capturas Textual headless: 12 estados/tamanhos combinados em SVG e texto,
  com dados sintéticos, em 80×24 e 120×35; uma captura também foi inspecionada visualmente.
- YAML dos três workflows e dependências de qualidade da release: verificados.

## Limites desta validação

Não houve inferência com provedores reais nem execução local da matriz macOS/Windows.
Os testes reais continuam opt-in e receberam cenários de retomada de decisão e
leitura paginada de arquivo grande. Os workflows modificados ainda precisam rodar
no GitHub após a publicação das alterações.

A proteção administrativa de `develop` não foi aplicada: a integração GitHub
disponível não oferece alteração dessas regras. Os checks a exigir estão documentados.
Capturas headless ajudam a revisar a interface; não substituem uso em terminais reais.
