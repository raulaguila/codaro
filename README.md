# Codaro CLI

Agente de desenvolvimento no terminal para perguntar, planejar e executar atividades em repositórios locais, com recuperação progressiva de contexto.

> Busca, planejamento persistente, alterações com revisão de diff, comandos com aprovação, validação e retomada de tarefas por projeto. Referências via LSP e interrupção imediata de conexões HTTP ociosas estão no roadmap.

Também estão disponíveis sessões independentes, undo/redo por interação, saídas
grandes recuperáveis, continuidade semântica opcional, exploração delegada somente
leitura, MCP, plugins explícitos, diagnósticos LSP opcionais e benchmarks de
implementação. Veja configuração, exemplos e limites em
[Plataforma do agente](docs/AGENT_PLATFORM.md).

No chat, Ctrl+P inclui **Sessões**, **Cadastrar MCP ou plugin**, **Desfazer
interação**, **Refazer interação** e **Funcionalidades e integrações**.

## Instalação

Requisitos: Python 3.11+ e [ripgrep](https://github.com/BurntSushi/ripgrep).

```bash
# Ubuntu/Debian
sudo apt install ripgrep
# macOS: brew install ripgrep

git clone https://github.com/raulaguila/codaro.git
cd codaro

python -m venv .venv
source .venv/bin/activate
# Versões usadas na auditoria
pip install -r requirements-dev.lock
pip install -e '.[dev]'
```

## Experimente sem configurar IA

```bash
codaro index --repo examples/demo
codaro search can_edit --repo examples/demo
codaro search 'editing permissions' --repo examples/demo --json
codaro read auth.py --repo examples/demo --start 4 --end 15
codaro doctor
```

O índice SQLite fica em `.codaro/index.sqlite3` dentro do repositório explorado. Cada execução atualiza somente arquivos alterados e remove entradas de arquivos apagados ou ignorados.

## Chat com Ollama

Instale e inicie [Ollama](https://ollama.com), e baixe um modelo que suporte chamadas de ferramentas:

```bash
ollama pull qwen2.5:7b
export CODARO_BASE_URL=http://localhost:11434/v1
export CODARO_MODEL=qwen2.5:7b

codaro ask 'Como validamos o acesso antes de editar um projeto?' --repo examples/demo
codaro chat --repo examples/demo

# Dentro de qualquer projeto, abra o chat no diretório atual
codaro .
# Ou informe outro diretório
codaro ./examples/demo
```

`codaro .` equivale a `codaro chat --repo .`: o diretório de trabalho do terminal se torna a raiz do projeto. O atalho também aceita caminhos relativos/absolutos e opções do chat, por exemplo `codaro . --read-only`.

O agente recebe a raiz absoluta do projeto em cada consulta e pode confirmá-la com `get_repository_info`. No chat, `/pwd` mostra essa raiz diretamente, sem chamar o modelo; no terminal, use `codaro pwd --repo /caminho/do/projeto`. A pasta de instalação do Codaro não define a pasta explorada.

O chat ocupa toda a largura do terminal, com mensagens compactas e entrada fixa embaixo. O cabeçalho mostra projeto, modelo, modo de edição e conexão; as atividades ficam agrupadas por pergunta e a barra de status mostra a ação em andamento. `Ctrl+L` limpa a conversa, `Ctrl+X` solicita cancelamento, `Ctrl+P` abre os comandos e `Ctrl+Q` encerra. O cancelamento HTTP é verificado entre fragmentos da resposta e chamadas de ferramentas; se o servidor estiver parado sem enviar dados, aguarda o próximo fragmento ou o timeout. A conversa é salva em `.codaro/session.json`; o fluxo da última investigação fica em `.codaro/prompt.json` para diagnóstico. O modelo deve suportar `tools` na API de chat completions; a confiabilidade das chamadas varia conforme modelo e servidor.

## Modos, planejamento e execução

Um único agente usa um prompt base e capacidades controladas pelo aplicativo. O modo muda as instruções e as ferramentas disponíveis; texto do modelo nunca concede permissões.

| Modo | Comportamento |
| --- | --- |
| **Perguntar (`ask`)** | Responde dúvidas e consulta código/conversa quando necessário; não altera arquivos do projeto nem executa comandos. |
| **Planejar (`plan`)** | Investiga arquitetura, registra etapas e critérios de aceite, sem alterações ou comandos. |
| **Executar (`execute`)** | Investiga, planeja atividades amplas, implementa, executa verificações autorizadas e corrige falhas. Perguntas simples continuam recebendo respostas simples. |

`codaro .` abre **Executar**, com aprovação por ação. `--read-only` seleciona Perguntar. Os modos de consulta também podem salvar conversa e planejamento em `.codaro`.

```bash
codaro . --mode ask
codaro . --mode plan
codaro ask 'Como funciona a autenticação?'
codaro plan 'Adicionar recuperação de senha'
codaro execute 'Execute o plano da tarefa ativa e valide o resultado'
# Compatibilidade: edit usa o mesmo ciclo de execute
codaro edit 'Corrija a validação de entrada'
```

No chat, `/mode ask|plan|execute` troca o modo; `/ask`, `/plan` e `/execute` são atalhos. A troca preserva conversa e tarefa, e revoga o escopo de aprovação anterior. O plano aparece em um bloco expansível. **Executar plano** seleciona Executar e preenche uma mensagem; Enter inicia a atividade. `/execute` também preenche esse rascunho quando existe uma tarefa. Trocas durante execução/revisão são bloqueadas.

O agente registra etapas (`todo`, `doing`, `done`) e critérios com `update_plan`, recupera páginas de tarefa com `get_task` e sinaliza plano/conclusão/bloqueio com `finish_task`. Uma conclusão com etapas pendentes ou sem validação da revisão atual é recusada pela ferramenta. O plano pode declarar `validation_commands` com os comandos exatos esperados; atualizar essa lista permite substituir verificações obsoletas após uma mudança de arquitetura, sem conceder autorização para executá-las. Perguntas simples podem concluir sem planejamento formal. O controlador informa pendências quando o modelo encerra a resposta antes de completar a atividade.

### Aprovação por ação ou por tarefa

**Por ação (`action`) é o padrão**: cada conjunto de diffs e cada comando exige revisão. Após a decisão, o resultado volta ao modelo pelo protocolo nativo; a mesma execução continua para validar ou registrar o bloqueio. Voltar/Escape na revisão integrada rejeita aquele conjunto; Enter não aprova por foco inicial.

**Por tarefa (`task`) é opcional**, com autorização explícita para caminhos relativos e comandos exatos. Permite criação, alteração, remoção e renomeação dentro dos caminhos revisados. Comandos são arrays de argumentos completos, sem correspondência por prefixo. Fora do escopo, a ação volta a exigir aprovação individual.

```bash
codaro execute 'Corrija a autenticação e valide' --approval task \
  --scope src --scope tests \
  --allow-command '["pytest","-q"]' \
  --allow-command '["ruff","check","src","tests"]'
```

Antes de chamar a IA, a CLI apresenta objetivo, caminhos e comandos e solicita confirmação com padrão não. No chat, defina `/task new OBJETIVO`, selecione Executar e use `/permissions task`; revise o JSON de caminhos/comandos e confirme. `/permissions action` revoga o escopo. Autorizações vivem somente na sessão e são vinculadas ao identificador da tarefa; não são restauradas de disco, copiadas da memória ou herdadas por outra tarefa. Paths externos, links, ignores e arquivos proibidos continuam bloqueados nas ferramentas de arquivo. Comandos aprovados **não são uma sandbox** e podem atuar fora desses caminhos; conceder um comando autoriza seus efeitos, não apenas a pasta usada como cwd.

### Ciclo de implementação e validação

O fluxo é investigar → planejar → preparar/revisar → aplicar → validar → corrigir e validar novamente. `propose_edit` substitui um trecho exato já lido; `apply_changes` reúne operações `edit`, `create`, `delete` e `rename`. Remover/renomear exige leitura completa do arquivo atual. A criação pode incluir diretórios; o destino futuro é verificado com o mesmo motor de ignores do ripgrep, sem criar arquivos no projeto antes da autorização.

Cada conjunto contém até oito arquivos; renomear conta como criação do destino e remoção da origem. Um conflito interrompe as operações restantes e retorna quais foram aplicadas e quais ficaram pendentes. Não há transação atômica entre arquivos. Não fazemos rollback automático sobre alterações externas; use checkpoints para revisar uma reversão. Diretórios vazios criados podem permanecer após falha ou desfazer.

Para verificar a implementação, o agente usa `run_command` com `purpose: "validation"`. Cada solicitação executa novamente: resultados de testes não são reutilizados no novo ciclo. O registro associa exit code/timeout à revisão do código. Alterações nas ferramentas e mudanças detectadas nos arquivos indexados invalidam resultados antigos; comandos de validação já usados precisam passar novamente na revisão atual, ou o plano deve declarar explicitamente a lista atual de verificações necessárias. Isso comprova os comandos executados, **não a cobertura ou correção semântica de todo o projeto**. Comandos marcados `operation` não contam como validação.

### Tarefas, retomada e limites

```bash
codaro task                       # tarefa ativa
codaro task list
codaro task new 'Implementar recuperação de senha'
codaro task resume ID_DA_TAREFA   # seleciona para conferência/continuação
codaro . --resume
```

No chat: `/task`, `/task list`, `/task new OBJETIVO` e `/task resume ID`. Use uma nova tarefa para uma atividade diferente; perguntas de acompanhamento preservam o objetivo ativo. A seleção/retomada não executa operações. Ao continuar, o agente confere o índice atual e recebe o plano, a revisão e operações recentes; chamadas antigas não são reaplicadas automaticamente. Uma tarefa cancelada pode ser selecionada explicitamente com `resume`. Aprovações por tarefa precisam ser revistas em outra sessão.

`.codaro/tasks.json` é privado, atômico e vinculado à raiz: retém até vinte tarefas/4 MB, vinte e quatro etapas, dezesseis critérios, duzentos eventos e trinta e duas validações por tarefa. Registra interações/run IDs, decisões de aprovação, intenções de alteração, checkpoints e resultados de comandos. Não armazena raciocínio interno como planejamento. Memória de conversa, checkpoints e último fluxo HTTP continuam em seus arquivos existentes. Locks impedem execuções concorrentes no mesmo projeto; estados interrompidos permanecem inspecionáveis. Arquivos e comandos já aplicados são mantidos após cancelamento.

Limites padrão: Perguntar oito etapas; Planejar vinte; Executar trinta e duas. Planejar/Executar têm orçamento acumulado de 96.000 caracteres de resultados, mantendo o limite de contexto por requisição. A atividade tem prazo de 1.800 segundos, descontando espera em revisões, e até três falhas de validação por interação. Repetição consecutiva sem progresso encerra a investigação. Configure `--max-steps` (1–200), `--max-seconds` (1–7.200) e `--max-corrections` (1–10) no chat ou execute. Um bloqueio/limite conserva o progresso para outra interação; não cria execução ilimitada.

## Navegação e configuração no chat

Ctrl+P reúne Perguntar, Planejar, Executar, nova conversa/tarefa, retomada, alterações, permissões, contexto, histórico, provedores, temas e ajuda. A paleta e seus comandos estão em português; os aliases técnicos continuam disponíveis.

- **Limpar mensagens** (`Ctrl+L` ou `/clear`) limpa mensagens e contexto enviados, mantendo a tarefa, o escopo autorizado e propostas pendentes visíveis. `/restore-clear` desfaz a última limpeza nesta sessão.
- **Nova conversa e tarefa** (`/new`) abre uma revisão do efeito: revoga o escopo anterior, descarta propostas pendentes e começa outra atividade. Arquivos já alterados são mantidos. É possível informar o novo objetivo.
- **Gerenciar provedores** (`/provider-manage`) permite editar URL/TLS, renomear, substituir a API key, testar e remover perfis. Na edição, uma chave vazia mantém a credencial salva; ela permanece mascarada. Remover o perfil ativo exige selecionar outro modelo antes de enviar uma pergunta.
- Os formulários têm rótulos permanentes e ações fixas. **Testar conexão** permite cancelar e ignora resultados tardios. O teste não salva o perfil. Uma operação HTTP síncrona já iniciada pode continuar até retornar/atingir timeout; o cancelamento evita novas etapas e libera a interface.
- O seletor oculta modelos que a API informa não suportarem ferramentas/chat e distingue suporte confirmado de suporte desconhecido. Para capacidade desconhecida, use o teste do modelo no cadastro/edição do provedor.
- O escopo da tarefa usa campos de caminhos e comandos exatos, um por linha, com prévia e JSON avançado opcional. Aspas agrupam argumentos; essa entrada não executa um shell. `/permissions action` revoga o escopo.
- A chegada de mensagens respeita a posição de leitura e oferece **Novas mensagens ↓**. O orçamento estimado e sua origem continuam visíveis após a geração; `/status` detalha contagem e calibração.
- É possível escrever o próximo rascunho enquanto o agente trabalha. O envio simultâneo fica bloqueado e preserva o texto. Erros oferecem repetir a pergunta, configurar o provedor e escolher outro modelo.
- `/reasoning` alterna o raciocínio expandido/recolhido nesta sessão. A prévia renderiza Markdown estável, conserva cercas incompletas como texto e só promove a resposta depois da aceitação. Respostas sem ferramentas não criam um resumo vazio de atividades.
- Os temas usam cores semânticas para manter texto/fundo consistentes. Em terminais estreitos, o cabeçalho prioriza modo e aprovação em duas linhas e os atalhos usam `^` para Ctrl; no macOS, `⌘` indica Command. `/help` mostra os atalhos completos e a alternativa Ctrl.

Detalhamento da correção dos 17 achados: [UI e UX](docs/UI_UX.md).

## Interface e streaming

- `codaro chat` exibe a resposta final após o agente concluir e validar o turno, em Markdown, com títulos, listas, tabelas e destaque de sintaxe em blocos de código. Durante a geração, a barra de status e as ferramentas mostram o progresso.
- A abertura tem sugestões que preenchem um rascunho sem chamar o modelo. Enter envia; Alt+Enter insere uma nova linha (Shift+Enter também funciona quando reconhecido pelo terminal). A entrada cresce até oito linhas de altura e preserva rascunhos acima de 8.000 caracteres para que você possa reduzi-los antes do envio.
- Cada pergunta tem um resumo expansível das ações, leituras, buscas e duração. Dentro dele ficam consulta ou arquivo/símbolo, resultados ou linhas e indicação de leitura parcial ou reutilização de conteúdo. Erros abrem os detalhes automaticamente.
- As atividades aparecem uma única vez na conversa. A barra de status informa o arquivo ou consulta durante a execução.
- A barra superior mostra pasta abreviada, modelo, modo de edição e conexão. A barra de status informa o estado atual. `/pwd` mostra o caminho completo.
- O texto recebido por streaming aparece automaticamente em **Texto em geração · não validado**, fora do resumo de atividades. Ao concluir o turno, esse mesmo bloco vira a resposta final em Markdown, sem apagar e recriar a mensagem nem duplicar seu texto. A geração acompanha até 16.000 caracteres (limite textual do provedor), inclusive após os primeiros 4.000. Se a etapa terminar em ferramentas, nova tentativa ou interrupção, o bloco permanece identificado e recolhido; etapas arquivadas mostram até 4.000 caracteres, com fluxo completo em `.codaro/prompt.json`. Somente a resposta aceita é salva no histórico.
- Quando o servidor envia `reasoning_content` ou `reasoning` textual separado, ele aparece em **Raciocínio enviado pelo modelo · provisório**, em um bloco próprio com até 4.000 caracteres. Ao terminar a etapa, fica recolhido. Esse texto não é misturado à resposta, reenviado como conversa nem salvo na sessão; o dump HTTP continua disponível para diagnóstico. Modelos que não expõem esse campo mostram apenas geração e atividades. Não inferimos raciocínio a partir de texto comum.
- `codaro ask`, `plan` e `execute` mostram progresso no terminal e enviam atividade para stderr; o texto final aparece após o retorno do agente. O conteúdo provisório fica no JSON de diagnóstico.
- O provedor solicita SSE da API OpenAI-compatible e entrega fragmentos conforme chegam. Se o servidor responder com JSON comum ou acumular o conteúdo antes de enviar, a resposta chega de uma vez; a interface não simula streaming. Confira os eventos SSE e o corpo da resposta em `.codaro/prompt.json` para distinguir esse caso.
- Uma resposta interrompida ou cancelada não é salva no histórico. O chat informa a interrupção e conserva a prévia recolhida apenas na investigação daquela tela. Retomar a sessão restaura somente perguntas e respostas concluídas.

O chat detecta o sistema onde o processo está rodando. No macOS, o rodapé e `/help` mostram Command (`⌘`) com a alternativa Ctrl, e Option (`⌥`) nas dicas da entrada. Linux e Windows mantêm Ctrl/Alt. Em SSH, a detecção corresponde ao servidor.

| Ação | macOS | Linux / Windows |
| --- | --- | --- |
| Cancelar | `⌘X` ou `Ctrl+X` | `Ctrl+X` |
| Limpar conversa | `⌘L` ou `Ctrl+L` | `Ctrl+L` |
| Comandos | `⌘P` ou `Ctrl+P` | `Ctrl+P` |
| Sair | `⌘Q` ou `Ctrl+Q` | `Ctrl+Q` |
| Nova linha | `⌥Enter` | `Alt+Enter` |
| Histórico direto | `⌥↑` / `⌥↓` | `Alt+↑` / `Alt+↓` |

**Command depende do terminal:** o Codaro aceita o modificador `super` do protocolo de teclado estendido, mas alguns terminais interceptam `⌘Q`/`⌘P`/outras combinações para seus próprios menus. Nesses casos, use Ctrl ou configure um mapeamento no terminal para enviar a combinação equivalente: `⌘X` → byte hexadecimal `18`, `⌘L` → `0c`, `⌘P` → `10`, `⌘Q` → `11`. Esses bytes acionam os atalhos Ctrl existentes. Para Option, configure o terminal para enviar Alt/Esc; Shift+Enter também insere uma nova linha quando reconhecido pelo terminal.

Chamadas de ferramentas devem chegar no campo nativo `tool_calls`, com nomes e argumentos do catálogo enviado ao servidor. Se o modelo escrever uma chamada JSON como texto — inclusive com um nome inventado como `read_file` — o agente pede uma correção pelo protocolo uma vez. Esse texto não é executado nem aceito como resposta final; se o erro persistir, a tarefa fica bloqueada e o fluxo é preservado em `.codaro/prompt.json`. Exemplos acompanhados de explicação continuam permitidos. Use `codaro doctor --check-tools` para verificar o ciclo de chamada, resultado e resposta com seu modelo/servidor.

## Provedores e BYOK

O menu **Ctrl+P** (ou **⌘P** no macOS com suporte do terminal) oferece **Cadastrar provedor** para abrir o formulário BYOK e **Selecionar provedor e modelo** para escolher entre os perfis já cadastrados. Abrir esses formulários pelo menu preserva o rascunho da mensagem.

No chat, `/providers` abre o cadastro: escolha **OpenAI Compatible, OpenAI, Ollama, Anthropic, Gemini ou Groq**, informe a chave com entrada oculta e clique em **Cadastrar e listar modelos**. Os provedores conhecidos já têm URL preenchida; OpenAI Compatible precisa da URL base do seu servidor. O formulário inclui **TLS Insecure**, também disponível como flag na CLI. Ollama local não exige chave. Depois do cadastro, escolha um modelo na lista recebida da API. `/models` ou `/model` reabre a seleção; **Atualizar API** consulta novamente o catálogo.

O botão **Testar conexão** verifica o catálogo sem salvar o perfil. Depois desse teste, escolha um modelo no campo **Modelo para testar** e pressione novamente: o Codaro verifica o ciclo completo de ferramenta e resposta com duas chamadas curtas de inferência (que podem ter custo). Assim, uma listagem bem-sucedida não esconde um erro no endpoint de conversa. O teste não grava a chave no histórico nem no JSON da conversa.

Para **Ollama**, a URL pode ser `http://localhost:11434`, `http://localhost:11434/api` ou `http://localhost:11434/v1`: o perfil Ollama usa a API nativa `/api/chat`, enquanto o catálogo usa `/api/tags`. A URL salva pode continuar terminando em `/v1`; o adaptador resolve os endpoints nativos. Perfis Ollama já salvos sem `/v1` recebem a correção ao carregar. URLs de OpenAI Compatible continuam sendo explícitas, sem adivinhar o prefixo do servidor.

Também funciona sem abrir o chat:

```bash
codaro providers add openai
codaro providers add anthropic
codaro providers add gemini
codaro providers add groq
codaro providers add ollama
codaro providers add openai-compatible --base-url https://meu-servidor/v1 --tls-insecure
# A API key é solicitada com entrada oculta; em seguida aparece o catálogo.
codaro providers list
codaro models list --provider openai --refresh
codaro models use ID_DO_MODELO --provider openai
codaro .
```

`--name` permite vários perfis do mesmo provedor. Para automação, `--key-env NOME_DA_VARIAVEL` lê a chave dessa variável, sem colocar seu valor nos argumentos do processo. `codaro providers remove PERFIL` remove o perfil e sua credencial local. O provedor/modelo selecionado passa a ser o padrão global; a troca no chat preserva a conversa, atualiza imediatamente janela, reserva de saída e contagem de contexto, e revoga aprovações por tarefa.

**Metadados vêm da API, quando disponíveis.** Gemini usa `/v1beta/models`, incluindo `inputTokenLimit`/`outputTokenLimit`, e conversa pelo endpoint OpenAI-compatible. Anthropic usa `/v1/models`, incluindo `max_input_tokens`/`max_tokens` quando informados, e `/v1/messages` nativo para ferramentas, resultados e streaming. Groq e servidores compatíveis usam `/models` e seus campos de limites. Ollama lista `/api/tags` e consulta `/api/show` ao selecionar: `parameters.num_ctx` tem prioridade; sem ele, lê `*.context_length` em `model_info`. A conversa nativa envia explicitamente `options.num_ctx` com esse valor, alinhando o orçamento do cliente à janela solicitada ao servidor. A seleção consulta os metadados novamente. O catálogo mostra ID, nome, limites, origem e suporte a ferramentas quando informado.

Algumas APIs, incluindo a listagem padrão da OpenAI, não informam contexto. Nesses casos, o catálogo mostra **não informado pela API** e o cliente usa **fallback de 16.384 tokens**; para Ollama sem `num_ctx` nem `model_info.context_length` informado, usa **4.096 tokens** como fallback conservador. A origem aparece em `/status`, no diagnóstico e na seleção. Isso não é o limite oficial nem uma garantia sobre a janela ativa do servidor. É possível definir um limite conhecido com `codaro models use ID --provider PERFIL --context-window 8192`; essa configuração é preservada ao atualizar o catálogo. Limites de entrada de Gemini/Anthropic são tratados conservadoramente como orçamento total, reservando saída e margem; o cliente mantém seu teto de 2.000.000 tokens e reserva padrão de até 1.400 tokens, limitada pelo máximo de saída informado. APIs indisponíveis não apagam a configuração anterior. A abertura do chat tenta atualizar os detalhes do modelo Ollama selecionado, inclusive perfis antigos com fallback. Se a API estiver indisponível, conserva a configuração anterior; os demais provedores usam o catálogo salvo.

Os perfis e chaves ficam em `$XDG_CONFIG_HOME/codaro/provider-credentials.json` (padrão `~/.config/codaro/provider-credentials.json`), fora dos projetos. A gravação é atômica e, em POSIX, usa diretório privado e arquivo `0600`; links, arquivos compartilhados e permissões públicas são rejeitados. O arquivo contém as chaves em texto local, sem criptografia/keychain nesta versão. Seu nome é excluído pela política de arquivos do agente, inclusive ao analisar a pasta que contém a configuração. As chaves do formulário não são mensagens do chat, não entram no histórico e não aparecem no cadastro/listagem/diagnóstico. Em Windows, a proteção depende das permissões do diretório do usuário.

A configuração antiga continua disponível: `CODARO_BASE_URL`, `CODARO_MODEL` ou `CODARO_API_KEY` definidos selecionam o modo de ambiente. `CODARO_PROVIDER=PERFIL` seleciona explicitamente um perfil cadastrado. As opções `--context-window`, `--tls-insecure/--tls-verify`, `CODARO_CONTEXT_WINDOW`, `CODARO_MAX_OUTPUT_TOKENS` e `CODARO_TIMEOUT` podem ajustar os limites/configuração do perfil. `codaro doctor --check-tools` verifica o ciclo real de ferramenta e resposta no provedor ativo, incluindo Anthropic.

## Comandos, referências e histórico

Digite `/` para ver sugestões; ↑/↓ escolhem e Tab completa. Os comandos abaixo são locais e não consultam a IA:

| Comando | Função |
| --- | --- |
| `/help` | Comandos e atalhos |
| `/pwd` | Diretório completo da sessão |
| `/status` | Modelo, modo, turnos, último contexto enviado em caracteres e seu limite |
| `/model` ou `/model nome` | Ver ou trocar o modelo para as próximas perguntas, preservando endpoint e TLS |
| `/models` | Selecionar provedor/modelo do catálogo e atualizar metadados da API |
| `/providers` | Cadastrar provedor e API key com entrada oculta |
| `/clear` | Limpar o chat e descartar propostas pendentes |
| `/resume` | Recuperar a última conversa salva no projeto |
| `/compact` | Reduzir o contexto ativo aos quatro turnos mais recentes, preservando a conversa salva |
| `/history termos` | Buscar mensagens e decisões antigas deste projeto |
| `/memory` | Ver objetivo, pedidos, decisões, restrições e notas da tarefa |
| `/memory decision texto` | Registrar decisão explícita; também aceita `constraint`, `pending` e `clear` |
| `/map` | Atualizar e mostrar mapa de módulos, manifestos e candidatos a pontos de entrada |
| `/changes` | Listar checkpoints de edições aprovadas e conflitos |
| `/undo [id]` | Revisar o diff para desfazer a última edição ou um checkpoint específico |

Use ↑/↓ no início/fim da entrada para percorrer perguntas e recuperar o rascunho ao voltar. Em textos com várias linhas ou linhas quebradas visualmente, as setas continuam movendo o cursor; Alt+↑/↓ acessam o histórico diretamente. Textos colados com múltiplas linhas permanecem no rascunho até Enter. Escape e Ctrl+X cancelam a investigação; nas revisões, Escape volta ou rejeita o comando.

`Explique @src/main.py` inclui uma leitura local antes da primeira requisição. Tab completa caminhos permitidos; para nomes com espaços use `@"pasta com espaços/main.py"`. São até quatro referências por pergunta, sujeitas às mesmas regras de ignore, limites e proteção das ferramentas. Uma referência inexistente ou proibida informa erro antes de consultar o modelo. Essas leituras ficam em `local_retrievals` no JSON de debug, sem simular chamadas do modelo.

Se houver `AGENTS.md` na raiz, o agente consulta as orientações locais de estilo, build e testes em cada pergunta. São até 80 linhas e 2.400 caracteres serializados por leitura inicial, com teto compartilhado de 6.000 caracteres e desconto no orçamento de ferramentas. Orientações não dispensam aprovações nem as restrições da sessão. Instruções em subpastas ainda não são carregadas automaticamente.

## Retomar a conversa

```bash
codaro . --resume
codaro ./outro-projeto --resume
# Atalho na pasta atual
codaro --resume
```

A última conversa de cada projeto é salva automaticamente em `.codaro/session.json`, com até 50 turnos e 512 KB; ao retomar, o chat mostra até 30 turnos recentes. O contexto enviado à IA mantém seu próprio limite e pode descartar turnos antigos sem apagar a conversa salva. `/clear` limpa a sessão em memória; o arquivo anterior permanece disponível para `/resume` até outra pergunta concluída substituí-lo. Propostas pendentes não são reaplicadas ao retomar; resultados de aprovação já registrados são preservados.

O arquivo usa gravação atômica e `0600` em POSIX, bloqueia links e valida raiz, versão e mensagens ao carregar. A chave do provedor é mascarada, inclusive em formas escapadas. A conversa pode conter código e outros dados do projeto: mantenha `.codaro/` no `.gitignore`. Sessões simultâneas no mesmo projeto compartilham o arquivo e a última gravação prevalece.

## Recuperação da conversa e memória da tarefa

Perguntas concluídas em `ask`, `edit` e no chat também entram em `.codaro/memory.sqlite3`, um arquivo local por projeto, independente do contexto ativo e do `session.json`. O arquivo retém até **500 turnos e 8 MB**, removendo os mais antigos conforme necessário. Guarda pergunta, resposta final, ações e resultados de revisão; não arquiva pensamento interno do modelo. A última sessão do formato anterior é importada uma vez quando a busca da conversa for utilizada, com datas de importação e identificação `legacy-session`.

O modelo dispõe de `search_conversation(query, limit)` para buscar com SQLite FTS5/BM25 e `read_conversation(turn_id, offset, limit)` para recuperar páginas de 200–4.000 caracteres. A busca retorna até oito trechos com identificador, origem e data de registro. Esses resultados passam pelo orçamento de contexto das outras ferramentas. Pedidos antigos e respostas da IA são dados históricos: não comprovam a implementação atual nem autorizam comandos. Antes de editar, a leitura atual continua obrigatória.

Objetivo, últimos pedidos e até 32 itens estruturados ficam na mesma memória. Há decisões, restrições e pendências registradas pelo usuário (até oito por tipo), e notas atribuídas ao agente. `remember_task(note)` permite ao modelo registrar somente notas, sem transformar suas próprias afirmações em decisões do usuário. As decisões, restrições e pendências ativas registradas pelo usuário entram como instruções preservadas. Notas do agente recebem um resumo limitado; itens omitidos continuam recuperáveis pelas ferramentas. A tarefa atual prevalece sobre pedidos anteriores.

```bash
codaro history "autenticação JWT" --repo /caminho/do/projeto
codaro memory decision "Usar PostgreSQL." --repo /caminho/do/projeto
codaro memory constraint "Preservar a API pública." --repo /caminho/do/projeto
codaro memory pending "Validar as migrações." --repo /caminho/do/projeto
codaro memory --repo /caminho/do/projeto
codaro memory clear --repo /caminho/do/projeto
```

`/clear` limpa a conversa ativa; `/memory clear` limpa a memória estruturada da tarefa. O arquivo histórico permanece disponível para recuperação. O armazenamento valida projeto/versão, bloqueia links, mascara a chave configurada e escreve snapshots SQLite privados e atômicos, sem abrir journals em caminhos do projeto. Em POSIX, um lock entre processos protege atualizações; revisões concorrentes da memória estruturada são detectadas. Em outras plataformas, a proteção de concorrência é apenas dentro do processo.

## Mapa do projeto e calibração

`codaro map --repo CAMINHO` e `/map` mostram módulos, extensões, manifestos e candidatos a pontos de entrada. O mapa usa os caminhos e hashes do índice incremental e é atualizado com arquivos modificados, removidos ou excluídos por ignore. O agente reutiliza o mapa enquanto o digest não muda e inclui uma versão compacta nas perguntas de arquitetura. Nomes como `main.py` e definições Python são candidatos para investigação; não são apresentados como prova de execução.

Quando o endpoint informa `usage.prompt_tokens`, o contador compara entrada real e estimativa. A calibração fica separada por projeto, endpoint, modelo e encoding. Uma subestimativa aumenta o fator imediatamente, com margem de 15%; a redução só ocorre após oito amostras, considera o maior desvio das últimas vinte e mantém um fator mínimo de 0,6. O fator máximo é 4. Valores inválidos são ignorados. Sem usage, continua valendo a estimativa configurada.

`/status` mostra estimativa, consumo informado, fator e número de amostras. O JSON de debug registra `reported_prompt_tokens` e `calibrated_scale` por chamada, além de `task_memory`, `project_map` e `conversation_turn_id`. A calibração melhora a estimativa. No Ollama, a janela solicitada que precisou ser reduzida por falta de memória também é salva por até 24 horas. `/recalibrate`, disponível na paleta, limpa a calibração deste projeto e restaura os limites selecionados para reaprendê-los. A configuração global do servidor permanece intacta. OpenAI solicita consumo de tokens no streaming; endpoints compatíveis que rejeitam `stream_options` continuam sem essa opção.

## Edição com revisão de diff

```bash
# Chat com propostas de edição habilitadas
codaro chat --repo /caminho/do/projeto
# Investigação sem ferramenta de edição
codaro chat --read-only --repo /caminho/do/projeto
# Uma solicitação de mudança, com revisão e confirmação no terminal
codaro edit 'Adicione uma validação de entrada à função can_edit' --repo examples/demo
```

O agente lê o trecho atual e solicita a alteração com texto original exato, novo texto e motivo. O conjunto de diffs é revisado durante o ciclo, antes da aplicação. Após Aplicar/Rejeitar/Voltar, o resultado volta ao modelo. A ação inicialmente focada é Voltar: Enter não aprova automaticamente. Na CLI, o padrão de confirmação é **não**. `ask` continua somente leitura.

- A aplicação verifica novamente as regras de ignore, o conteúdo original e a identidade do arquivo, e usa substituição atômica no mesmo diretório. Uma alteração concorrente detectada bloqueia a proposta; faça uma nova solicitação sobre o arquivo atual.
- BOM UTF-8, finais de linha LF/CRLF e bits de permissão usuais são preservados. Arquivos binários, links simbólicos, hard links e caminhos proibidos são bloqueados. A aplicação requer POSIX com `dir_fd` e `O_NOFOLLOW`; plataformas sem esses recursos podem investigar, mas não aplicar.
- Até oito arquivos por conjunto, uma operação por caminho. Cada trecho original/novo tem até 3.000 caracteres; o diff tem até 60.000 caracteres. O trecho original deve ocorrer uma única vez e estar inteiramente em linhas lidas, sem truncamento.
- Durante a revisão integrada, a tarefa aguarda a decisão. Cancelar descarta propostas ainda pendentes, preservando alterações já aplicadas. O adaptador Python legado sem callback de revisão conserva propostas para revisão posterior.
- A aprovação pode abranger um conjunto; os arquivos são aplicados sequencialmente, com resultado parcial explícito. Há criação/exclusão/renomeação; não há transação entre arquivos nem reaplicação de propostas pendentes em outra sessão. Desfazer exige revisão. A gravação de conteúdo é atômica; não impede toda corrida com processos externos alterando caminhos simultaneamente.

## Checkpoints e desfazer

Antes de aplicar uma edição aprovada, o Codaro salva os bytes originais e propostos em `.codaro/checkpoints.json`, com hashes, caminho, status e identificador da investigação. O arquivo é privado e atômico, retendo até 20 checkpoints e 12 MB. Se o snapshot não puder ser salvo, a edição não começa. Se apenas a atualização posterior do status falhar, o aplicativo informa que a edição foi aplicada; o snapshot preparado permanece verificável pelo hash do arquivo.

```bash
codaro changes --repo /caminho/do/projeto
codaro undo --repo /caminho/do/projeto
codaro undo ID_DO_CHECKPOINT --repo /caminho/do/projeto
```

No chat, use `/changes` e `/undo [id]`. O diff inverso é mostrado e exige aprovação; Enter não aplica. O arquivo precisa continuar exatamente igual ao conteúdo aprovado, tanto na criação da proposta inversa quanto na aplicação. Alterações externas, exclusões por ignore e links bloqueiam o desfazer. BOM, CRLF e permissões de execução são preservados. A restauração cria outro checkpoint, permitindo revisar uma reversão posterior. Comandos executados no terminal não recebem snapshots nem são revertidos por esse mecanismo.

Os snapshots contêm os bytes exatos dos arquivos para permitir restauração, sem mascaramento que alteraria o código. Mantenha `.codaro/` fora do Git. A sessão retém apenas propostas resolvidas; checkpoints persistidos podem ser consultados após reiniciar.

## Comandos e validação com aprovação

No chat com edição habilitada e em `codaro edit`, o modelo pode solicitar `run_command` usando `argv` (lista de argumentos) e `timeout` (1–300 segundos, padrão 60). Cada execução exige aprovação humana e mostra diretório, argumentos e timeout. Enter rejeita por padrão; Escape rejeita a revisão no chat. `--read-only` e `codaro ask` não disponibilizam essa ferramenta. Comandos ficam bloqueados enquanto houver propostas pendentes, para evitar tratar um teste do código antigo como validação de um diff ainda não aplicado.

Os comandos partem da raiz escolhida, sem shell implícito, e executam com as permissões do usuário; essa raiz define o diretório de trabalho, não uma sandbox. `CODARO_API_KEY` não é herdada pelo subprocesso. Saída e erro são combinados, capturados com memória limitada e devolvidos com código de saída, timeout e indicação de truncamento. Em POSIX, cancelamento/timeout encerram o grupo de processos; em outras plataformas, o processo principal é encerrado. Os resultados também entram no fluxo nativo `tool_calls` → `tool` e no JSON de debug.

No ciclo integrado, a validação segue a aplicação na mesma interação. Aprovar um diff não aprova comandos de teste fora do escopo autorizado. **Validar alteração** permanece disponível para revisões manuais/desfazer. A escolha das verificações depende do projeto e do modelo; resultados não executados permanecem pendentes.

## API compatível com OpenAI

```bash
export CODARO_BASE_URL=https://api.openai.com/v1
export CODARO_MODEL=gpt-4.1-mini
export CODARO_API_KEY='sua-chave'
codaro chat --repo /caminho/do/projeto
```

Na investigação com IA, a raiz absoluta do projeto, a pergunta, o histórico e os trechos lidos são enviados ao endpoint configurado. Os comandos locais `index`, `search` e `read` não chamam modelos por padrão. Não coloque chaves no código ou no Git.

## TLS e protocolo de ferramentas

A verificação do certificado e do hostname HTTPS fica **ativa por padrão**. Para um endpoint com certificado não confiável, você pode desativá-la explicitamente:

```bash
codaro . --tls-insecure
codaro ask 'Quais arquivos existem?' --repo examples/demo --tls-insecure
codaro edit 'Proponha uma validação de entrada' --repo examples/demo --tls-insecure
# Configuração equivalente por ambiente
export CODARO_TLS_INSECURE=true
# Sobrescreve o ambiente e reativa a verificação nesta execução
codaro . --tls-verify
```

As flags ficam depois do caminho ou subcomando. `CODARO_TLS_INSECURE` aceita `true/false`, `1/0`, `yes/no` e `on/off`; valores inválidos são rejeitados. `--tls-insecure` desativa a validação de certificado e hostname, portanto use-o apenas quando confiar no endpoint. O modo aparece no chat e em `codaro doctor`. Em URLs HTTP não há TLS a verificar.

O agente envia `tools` e `tool_choice: auto` para `/chat/completions`, recebe `tool_calls`, executa as ferramentas locais e envia cada resultado com `role: tool` e o `tool_call_id` correspondente. O modelo é consultado novamente para responder com os resultados. Apenas essas chamadas estruturadas executam ferramentas; JSON escrito na resposta nunca é executado como chamada.

Inteiros decimais canônicos enviados como strings, como `"10"` e `"0"`, são normalizados antes da validação de limites. Booleanos, floats, expressões, formatos ambíguos e valores fora dos limites continuam bloqueados. Uma tentativa reconhecida de chamar uma ferramenta em texto recebe uma correção de protocolo, limitada a uma tentativa por pergunta; se persistir, o agente informa incompatibilidade e não salva uma resposta falsa no histórico.

Para verificar o modelo/servidor configurado:

```bash
codaro doctor --check-tools
# Com TLS explicitamente sem verificação
codaro doctor --check-tools --tls-insecure
```

Esse diagnóstico faz duas chamadas reais ao provedor: pede uma ferramenta de teste sem argumentos, devolve um resultado com um identificador aleatório usando `role: tool` e verifica se a resposta final contém esse identificador. A segunda chamada usa o caminho de streaming, incluindo o fallback JSON. Nenhuma ferramenta de arquivo é executada. Um resultado positivo confirma esse ciclo, não garante a qualidade de toda investigação. `codaro doctor` sem a flag continua sem chamar a API.

Resultados de ferramentas incluem `name` e o `tool_call_id`, inclusive nos casos de erro. Um erro em qualquer ferramenta do lote mantém a recuperação de protocolo ativa. Respostas com `function_call` legado ou conclusão `tool_calls` sem chamadas são rejeitadas explicitamente.

## JSON da última investigação

O registro fica **sempre ativo**, sem flag: cada pergunta em `ask`, `edit` ou no chat grava `.codaro/prompt.json` **na raiz do projeto selecionado**, independentemente da pasta de instalação. O arquivo substitui o fluxo anterior e tem um `run_id` único.

- Metadados: pasta, modelo, endpoint, modo, configuração TLS, limites, pergunta, timestamps, duração e status (`running`, `success`, `error` ou `cancelled`).
- `turns`: requisições recentes com mensagens e schemas de ferramentas, orçamento de contexto, resposta estruturada, resultado de cada ferramenta, argumentos normalizados e duração.
- `http_attempts`: tentativas HTTP, status, payload efetivamente enviado (`http_request`), prévia do corpo JSON ou eventos SSE recebidos, motivo de conclusão e consumo de tokens quando informado pelo servidor. Respostas inválidas e reasoning separado também ficam disponíveis no dump para diagnóstico.
- `events`: atividade do agente; `final_answer` nas conclusões bem-sucedidas e `error` nas falhas/cancelamentos. Uma resposta parcial não vira uma resposta concluída no histórico.
- `compactions`: grupos de ferramentas/histórico removidos, registro das ações e estimativa de tokens antes/depois. Resultados e tentativas também ficam no arquivo incremental da execução; o snapshot pode remover turnos antigos para respeitar sua cota.

O snapshot tem cota de **2 MB**. Cada execução mantém também `.codaro/run-<run_id>.jsonl`, com eventos incrementais e cota de **8 MB**, inclusive em falhas/cancelamentos. A retenção automática, aplicada ao iniciar/finalizar execuções, mantém até **20 arquivos e 32 MB**; durante uma execução o arquivo ativo pode acrescentar até 8 MB antes da próxima rotação. Prévias SSE/NDJSON no snapshot são limitadas; `events_in_archive`, `older_turns_in_archive` e `snapshot_limited` indicam conteúdo movido. `archive.complete=false` e `omitted_events`/`error` indicam perda por cota ou falha de gravação. Esses arquivos são diagnósticos sujeitos a retenção, não um backup permanente.

Falhas e cancelamentos entram na memória como tentativas não concluídas. `read_conversation` recupera páginas dos resultados de ferramentas do arquivo por ID enquanto ele existir; raciocínio e eventos brutos não entram nessa recuperação. Resultados antigos não autorizam edições nem comprovam o estado atual do código.

```bash
# Execute na pasta que você abriu com codaro .
python -m json.tool .codaro/prompt.json
# Resumo, se tiver jq instalado
jq '{run_id, model, status, duration_ms, error}' .codaro/prompt.json
# Chamadas e resultados, preservando o vínculo por ID
jq '.turns[] | {iteration, outcome, calls: .response.tool_calls, results: .tool_results}' .codaro/prompt.json
```

A gravação usa arquivo temporário e substituição atômica, com permissão `0600` em POSIX; links simbólicos e hard links no destino são bloqueados. Há checkpoints antes das requisições e após respostas/ferramentas, além da finalização em sucesso, erro ou cancelamento. Uma interrupção forçada do processo pode deixar o último checkpoint com status `running`. Em sessões simultâneas na mesma pasta, o arquivo corresponde à última gravação; confira `run_id` e a pergunta.

Cabeçalhos de autorização não são registrados e as chaves dos provedores usados na sessão são mascaradas, incluindo formas escapadas em JSON e chaves de objetos. A mesma proteção se aplica às mensagens enviadas nas próximas requisições após uma troca de provedor. **O arquivo contém perguntas, histórico e código consultado**: a remoção da chave do provedor não remove outros segredos presentes nesses conteúdos. `.codaro` fica fora das buscas do agente e já está no `.gitignore` deste projeto; adicione `.codaro/` ao `.gitignore` de outros projetos em que usar o Codaro. Corpos HTTP de erro ficam limitados a 64 KB; os limites normais de respostas e streaming continuam valendo. Falhas de gravação geram aviso e preservam o resultado ou erro original da investigação.

O formato segue o fluxo de diagnóstico do Thoth: requisições e respostas por iteração, resultados de ferramentas e fechamento atômico. No Thoth, planejamento usa chamadas sem streaming e a síntese tem uma etapa própria. No Codaro, perguntas reconhecidas sobre estrutura recebem leituras locais iniciais limitadas; o modelo pode usar JSON ou streaming para continuar. O Codaro também aceita chamadas estruturadas durante SSE, com o mesmo contrato de mensagens no modo JSON e no streaming. O payload é construído pela mesma função usada para calcular o orçamento e registrar a requisição, evitando divergências entre essas representações.

## Estrutura do projeto e evidências

`get_repository_info` descreve a sessão do **Codaro**: pasta, capacidades e escopo. Essas capacidades não são módulos, scripts ou pontos de entrada do projeto explorado. O resultado identifica explicitamente esse escopo.

Citações `caminho:linha` são recomendadas quando úteis, mas **não são requisito para aceitar a resposta**. Perguntas gerais podem ser respondidas diretamente. Falhas de leitura também podem ser explicadas sem citações. O prompt orienta fundamentar afirmações sobre o projeto nas fontes consultadas e não inventar resultados.

No modo Executar, consultas explícitas como “o que pode me falar sobre o projeto atual?” usam temporariamente o fluxo de leitura. Elas preservam a tarefa pendente, não oferecem ferramentas de alteração e não exigem validação de uma implementação anterior. O modo selecionado é restaurado ao terminar, inclusive em caso de erro. O reconhecimento é conservador e usa padrões em português/inglês; pedidos com verbos de implementação, execução ou continuação mantêm o fluxo de tarefa. Para garantir uma consulta em qualquer formulação, selecione o modo Ask.

O limite de resposta é separado da janela de contexto: `CODARO_MAX_OUTPUT_TOKENS` controla a saída (padrão: 1.400). Quando o provedor informa uma saída truncada, o Codaro pede uma versão curta, com até duas novas tentativas. A interface mostra o motivo específico, como “Limite de resposta atingido”, “Contexto ajustado” ou “Verificação pendente”. Para implementações, a tentativa de validação inclui a resposta anterior no histórico; respostas idênticas sem avanço de revisão, plano ou verificações interrompem o ciclo e preservam as alterações. Uma tarefa bloqueada é registrada como `status: "blocked"` em `.codaro/prompt.json`, mesmo quando uma resposta textual foi produzida. No Ollama, motivo de término e consumo são registrados também quando a saída é truncada.

Perguntas reconhecidas sobre estrutura/arquitetura/pontos de entrada recebem uma recuperação inicial limitada de manifestos e candidatos: até cinco leituras de 60 linhas, 1.800 caracteres serializados por leitura e 6.000 caracteres ao todo, descontados do orçamento. Para pontos de entrada conhecidos, a leitura procura main/bootstrap mesmo após imports longos. O reconhecimento usa padrões em português/inglês e não classifica toda intenção. Metadados e nomes candidatos não substituem o conteúdo dos arquivos. As leituras entram na mensagem system como dados e são registradas em `local_retrievals`.

O controlador não tenta certificar a verdade de uma explicação por presença de uma citação. Para edição, continua exigindo conteúdo atual observado; para conclusão de implementação, verifica resultados de validação associados à revisão atual. A qualidade da interpretação e a suficiência dos testes ainda dependem do modelo e do projeto.

### Formato interno do Thoth versus API OpenAI

O dump do Thoth representa definições como `{name, description, parameters}`. Seu adaptador OpenAI transforma essas definições em `{type: "function", function: {name, description, parameters}}` antes do envio a `/chat/completions`, como faz o Codaro. Veja [`openaiTools`](https://github.com/raulaguila/thoth-backend/blob/4375307787b535c3161dfae478282ab1cf8f3d3f/internal/adapter/outbound/llm/chat_helpers.go#L95). O formato do dump não deve ser confundido com o payload enviado pelo adaptador; APIs nativas de outros provedores podem ter contratos diferentes.

## Como a recuperação funciona

1. `ripgrep` lista arquivos e aplica `.gitignore` e `.codaroignore`, mesmo sem um repositório Git inicializado.
2. Tree-sitter extrai funções, métodos, classes e decoradores Python, preservando linhas e nomes qualificados. Classes e funções grandes são divididas em trechos de até 100 linhas. Janelas de arquivo retêm imports e expressões de módulo. Outras linguagens usam janelas de 60 linhas nesta versão.
3. SQLite FTS5/BM25 busca termos e identificadores, incluindo partes de `camelCase` e `snake_case`.
4. Correspondências exatas de símbolo/caminho e resultados textuais são combinados com RRF; intervalos sobrepostos são deduplicados.
5. O agente recebe primeiro metadados e previews; solicita `read_symbol` ou `read_lines` apenas quando necessário. A leitura de símbolos analisa o arquivo atual, em vez de reutilizar linhas possivelmente antigas. Definições duplicadas exigem `start_line` para desambiguar.

A busca inicial é textual e estrutural. Embeddings, reranking e LSP serão adicionados conforme avaliação: não há compreensão semântica por vetores neste estágio.

## Limites de contexto e arquivos

- Até 8/20/32 etapas em Perguntar/Planejar/Executar por padrão, com até oito chamadas por etapa. Finalização sem ferramentas após o limite; CLI permite configurar o número de etapas.
- Leituras de até 160 linhas e 6.000 caracteres, com indicação de truncamento e próxima linha. `partial_line` indica que uma única linha excedeu o orçamento; não conclua sobre a parte omitida.
- Até 12 resultados por busca, com previews de 240 caracteres.
- Orçamento de 24.000 caracteres em Perguntar e 96.000 em Planejar/Executar por interação. É uma aproximação de volume, não uma contagem exata de tokens.
- Histórico de perguntas e respostas limitado a 16.000 caracteres, sem reter corpos de ferramentas entre turnos. A requisição completa respeita o teto adicional de 64.000 caracteres e o orçamento estimado de tokens, incluindo instruções, mensagens e schemas.
- Janela configurável em tokens: padrão **16.384**, reserva de saída **1.400** e margem **512**. Esses valores são limites do cliente; configure a janela realmente habilitada no servidor. O tamanho arquitetural anunciado do modelo não garante a configuração do endpoint.
- A partir de 85% do orçamento, o agente remove turnos antigos e compacta grupos completos de chamadas/respostas da investigação atual. Um registro limitado preserva caminhos, ações e status de comandos/propostas, sem tratar código removido como prova. A conversa salva e os resultados no JSON de debug são preservados. Trechos descartados precisam ser relidos antes de justificar conclusões/edições; pares `tool_calls`/`tool` nunca são quebrados.
- Leituras, buscas e listagens são reduzidas conforme o espaço restante. Leituras idênticas/contidas não repetem conteúdo quando o arquivo não mudou; intervalos com prefixo sobreposto enviam somente as novas linhas. Após compactação, as leituras podem ser feitas novamente. Comandos no ciclo integrado executam a cada nova solicitação, permitindo testar novamente depois de corrigir. Apenas o adaptador legado mantém o cache de execuções por pergunta. Erros de leitura podem ser tentados novamente.
- Rejeições reconhecidas de contexto do servidor reduzem o orçamento e permitem até seis novas tentativas ao modelo, sem reexecutar ferramentas. Limites explicitamente informados no erro ajudam a ajustar o orçamento; a redução aprendida é salva por projeto/endpoint/modelo/configuração para as próximas interações. Outros erros HTTP mantêm seu tratamento normal. Em janelas pequenas, o agente remove contexto inicial recuperável, usa instruções compactas e carrega ferramentas por demanda. Se necessário, passa a um conjunto mínimo com `request_tools` e uma ferramenta por vez, descartando notas intermediárias recuperáveis e, como último recurso, reduzindo a reserva de saída a 256 tokens. Pergunta atual, decisões/restrições do usuário e controles de autorização são conservados. Se nem o turno mínimo couber ou o servidor rejeitar todas as tentativas, preserva a conversa/alterações e oferece continuar por uma parte menor ou selecionar outro modelo, sem expor mensagens técnicas de estouro de contexto; detalhes permanecem no debug. A investigação continua limitada às etapas configuradas e ao volume total de resultados por interação; compactação não significa análise ilimitada.
- Até 20.000 arquivos de 512 KB cada. Caminhos externos e links simbólicos são rejeitados. No POSIX, leituras usam descritores de diretório e `O_NOFOLLOW`; o caminho alternativo para plataformas sem esse recurso não oferece a mesma proteção contra substituições concorrentes de diretórios.
- Além das extensões de código/configuração, são permitidos nomes conhecidos como `.gitignore`, `.dockerignore`, `.codaroignore`, `.gitattributes`, `.editorconfig`, `go.mod`, `go.sum`, `Makefile`, `Dockerfile`, `Containerfile`, `Justfile`, `Procfile`, `Gemfile`, `Rakefile`, `Jenkinsfile`, `CMakeLists.txt`, `README` e `LICENSE`. Caminhos absolutos dentro da raiz selecionada funcionam; as mesmas regras de ignore, tamanho, arquivos binários e links continuam aplicadas.
- `.env*`, nomes contendo `secret`/`credential`, chaves privadas e diretórios gerados são excluídos. Isso não detecta todos os segredos; revise as exclusões antes de usar uma API externa.

Configure a janela real do seu servidor, por exemplo **8.192 tokens**, e abra o projeto:

```bash
codaro . --context-window 8192
# Ou configure o padrão para chat, ask, edit e doctor:
export CODARO_CONTEXT_WINDOW=8192
export CODARO_MAX_OUTPUT_TOKENS=1400
codaro .
```

Para janelas pequenas, uma reserva de saída menor deixa mais espaço para investigar:

```bash
export CODARO_MAX_OUTPUT_TOKENS=512
codaro . --context-window 8192 --read-only
```

O padrão usa uma **estimativa conservadora baseada em bytes UTF-8**, não uma contagem exata: tokenização e framing variam por servidor. `/status` mostra a estimativa enviada, orçamento ativo (que pode diminuir após rejeições), janela, saída e método; a barra de status mostra o uso durante as consultas. `codaro doctor` mostra a configuração sem consultar a API. `/compact` continua limitando o histórico aos quatro turnos recentes; a compactação durante a investigação é automática.

O modelo também pode usar **get_context_status** para consultar o orçamento, **compact_context** para liberar histórico/trechos recuperáveis e **request_tools** para carregar até oito ferramentas por nome no próximo passo. O cliente rejeita conjuntos que não cabem, ferramentas indisponíveis no modo atual e chamadas a nomes não anunciados naquela requisição. Carregar uma ferramenta só a habilita na próxima requisição; terminar uma tarefa bloqueia novas mutações nessa tarefa. As ferramentas **search_conversation/read_conversation** continuam disponíveis para recuperar decisões antigas. Compactação preserva o protocolo e o registro das ações; trechos descartados precisam ser relidos antes de editar. A contagem e a calibração Anthropic usam o payload Messages nativo.

Perfis **Ollama** usam chat nativo com `options.num_ctx`, `options.num_predict`, ferramentas, streaming NDJSON e contagens `prompt_eval_count`/`eval_count`. Isso solicita a janela por requisição, sem editar o Modelfile ou a configuração global do servidor. Se o Ollama rejeitar explicitamente a janela por falta de memória, o Codaro reduz `num_ctx` pela metade e tenta novamente, até o mínimo de 4.096, dentro do limite de recuperação. Erros de memória reconhecidos em HTTP 200 JSON/NDJSON também acionam essa recuperação. O ajuste é salvo no projeto por 24 horas e aparece na origem do contexto; `/recalibrate` permite reaprender após mudanças de memória no servidor. Não altera o Modelfile. Janelas grandes exigem memória disponível no Ollama; `codaro models use ID --provider PERFIL --context-window 8192` permite solicitar uma janela menor mantendo a adaptação automática. Servidores cadastrados como **OpenAI Compatible** continuam usando `/chat/completions`; sua janela precisa estar habilitada no servidor. Truncagem silenciosa de uma API não pode ser detectada em todos os casos.

Opcionalmente, para um endpoint que use uma das codificações OpenAI suportadas:

```bash
python -m pip install -e '.[tokenizer]'
export CODARO_TOKEN_ENCODING=cl100k_base  # ou o200k_base, conforme o modelo
```

Isso tokeniza o payload serializado, com margem para mensagens/schemas, e ainda é uma estimativa da entrada real do servidor. Não use essas codificações como se fossem o tokenizer exato do Llama. O primeiro carregamento pode baixar os dados de vocabulário; sem essa opção, não há dependência nem download de tokenizer. O corpo da resposta enviada ao modelo usa `max_tokens` igual à reserva configurada.

Para excluir arquivos adicionais, crie `.codaroignore` na raiz do projeto:

```gitignore
private/**
customer_data.json
```

Arquivos do repositório são tratados como dados pelo prompt do agente. As ferramentas de leitura, proposta e aplicação validam argumentos no programa. No modo Executar, a escrita e os comandos dependem da política de aprovação vigente na interface.

`AGENTS.md` da raiz é preservado integralmente, dentro da política de leitura. Orientações de diretórios ancestrais são carregadas ao consultar os arquivos correspondentes; uma operação de alteração que descobrir novas orientações precisa ser reavaliada pelo modelo antes de executar. A compactação conserva essas orientações. Se as instruções obrigatórias não couberem, nenhuma alteração é feita com regras descartadas.

Uma conclusão de execução com pedido explícito de implementação exige mudança verificável no digest do projeto e validação da revisão atual. Perguntas consultivas continuam aceitas sem exigir citações. Quando o código já atende ao pedido, `finish_task(verified_no_change=true)` exige leitura atual e validação aprovada antes de registrar a conclusão; isso não representa garantia semântica de que os testes escolhidos cobrem toda a solicitação.

Argumentos de ferramentas têm teto de 64.000 bytes UTF-8 de JSON serializado, além dos limites dos campos e do contexto. Divida operações maiores. Saídas com `finish_reason=length` ou `stop_reason=max_tokens` recebem até duas tentativas adicionais para uma resposta curta ou operação menor. JSON incompleto não executa ferramentas. O prazo da tarefa é observado pelos adaptadores, streaming e comandos, com timeout de transporte limitado ao tempo disponível; uma leitura síncrona já iniciada ainda pode aguardar seu timeout.

Detalhamento dos 13 achados corrigidos e critérios de regressão: [correções do fluxo do modelo](docs/MODEL_FLOW_FIXES.md).

## Desenvolvimento

```bash
pytest -q
ruff check .
ruff format --check .
```

Os testes verificam recuperação, atualização e migração do índice, rollback, restrições de arquivos, limites do agente, contratos HTTP, CLI, propostas de edição, conflitos, aplicação atômica e interação real com a interface Textual em modo headless. Os modelos e respostas HTTP são simulados; não há chamadas pagas ou acesso externo nos testes. O CI está configurado para Python 3.11, 3.12 e 3.13 em Linux. A auditoria local foi executada em Python 3.12.

Veja os achados e as limitações em [AUDIT.md](AUDIT.md).

## Próximas entregas

- Embeddings multilíngues opcionais e combinação com a busca textual.
- Ampliar o conjunto de avaliações com projetos e perguntas reais do usuário.
- Reranking quando houver ganho medido.
- Definições e referências via LSP, com novos parsers Tree-sitter.
- Interrupção imediata de conexões que estejam sem enviar fragmentos.
- Transações de edição envolvendo vários arquivos e execução opcional em sandbox.


## Diagnóstico e recuperação

`codaro doctor` verifica ripgrep, SQLite FTS5 e a configuração do modelo, sem chamar a API ou mostrar a chave. Retorna código 1 se um requisito local estiver ausente.

- Configure `CODARO_TIMEOUT` entre 1 e 300 segundos (padrão: 90). É um limite por operação HTTP, não um prazo total de investigação.
- Respostas 429, 502, 503 e 504 têm até duas novas tentativas com espera curta. Uma conexão interrompida depois de começar a resposta não é repetida automaticamente, para evitar duplicações. Outros erros retornam uma mensagem sem expor o corpo remoto.
- Use um modelo/servidor compatível com ferramentas na API OpenAI. Modelos só de completions não bastam. O modelo padrão é `qwen2.5:7b`; você pode substituí-lo por outro com suporte a tools.
- O índice é um cache derivado. Se estiver corrompido, feche processos Codaro, renomeie apenas `index.sqlite3` e seus arquivos auxiliares e execute `codaro index --repo ...` novamente. Preserve memória, sessão e checkpoints. Formatos antigos conhecidos do índice são reconstruídos ao atualizar a versão do schema.
- Conteúdo binário e texto fora de UTF-8 são ignorados no índice e rejeitados na leitura. Arquivos vazios são suportados.
- A atualização calcula hashes dos arquivos para detectar mudanças. Em repositórios grandes, esse I/O pode ser significativo; use `.codaroignore` para manter o escopo útil.

## Avaliações reproduzíveis

O conjunto inicial em `evaluations/cases.json` usa o código real do Codaro e o projeto demo. Você pode adicionar projetos locais: `repo` é resolvido em relação ao arquivo de casos, e cada caso declara `id`, `question`, `query`, `expected_paths` e, opcionalmente, `answer_contains`.

```bash
# Busca local: não chama o modelo
codaro evaluate evaluations/cases.json --output .codaro/evaluation-retrieval.json
# Respostas do modelo configurado: envia os trechos selecionados ao endpoint
codaro evaluate evaluations/cases.json --agent --output .codaro/evaluation-agent.json
```

O modo local mede precisão dos até seis resultados, recall dos caminhos esperados e duração. `--agent` mede conclusão, correspondência das expectativas textuais, recall de arquivos efetivamente lidos, precisão das citações quando presentes, chamadas ao modelo/ferramentas, chamadas repetidas, compactações, estimativas e usage disponível. Uma resposta sem citações pode passar quando as fontes esperadas foram lidas e as expectativas textuais foram satisfeitas; referências inventadas, quando presentes, falham. O relatório informa quantas chamadas possuem consumo real; ausência de usage não é apresentada como consumo zero. Casos com erro não interrompem os seguintes; o comando retorna código 1 se alguma expectativa falhar.

Avaliações do agente não permitem edição/comandos e usam memória efêmera, preservando a memória das conversas do projeto. Atualizam o índice e o último JSON de debug. Os casos iniciais são testes de fumaça; métricas objetivas e correspondências textuais não substituem revisão humana da qualidade semântica. Compare relatórios ao mudar modelo, prompts, recuperação ou limites de contexto.
