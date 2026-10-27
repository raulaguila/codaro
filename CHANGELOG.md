# Changelog

## 0.2.0

- Cópia de respostas/código usa clipboard nativo no macOS, Windows/WSL e Linux
  quando disponível, sem bloquear a UI. SSH mantém OSC 52, com retorno sem falsa
  confirmação e seleção manual como alternativa.

- Catálogo único de linguagens, nomes e aliases para listagem, grep e buscas
  lexical/semântica. Filtro OCaml inclui interfaces `.mli` sem alterar sua
  gramática. Inventário de suporte paginado com resumo, detalhes e cursores
  preservados quando o contexto reduz a página; documentação gerada do registro.

- Processo do agente em sequência cronológica, com pensamento, ferramentas e
  resultados preservados até a conclusão; resposta final separada e processo
  recolhível. Correção de eventos recebidos antes do primeiro refresh e de
  atualizações durante a desmontagem, com acompanhamento de rolagem agrupado.
- Stream com padrão de 16 MiB configurável por CODARO_MAX_STREAM_MB, limite por
  evento e parser incremental sem rescans de linhas a cada fragmento.
- Observação nativa de alterações do projeto, indexação por lotes e reconciliação
  periódica; cancelamento da listagem durante indexação/investigação.
- Revisão de operações interrompidas por /recovery, usando checkpoints e recibos
  existentes, sem repetir comandos. Fases tipadas incluem esperas explícitas.
- Qualificação completa por modelo nas fixtures, gate de release com implementação,
  benchmark comparativo de embeddings e limite de threads do encoder local.

- Histórico Markdown com renderização leve, montagem em lote ao retomar e
  cancelamento antecipado no Ctrl+Q, incluindo a listagem inicial de arquivos.
  Gravação de sessões grandes sem repetir a ocultação de credenciais a cada
  turno descartado; espera de encerramento de comandos com orçamento compartilhado.
  Benchmark reproduzível de UI e persistência sem chamadas de IA.

- Correções da auditoria de UI: cancelamento de jobs com confirmação do término,
  acesso local a saídas por `/jobs`, cofre consultado sob demanda e rótulos de
  transporte precisos. Perguntas/planos compactos, opções longas distinguíveis,
  autorizações com ações fixas e melhorias de contraste/densidade.

- Execução com fases observáveis, detecção de repetição de evidências e módulos
  separados para loop, aprovações, comandos e recuperação de lotes.
- Correção de diagnóstico LSP com servidor personalizado sem padrão embutido.
  Servidores e encoders reutilizados na sessão, documentos atualizados e
  aquecimento vetorial limitado em segundo plano.
- Substituições múltiplas em um arquivo observado; lotes com preflight,
  checkpoints e tentativa de rollback em falhas gerenciadas.
- Comandos com diretório revisado, execução em segundo plano, saída incremental,
  consulta e cancelamento de processos próprios da sessão.
- Descoberta de verificações de projetos, critérios vinculados a verificações e
  relatório por revisão. Comando bem-sucedido arbitrário não aprova um critério
  vinculado a outra verificação.
- Edição nativa Windows com preservação de bytes e proteção de caminhos;
  processos Windows associados a Job Objects antes de executar código.
- Caminhos de criação normalizados para Windows e saltos imediatos no plano,
  preservando a rolagem manual em atualizações da interface.
- Cofre nativo opcional para BYOK, migração explícita e exportação sem segredos.
- Inventário de capacidades por linguagem, fixtures Go/Node e verificação nativa
  de edição/processos em três plataformas no CI.
- Aceitação real semanal do protocolo com Ollama, gate para releases, políticas
  de atualização/migração e publicação PyPI opcional por Trusted Publisher.

## 0.1.1

Base alpha anterior: agente no terminal, provedores BYOK, modos de trabalho,
controle de contexto, sessões e recuperação multilíngue com mapa automático.
