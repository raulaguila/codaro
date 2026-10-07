# Codaro CLI

Assistente de IA no terminal para investigar repositórios locais, com fontes no código e recuperação progressiva de contexto.

> Busca, leitura, explicação, edição com revisão de diff, comandos com aprovação e retomada de conversa por projeto. Referências via LSP e interrupção imediata de conexões HTTP ociosas estão no roadmap.

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

## Interface e streaming

- `codaro chat` mostra a resposta enquanto ela chega, em Markdown, com títulos, listas, tabelas e destaque de sintaxe em blocos de código.
- A abertura tem sugestões que preenchem um rascunho sem chamar o modelo. Enter envia; Alt+Enter insere uma nova linha (Shift+Enter também funciona quando reconhecido pelo terminal). A entrada cresce até oito linhas de altura e preserva rascunhos acima de 8.000 caracteres para que você possa reduzi-los antes do envio.
- Cada pergunta tem um resumo expansível das ações, leituras, buscas e duração. Dentro dele ficam consulta ou arquivo/símbolo, resultados ou linhas e indicação de leitura parcial ou reutilização de conteúdo. Erros abrem os detalhes automaticamente.
- As atividades aparecem uma única vez na conversa. A barra de status informa o arquivo ou consulta durante a execução.
- A barra superior mostra pasta abreviada, modelo, modo de edição e conexão. A barra de status informa o estado atual. `/pwd` mostra o caminho completo.
- Texto intermediário de fases de ferramentas é removido antes da resposta final. Campos separados de reasoning não são exibidos como resposta.
- `codaro ask` também atualiza a resposta progressivamente no terminal e envia as informações de atividade para stderr.
- O provedor usa SSE da API OpenAI-compatible. Se o servidor responder com JSON comum, a resposta é exibida de uma vez.
- Uma resposta interrompida ou cancelada não é salva no histórico. O chat remove o bloco parcial e informa a interrupção.

## Comandos, referências e histórico

Digite `/` para ver sugestões; ↑/↓ escolhem e Tab completa. Os comandos abaixo são locais e não consultam a IA:

| Comando | Função |
| --- | --- |
| `/help` | Comandos e atalhos |
| `/pwd` | Diretório completo da sessão |
| `/status` | Modelo, modo, turnos, último contexto enviado em caracteres e seu limite |
| `/model` ou `/model nome` | Ver ou trocar o modelo para as próximas perguntas, preservando endpoint e TLS |
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

Objetivo, últimos pedidos e até 32 itens estruturados ficam na mesma memória. Há decisões, restrições e pendências registradas pelo usuário (até oito por tipo), e notas atribuídas ao agente. `remember_task(note)` permite ao modelo registrar somente notas, sem transformar suas próprias afirmações em decisões do usuário. Um resumo limitado entra no contexto; itens omitidos continuam recuperáveis pelas ferramentas. A tarefa atual prevalece sobre pedidos anteriores.

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

`/status` mostra estimativa, consumo informado, fator e número de amostras. O JSON de debug registra `reported_prompt_tokens` e `calibrated_scale` por chamada, além de `task_memory`, `project_map` e `conversation_turn_id`. A calibração melhora a estimativa; não altera a janela real do servidor.

## Edição com revisão de diff

```bash
# Chat com propostas de edição habilitadas
codaro chat --repo /caminho/do/projeto
# Investigação sem ferramenta de edição
codaro chat --read-only --repo /caminho/do/projeto
# Uma solicitação de mudança, com revisão e confirmação no terminal
codaro edit 'Adicione uma validação de entrada à função can_edit' --repo examples/demo
```

O agente deve ler o trecho atual e chamar `propose_edit` com o texto original exato, o novo texto e o motivo. A proposta fica na memória; o arquivo permanece intacto. No chat, **Revisar diff** abre o diff com destaque de sintaxe, motivo e ações **Aplicar**, **Rejeitar** e **Voltar**. A ação inicialmente focada é Voltar: Enter não aprova automaticamente. No comando `edit`, cada diff aparece antes da confirmação; a resposta padrão é **não**. `ask` continua somente leitura.

- A aplicação verifica novamente as regras de ignore, o conteúdo original e a identidade do arquivo, e usa substituição atômica no mesmo diretório. Uma alteração concorrente detectada bloqueia a proposta; faça uma nova solicitação sobre o arquivo atual.
- BOM UTF-8, finais de linha LF/CRLF e bits de permissão usuais são preservados. Arquivos binários, links simbólicos, hard links e caminhos proibidos são bloqueados. A aplicação requer POSIX com `dir_fd` e `O_NOFOLLOW`; plataformas sem esses recursos podem investigar, mas não aplicar.
- Até oito propostas por resposta, uma por arquivo. Cada trecho original/novo tem até 3.000 caracteres; o diff tem até 60.000 caracteres. O trecho original deve ocorrer uma única vez e estar inteiramente em linhas lidas, sem truncamento.
- Resolva as propostas antes de outra pergunta. Limpar o chat descarta as propostas pendentes. Uma resposta cancelada ou interrompida também descarta suas propostas.
- Cada arquivo é aprovado e aplicado separadamente: não há transação entre arquivos, criação/exclusão de arquivos pela ferramenta de edição ou persistência de propostas pendentes entre sessões. Desfazer também exige revisão e aprovação. A escrita atômica evita arquivos parcialmente escritos; não impede toda corrida com um processo hostil que altera caminhos simultaneamente.

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

