# Estrutura do projeto

O Codaro organiza o agente, os adaptadores de IA e a interface em pacotes separados.
Cada pacote possui uma responsabilidade; os dados persistidos e os comandos públicos
mantêm seus formatos anteriores.

```text
src/codaro/
├── cli.py                 # Entrada Typer e comandos centrais
├── cli_commands/          # Grupos de provedores, modelos e integrações
├── agent/
│   ├── controller.py      # Estado da sessão, permissões e loop com o modelo
│   ├── requests.py        # Payload canônico para orçamento, trace e transporte
│   ├── excerpts.py        # Recortes explícitos com linhas e fim de logs
│   ├── prompts.py         # Instruções compartilhadas e dos modos
│   ├── schemas.py         # Contratos das ferramentas internas
│   ├── arguments.py       # Validação de argumentos
│   ├── execution.py       # Despacho e execução das ferramentas
│   ├── reading.py         # Contexto inicial e visão geral do projeto
│   ├── results.py         # Ajuste dos resultados ao orçamento
│   ├── messages.py        # Classificação e processamento de mensagens
│   ├── output.py          # Recuperação e continuação de saída truncada
│   ├── events.py          # Eventos consumidos pelas interfaces
│   └── presentation.py    # Títulos e resumos das atividades
├── llm/
│   ├── config.py          # Settings, ambiente e reserva de contexto
│   ├── profiles.py        # Persistência e seleção de provedores BYOK
│   ├── catalog.py         # Descoberta de modelos e metadados
│   ├── endpoints.py       # URLs e normalização dos provedores
│   ├── factory.py         # Escolha do adaptador
│   ├── openai.py          # API OpenAI compatible, OpenAI, Groq e Gemini
│   ├── ollama.py          # Protocolo nativo Ollama
│   ├── anthropic.py       # Protocolo nativo Anthropic
│   ├── protocol.py        # Payloads, validação de mensagens e limites
│   ├── streaming.py       # SSE, fragmentos e cancelamento
│   └── errors.py          # Erros normalizados dos provedores
└── ui/
    ├── app.py             # Ciclo de vida e coordenação da interface
    ├── composer.py        # Campo de entrada e atalhos de edição
    ├── widgets.py         # Geração, atividades e propostas
    ├── streaming.py       # Renderização de eventos e streaming do chat
    ├── reviews.py         # Revisão de alterações e aprovações
    ├── providers.py       # Cadastro, teste e seleção de provedores
    ├── integrations.py    # Cadastro de MCP e plugins
    ├── dialogs.py         # Diálogos de sessões e funcionalidades
    ├── palette.py         # Menu de comandos
    ├── formatting.py      # Apresentação de caminhos
    └── styles.tcss        # Estilos Textual distribuídos com o pacote
```

Os serviços de domínio continuam em módulos próprios na raiz: `repository.py`,
`index.py`, `context.py`, `continuity.py`, `memory.py`, `tasks.py`, `edits.py`,
`commands.py`, `policies.py`, `sessions.py`, `trace.py` e as integrações existentes.
Eles são compartilhados pela CLI, pelo agente e pela interface; não precisam conhecer
os componentes visuais.

`trace_metrics.py` mantém agregados independentes da retenção de snapshots e adapta
os relatórios dos avaliadores. `command_environment.py` controla variáveis herdadas;
`command_sandbox.py` constrói a execução isolada opcional, com falha fechada no modo
obrigatório. `scripts/package_smoke.py` valida a distribuição fora do código-fonte.

## Fluxo e dependências

```mermaid
flowchart LR
    CLI[CLI e grupos de comandos] --> UI[Interface Textual]
    CLI --> Agent[Agente]
    UI --> Agent
    Agent --> Domain[Repositório, índice, contexto, tarefas e alterações]
    Agent --> LLM[Adaptadores de IA]
    UI --> Profiles[Perfis e catálogo de modelos]
    Profiles --> LLM
```

