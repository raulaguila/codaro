# Changelog

## 0.2.0

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