Após aplicar um diff no chat, **Validar alteração** inicia outra investigação: o agente lê o código atual, descobre os testes relevantes e solicita sua execução. A aprovação de um diff não aprova comandos de teste. O resultado real aparece na conversa; quando testes não foram executados, isso continua explícito. A escolha dos testes depende das evidências, das instruções do projeto e do modelo.

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
- `turns`: todas as requisições com mensagens e schemas de ferramentas, orçamento de contexto, resposta estruturada, resultado de cada ferramenta, argumentos normalizados e duração.
- `http_attempts`: tentativas HTTP, status, corpo JSON original ou eventos SSE recebidos, motivo de conclusão e consumo de tokens quando informado pelo servidor. Respostas inválidas e reasoning separado também ficam disponíveis no dump para diagnóstico.
- `events`: atividade do agente; `final_answer` nas conclusões bem-sucedidas e `error` nas falhas/cancelamentos. Uma resposta parcial não vira uma resposta concluída no histórico.
- `compactions`: grupos de ferramentas/histórico removidos, registro das ações e estimativa de tokens antes/depois. Os resultados completos já obtidos continuam em `turns`; erros de contexto e tentativas de recuperação também são registrados.

```bash
# Execute na pasta que você abriu com codaro .
python -m json.tool .codaro/prompt.json
# Resumo, se tiver jq instalado
jq '{run_id, model, status, duration_ms, error}' .codaro/prompt.json
# Chamadas e resultados, preservando o vínculo por ID
jq '.turns[] | {iteration, outcome, calls: .response.tool_calls, results: .tool_results}' .codaro/prompt.json
```

A gravação usa arquivo temporário e substituição atômica, com permissão `0600` em POSIX; links simbólicos e hard links no destino são bloqueados. Há checkpoints antes das requisições e após respostas/ferramentas, além da finalização em sucesso, erro ou cancelamento. Uma interrupção forçada do processo pode deixar o último checkpoint com status `running`. Em sessões simultâneas na mesma pasta, o arquivo corresponde à última gravação; confira `run_id` e a pergunta.

Cabeçalhos de autorização não são registrados e a chave configurada é mascarada, incluindo formas escapadas em JSON. **O arquivo contém perguntas, histórico e código consultado**: a remoção da chave do provedor não remove outros segredos presentes nesses conteúdos. `.codaro` fica fora das buscas do agente e já está no `.gitignore` deste projeto; adicione `.codaro/` ao `.gitignore` de outros projetos em que usar o Codaro. Corpos HTTP de erro ficam limitados a 64 KB; os limites normais de respostas e streaming continuam valendo. Falhas de gravação geram aviso e preservam o resultado ou erro original da investigação.

O formato segue o fluxo de diagnóstico do Thoth: requisições e respostas por iteração, resultados de ferramentas e fechamento atômico. No Thoth, planejamento usa chamadas sem streaming e a síntese tem uma etapa própria. No Codaro, perguntas reconhecidas sobre estrutura e pontos de entrada investigam em JSON até obter leituras de arquivos; depois podem usar streaming. O Codaro também aceita chamadas estruturadas durante SSE, com o mesmo contrato de mensagens no modo JSON e no streaming. O payload é construído pela mesma função usada para calcular o orçamento e registrar a requisição, evitando divergências entre essas representações.

## Estrutura do projeto e evidências

`get_repository_info` descreve a sessão do **Codaro**: pasta, capacidades e escopo. Essas capacidades não são módulos, scripts ou pontos de entrada do projeto explorado. O resultado identifica explicitamente esse escopo.

Perguntas reconhecidas sobre estrutura, arquitetura e pontos de entrada têm uma verificação adicional: a explicação precisa citar `caminho:linha` de arquivos lidos com sucesso **naquela pergunta**. Listagens, previews de busca, metadados, leituras com erro e linhas parcialmente truncadas não satisfazem essa verificação. A investigação começa sem streaming até obter uma leitura válida; texto de chamadas de ferramentas não é emitido como resposta final.

Se o modelo tentar concluir sem evidências, o controlador faz uma recuperação local antes da única tentativa de correção: usa os caminhos realmente disponíveis, escolhe manifestos e candidatos de inicialização e lê trechos do projeto. São até cinco leituras de 60 linhas, com até 1.800 caracteres serializados por resultado e 6.000 caracteres para o contexto recuperado, descontados do orçamento de ferramentas. Em arquivos de inicialização conhecidos, a recuperação procura a declaração de `main`/`bootstrap` para localizar o trecho mesmo após um bloco longo de imports/declarations. Esses limites e nomes conhecidos não substituem uma investigação completa: o modelo ainda pode pedir outras leituras.