O agente recebe a solicitação, prepara o contexto e conduz o loop. Os adaptadores
traduzem o protocolo de cada API e normalizam erros. As ferramentas passam por
validação e pela política de aprovação antes da execução. A UI consome eventos e
coleta as decisões do usuário. A recuperação de saída truncada controla somente
tentativas e texto preservado; o controlador continua responsável pelas chamadas
ao modelo e pelos efeitos na tarefa.

`agent` não depende de Textual ou dos componentes de `ui`. `llm` não importa o
agente nem as interfaces. Dentro dos pacotes, use os módulos canônicos; os módulos
antigos de compatibilidade não devem virar novas dependências internas.

## Onde implementar mudanças

| Mudança | Local principal |
| --- | --- |
| Instruções do agente | `agent/prompts.py` |
| Ferramenta interna | `agent/schemas.py`, `arguments.py` e `execution.py` |
| Continuação de resposta | `agent/output.py` |
| Protocolo de um provedor | Adaptador em `llm`, registrado em `factory.py` |
| Descoberta de modelos | `llm/catalog.py` |
| I/O HTTP cancelável | `llm/transport.py` |
| Sessão, modo, provedor e calibração do agente | `agent/lifecycle.py` |
| Gravação compartilhada e decisões da conversa | `conversation.py`, `sessions.py` |
| Catálogo e arquivamento de sessões | `session_catalog.py` |
| Seleção de ferramentas e perfis de contexto | `agent/capabilities.py` |
| Compactação de lotes de ferramentas concluídos | `agent/context_recovery.py` |
| Aprovação e aplicação de alterações | `agent/reviews.py` |
| Reconhecimento de pedidos | `intent.py` |
| Cadastro e seleção persistida | `llm/profiles.py` |
| Telas e ativação do provedor na UI | `ui/provider_actions.py` |
| Retomada, limpeza e navegação das conversas | `ui/conversations.py` |
| Comando do menu Ctrl+P | `ui/palette.py` |
| Input e informações junto ao campo | `ui/composer.py` |
| Menu de comandos e arquivos junto ao input | `ui/completions.py`, `interaction.py` |
| Funcionalidades e limites do projeto na UI | `ui/features.py`, `features.py` |
| Descoberta e cadastro de integrações na UI | `ui/integrations.py` |
| Plano fixo e projeção do progresso | `ui/plan.py` |
| Seletor de modo e confirmação de aprovação automática | `ui/modes.py` |
| Paleta visual padrão | `ui/theme.py` |
| Hierarquia de mensagens e etapas | `ui/messages.py`, `ui/stages.py` |
| Perguntas interativas e espera por resposta real | `agent/questions.py`, `ui/questions.py` |
| Lista pesquisável de modelos | `ui/model_list.py` |
| Velocidade estimada e métricas de geração | `ui/generation.py`, `agent/events.py` |
| Ações de resposta e prévia local de arquivos | `ui/message_actions.py` |
| Pesquisa, retomada e arquivamento na UI | `ui/sessions.py` |
| Componente ou tela | `ui/widgets.py`, `reviews.py` ou módulo da tela |
| Aparência | `ui/styles.tcss` |
| Novo grupo de comandos CLI | `cli_commands`, com registro explícito em `cli.py` |

Os imports públicos anteriores continuam funcionando, incluindo `codaro.agent`,
`codaro.provider`, `codaro.providers` e `codaro.tui`. Os módulos de compatibilidade
reexportam os mesmos objetos, sem duplicar implementações. O executável continua
usando `codaro.cli:app`.

O controlador do agente e a aplicação Textual ainda concentram a coordenação dos
fluxos. Novas funcionalidades devem entrar nos módulos responsáveis, deixando
nesses dois arquivos apenas a integração e o estado compartilhado. Extrações futuras
precisam preservar cancelamento, limites de contexto, aprovação e a ordem dos eventos.

## Validação

```bash
pytest -q
ruff check src tests
ruff format --check src tests
python -m pip wheel . --no-deps -w /tmp/codaro-wheels
```

Os testes de arquitetura protegem as fronteiras de dependência. Os testes funcionais
cobrem o agente, os contratos HTTP, persistência, CLI e interação Textual headless.
Ao verificar uma distribuição instalada, confira também a inclusão de `styles.tcss`.
