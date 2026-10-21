# Qualidade, provedores reais e confiabilidade

## Verificações automáticas

O CI de cada push/PR executa Ruff e a suíte em Python 3.11, 3.12 e 3.13.
Python 3.12 também publica cobertura de linhas e branches em XML/HTML, sem um
percentual mínimo artificial. O objetivo é encontrar caminhos importantes sem
validação. Instale `.[dev]` para usar `pytest-cov` localmente:

```bash
pytest -q --cov --cov-report=term-missing --cov-report=html:.codaro/coverage
```

O job `retrieval-quality` executa os casos versionados de
`evaluations/cases.json`, usando o índice real, sem chamar modelos. Uma regressão
nas expectativas falha o job; o relatório JSON fica disponível como artifact.
Os testes de workflow cobrem aplicação, conflito, rejeição, cancelamento e
validação de código com modelos simulados. Isso permite reproduzir falhas sem
depender da disponibilidade ou da variabilidade de um provedor.

O job de pacote mantém a matriz Linux/macOS/Windows. `runs-on: matrix.os` é
intencional e necessário para executar nos três sistemas.

## Aceitação com modelos reais

Os testes em `tests/live` ficam desabilitados por padrão. A seleção explícita de
um perfil BYOK autoriza as chamadas, que podem consumir créditos. São enviados
somente projetos sintéticos de teste. O perfil selecionado na aplicação não muda.

```bash
# Perfis previamente cadastrados e com modelo selecionado na UI ou CLI:
pytest tests/live -q --live-provider local --live-provider hpc

# Inclui edição e execução de comandos nas cópias temporárias das fixtures:
pytest tests/live -q --live-provider local --live-execution

# Relatório com métricas e tempos, sem necessidade de imprimir o prompt completo:
pytest tests/live -q --live-provider local --junitxml=.codaro/live.xml -o junit_family=legacy
```

São verificados catálogo/metadados, resposta em streaming, separação do raciocínio,
ciclo nativo de ferramentas, leitura do repositório e recuperação da conversa
após retirar seu conteúdo do contexto recente. Com `--live-execution`, todos os
casos de `evaluations/workflows.json` rodam em cópias descartáveis: o agente só
recebe aprovação para os arquivos e comandos declarados no caso. Os testes
existentes verificam correções e revalidação de implementações que falharam.

O relatório registra métricas de chamadas, ferramentas, repetições, tokens,
compactações e diagnóstico nos cenários do agente; o teste de streaming mede
quantidade de fragmentos e tempo até o primeiro conteúdo. APIs que retornam JSON
completo em vez de streaming podem produzir um único fragmento: ele é registrado,
sem alegar que houve entrega incremental. Raciocínio é opcional no protocolo.

Para uma conversa mais longa, o benchmark existente mantém múltiplos turnos:

```bash
python scripts/benchmark_session.py --repo examples/demo --rounds 2 --allow-model-calls --output .codaro/session-benchmark.json
```

Esse benchmark usa o provedor ativo, envia o conteúdo desse projeto e atualiza
seu diagnóstico local. Compare o mesmo conjunto de casos entre versões/modelos;
conclusão técnica e correspondência textual não substituem revisão semântica.

### Execução manual no GitHub

O workflow `Live provider acceptance (manual)` só é iniciado por
`workflow_dispatch`; não recebe credenciais por eventos de PR. O ambiente
`live-providers` pode exigir revisores antes da execução. Há duas opções:

- `ollama`: inicia Ollama em contêiner local descartável e baixa o modelo informado.
  O padrão é `llama3.2:3b`; não exige chave, mas utiliza CPU, rede e tempo do runner.
- `openai` ou `anthropic`: utiliza os secrets `CODARO_LIVE_BASE_URL` e
  `CODARO_LIVE_API_KEY` do ambiente. Informe o ID exato do modelo. Para compatíveis,
  use `openai` e a URL completa, incluindo `/v1` quando necessário.

O resultado é um artifact JUnit com retenção de sete dias. A execução paga e os
benchmarks de implementação são opcionais; nenhum resultado real é presumido.

Para automação fora do GitHub, `--live-provider env` usa `CODARO_BASE_URL`,
`CODARO_MODEL`, `CODARO_API_KEY` e as opções de contexto/TLS já documentadas.
`CODARO_TEST_API_STYLE` escolhe `openai`, `ollama` ou `anthropic` apenas nesses
testes. Para uso diário, prefira o cadastro de perfis pela aplicação.

## Cancelamento e dados sensíveis

`llm/transport.py` mantém HTTP assíncrono por trás da interface síncrona dos
adaptadores. Um canal limitado transfere os bytes; parsing, callbacks, atividades
e gravação do diagnóstico continuam na thread solicitante. O cancelamento/prazo
interrompe a espera por conexão, cabeçalhos ou corpo e fecha o transporte.
O catálogo também utiliza esse transporte. Transporte injetado por testes mantém
seu próprio contrato de I/O. A desconexão do cliente não garante que o servidor
interrompa imediatamente sua computação.

Os testes cobrem esperas paradas, fechamento do transporte e entrega incremental.
Também há testes com sockets HTTP locais; em ambientes que proíbem sockets, eles
são marcados como indisponíveis, enquanto os contratos assíncronos continuam
rodando. Não há fallback de execução sem aprovação.

`redaction.py` acrescenta reconhecimento conservador de chaves de provedores,
tokens GitHub, identificadores AWS, Bearer, atribuições explícitas de credenciais
e chaves privadas PEM. O mascaramento vale para conteúdo enviado ao modelo,
sessões e diagnóstico. Credenciais configuradas continuam disponíveis somente nos
cabeçalhos de autenticação necessários à API. O conteúdo original em disco não é
alterado pelo mascaramento; hashes e identificadores comuns são preservados.

Isso não detecta todos os segredos possíveis. Campos reconhecidos podem ser
mascarados também em exemplos; alterações que dependam de um trecho mascarado
podem precisar de revisão manual. Mantenha `.codaro/` fora do Git.

## Organização e compatibilidade

- `agent/lifecycle.py`: transições de sessão, modo, provedor e calibração.
- `agent/reviews.py`: aprovação, aplicação e registro das alterações.
- `ui/provider_actions.py`: transições de telas e ativação de provedores.
- `intent.py`: reconhecimento compartilhado de pedidos, sem conceder permissões.

As interfaces públicas permanecem em `Agent` e `CodaroApp`. Os módulos legados
continuam reexportando os objetos canônicos, com testes de compatibilidade.
Novas dependências internas usam os pacotes canônicos. Uma remoção futura exige
migração documentada e release apropriada; não há warnings na conversa normal.

O reconhecimento de pedidos continua sendo uma heurística. Foram cobertos mais
verbos de mudança, incluindo remoção, renomeação, otimização e migração em pt/en,
com o mesmo critério no roteamento e no registro das tarefas. Perguntas gerais
continuam aceitas sem citações obrigatórias. Digests, aprovações e resultados de
validação permanecem as verificações estruturais para conclusão de mudanças.