A explicação rejeitada permanece no JSON de diagnóstico, mas não é enviada novamente como fatos nem salva no histórico da conversa. A recuperação entra na mensagem `system` inicial; cada requisição mantém uma única mensagem desse tipo. O dump registra as leituras do controlador em `local_retrievals`, separadamente de chamadas solicitadas pelo modelo. Persistindo a falta de evidências ou terminando o orçamento, o Codaro informa a falha. Se o índice não encontrar nenhum arquivo permitido, o resultado explica a limitação do escopo. Consultas sobre pasta/ferramentas, listagens e perguntas conceituais gerais continuam sem exigir leituras de implementação.

Arquivos de regras de ignore, como `.gitignore`, podem ser lidos para diagnosticar exclusões, mas não contam como implementação nem pontos de entrada.

O reconhecimento desse tipo de pergunta usa padrões em português e inglês; não classifica toda intenção possível. A verificação confirma leitura e uma citação dentro das linhas lidas, sem garantir que todas as conclusões do modelo estejam corretas ou que a evidência escolhida seja suficiente para toda a pergunta.

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

- Até 8 etapas de ferramentas e 8 chamadas por etapa; finalização sem ferramentas após o limite.
- Leituras de até 160 linhas e 6.000 caracteres, com indicação de truncamento e próxima linha. `partial_line` indica que uma única linha excedeu o orçamento; não conclua sobre a parte omitida.
- Até 12 resultados por busca, com previews de 240 caracteres.
- Orçamento de 24.000 caracteres de resultados por pergunta. É uma aproximação de volume, não uma contagem exata de tokens.
- Histórico de perguntas e respostas limitado a 16.000 caracteres, sem reter corpos de ferramentas entre turnos. A requisição completa respeita o teto adicional de 64.000 caracteres e o orçamento estimado de tokens, incluindo instruções, mensagens e schemas.
- Janela configurável em tokens: padrão **16.384**, reserva de saída **1.400** e margem **512**. Esses valores são limites do cliente; configure a janela realmente habilitada no servidor. O tamanho arquitetural anunciado do modelo não garante a configuração do endpoint.
- A partir de 85% do orçamento, o agente remove turnos antigos e compacta grupos completos de chamadas/respostas da investigação atual. Um registro limitado preserva caminhos, ações e status de comandos/propostas, sem tratar código removido como prova. A conversa salva e os resultados no JSON de debug são preservados. Trechos descartados precisam ser relidos antes de justificar conclusões/edições; pares `tool_calls`/`tool` nunca são quebrados.
- Leituras, buscas e listagens são reduzidas conforme o espaço restante. Leituras idênticas/contidas não repetem conteúdo quando o arquivo não mudou; intervalos com prefixo sobreposto enviam somente as novas linhas. Após compactação, as leituras podem ser feitas novamente. Comandos/propostas idênticos reutilizam o resultado dentro da mesma investigação; para repetir uma execução, inicie nova pergunta. Erros de leitura podem ser tentados novamente.
- Rejeições reconhecidas de contexto do servidor reduzem o orçamento e permitem até duas novas tentativas ao modelo, sem reexecutar ferramentas. Outros erros HTTP mantêm seu tratamento normal. Se pergunta, instruções e schemas não couberem, o agente explica a configuração necessária em vez de enviar uma requisição localmente excessiva. A investigação continua limitada a oito etapas e ao volume total de resultados por pergunta; compactação não significa análise ilimitada.
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

Arquivos do repositório são tratados como dados pelo prompt do agente. As ferramentas de leitura e de proposta validam argumentos no programa. O modelo não recebe uma ferramenta de aplicação: a escrita depende da aprovação na interface.

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
- Criação de arquivos e transações de edição envolvendo vários arquivos.


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

O modo local mede precisão dos até seis resultados, recall dos caminhos esperados e duração. `--agent` mede conclusão, correspondência das expectativas textuais, precisão das citações em linhas efetivamente lidas, chamadas ao modelo/ferramentas, chamadas repetidas, compactações, estimativas e usage disponível. O relatório informa quantas chamadas possuem consumo real; ausência de usage não é apresentada como consumo zero. Casos com erro não interrompem os seguintes; o comando retorna código 1 se alguma expectativa falhar.

Avaliações do agente não permitem edição/comandos e usam memória efêmera, preservando a memória das conversas do projeto. Atualizam o índice e o último JSON de debug. Os casos iniciais são testes de fumaça; métricas objetivas e correspondências textuais não substituem revisão humana da qualidade semântica. Compare relatórios ao mudar modelo, prompts, recuperação ou limites de contexto.
