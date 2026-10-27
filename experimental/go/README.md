# Codaro Go — experimento

Primeiro fluxo funcional da versão Go, desenvolvido separadamente do aplicativo
Python. O objetivo é experimentar interface, streaming e ferramentas antes de
decidir sobre uma migração. A implementação é própria; Crush é uma referência de
arquitetura. Não é um fork nem inclui código do Crush.

## Testar agora

Requisitos: **Go 1.25+**, **ripgrep (`rg`)** no PATH e terminal com pelo menos
60 colunas × 24 linhas. O primeiro build baixa as dependências declaradas em
`go.mod`/`go.sum`.

Na raiz do repositório:

```bash
cd experimental/go
go run ./cmd/codaro --demo ../..
```

Digite uma pergunta e pressione Enter. A demonstração lista e lê arquivos reais
do diretório escolhido, com um fluxo determinístico identificado como demonstração.
Ela não chama um modelo e não precisa de credenciais.

Para usar IA:

```bash
go run ./cmd/codaro ../..
```

1. Digite `/providers` ou use Ctrl+P e selecione essa opção com as setas e Enter.
2. Escolha **Ollama local** ou um provedor **OpenAI compatible** com ←/→.
3. Preencha URL e chave quando necessária. Deixe contexto vazio para usar a API.
   Tab navega;
   Ctrl+S consulta o catálogo sem fazer inferência. TLS sem verificação é opcional.
4. Busque um modelo, clique na linha ou use ↑/↓ e Enter para selecionar.
5. Pergunte sobre o projeto. Ctrl+X cancela a atividade; Ctrl+Q encerra.

Ollama usa `/api/tags`, `/api/show` e `/api/chat`, com `options.num_ctx` explícito.
APIs OpenAI compatible usam `/models` e `/chat/completions`. Há presets para OpenAI,
Groq e Gemini pela interface compatível. Anthropic nativo ainda não está neste
experimento. Suporte real depende do endpoint e do modelo aceitarem ferramentas.

Para executar em qualquer diretório sem instalar sobre o Codaro Python:

```bash
go build -o bin/codaro-go ./cmd/codaro
./bin/codaro-go /caminho/do/projeto
```

No Windows, use `go build -o bin/codaro-go.exe ./cmd/codaro`.

Pergunta única, útil para comparar o motor sem renderização:

```bash
./bin/codaro-go --demo --prompt "Explique este projeto" ../..
```

As flags vêm antes do diretório. Sem flags, o diretório padrão é o atual.

## O que já pode ser avaliado

- Chat com Markdown, streaming e input que cresce até três linhas por Alt+Enter.
- Tela inicial limpa, rodapé contextual e controles de pensamento e ferramentas
  separados em `/thinking` e `/details`. O processo também abre por clique.
- Menu `/` e Ctrl+P com navegação por setas, Enter e Esc, preservando o rascunho.
- Seletor unificado de modelos por provedor, busca aproximada, favoritos (Ctrl+S),
  filtro de favoritos (Ctrl+F) e recentes. Ctrl+R atualiza o catálogo. Metadados são
  consultados após 200 ms na seleção destacada, com cancelamento ao navegar.
  Capacidades aparecem somente quando informadas pela API.
- Catálogo em cache permite escolher enquanto a atualização acontece ao fundo;
  uma falha de provedor mantém os dados anteriores disponíveis.
- `@` busca arquivos permitidos, com ↑↓ e Enter/Tab. Até três referências explícitas
  são lidas como resultados reais de ferramentas, em páginas iniciais de até 60
  linhas e 2400 bytes; arquivos maiores exigem continuar pelas ferramentas.
- Perguntas do agente reúnem opções verticais e resposta livre no mesmo componente
  do input. ↑↓ escolhe; Enter envia a opção ou o texto digitado; Esc cancela a
  atividade. Ctrl+O abre o texto completo de uma pergunta longa.
- Plano estruturado com `update_plan`, recolhível por clique ou Ctrl+B, sempre junto
  ao input enquanto houver tarefas pendentes. Alt+↑↓ percorre listas maiores;
  `/cancel-plan` cancela as etapas pendentes e interrompe a atividade em andamento.
- Enter durante uma geração adiciona uma mensagem à fila (até cinco). `/queue`
  retira a última mensagem para editar. Cancelar ou falhar interrompe a fila.
- `/sessions` retoma conversas salvas por projeto, incluindo plano e rascunho, sem
  repetir ações. Ctrl+R pesquisa as mensagens da conversa; ↑ no input vazio recupera
  a última pergunta enviada nesta execução.
- `/export` salva a conversa e o processo completos em Markdown no diretório global.
- `/diff` mostra alterações rastreadas em relação ao HEAD, sem editores externos;
  não mostra arquivos novos não rastreados. Requer Git e um commit inicial.
- Seletores de modelo e modo clicáveis no header, inclusive em terminais compactos.
  Ctrl+L abre modelos; Shift+Tab alterna Perguntar/Planejar. `/mode` abre os modos.
  Trocar modelo ou modo preserva a conversa e o rascunho; a troca aguarda o término
  ou cancelamento de uma geração em andamento.
- Tool-calling nativo com listagem, mapa do projeto, leitura paginada e busca
  literal. Ferramentas executam somente leitura e não recebem um shell do modelo.
- Pensamento enviado pelo provedor e atividades em ordem. Texto intermediário
  continua no processo quando o modelo chama ferramentas; a resposta final fica
  separada. Ctrl+T ou `/process` expande e posiciona a leitura no processo atual.
- PgUp/PgDn e mouse para rolar; Ctrl+End volta ao acompanhamento do final.
- Cancelamento por contexto, limitação de iterações e recuperação limitada de
  resposta vazia, chamadas JSON em texto e rejeição de contexto pelo servidor.
- Diagnóstico da última interação em JSON, sem registrar a chave configurada.

Os modos disponíveis são **Perguntar** e **Planejar**, com histórico e planos salvos por projeto.
Planejar muda as instruções do agente para investigar e propor etapas, critérios e
verificações, mantendo ferramentas de leitura. O modo Executar aparece como
indisponível no seletor. Edições, execução de comandos, aprovações, MCP, LSP, embeddings e migração de sessões
Python são etapas futuras. A busca atual é textual, independente da linguagem;
ela não substitui a análise estrutural multilíngue da versão Python.

## Configuração e contexto

Configuração própria em `os.UserConfigDir()/codaro-go/config.json`: normalmente
`~/.config/codaro-go` no Linux, `~/Library/Application Support/codaro-go` no macOS
e `%AppData%\codaro-go` no Windows. Arquivos são criados com modo 0600 quando o
sistema suporta permissões POSIX. A chave fica somente na sessão ou no ambiente,
sem ser salva no arquivo de configuração. Credenciais Python não são importadas.

Variáveis opcionais, todas com prefixo próprio:

| Variável | Finalidade |
| --- | --- |
| `CODARO_GO_PROVIDER` | `ollama` ou `openai` |
| `CODARO_GO_BASE_URL` | URL do provedor |
| `CODARO_GO_MODEL` | Identificador do modelo |
| `CODARO_GO_API_KEY` | Credencial, consultada somente nesta execução |
| `CODARO_GO_CONTEXT_WINDOW` | Override explícito, 1024–2000000 tokens; 0 usa a API |
| `CODARO_GO_MODE` | `ask` ou `plan` |
| `CODARO_GO_TLS_INSECURE` | `true` para o provedor configurado |
| `CODARO_GO_CONFIG_DIR` | Diretório alternativo para o experimento |

O contexto é **automático por padrão**: se a API informa 131072 tokens, esse é o
limite ativo. O protótipo consulta metadados ao selecionar o modelo e também ao
iniciar com um modelo configurado. No Ollama, o valor é enviado em `options.num_ctx`;
uma janela maior pode exigir mais memória no servidor.

Ollama usa `parameters.num_ctx` quando definido no modelo, ou o contexto da
arquitetura principal em `model_info`, evitando confundir o contexto do encoder
visual com o da linguagem. APIs compatíveis reconhecem campos como
`context_length`, `context_window`, `max_model_len` e `inputTokenLimit`, além de
restrições `top_provider.context_length`.

O campo opcional `context_limit` é um teto manual. Zero ou ausente mantém o modo
automático; `context_window` é o valor efetivo, não um override. Configurações do
primeiro protótipo que mantinham 8192 passam a usar o valor da API automaticamente.
`/status` apresenta limite ativo, valor da API, override e origem dos dados.

Quando a API não informa o contexto, mantém-se o último valor válido do mesmo
modelo/provedor, identificado como cache. Sem metadados nem cache, o fallback é
8192 tokens, explicitamente identificado. A API pública da OpenAI, por exemplo,
nem sempre informa limites em `/models`; a ausência não é tratada como dado real.
O seletor mantém um cache por provedor; Ctrl+R atualiza o catálogo, e a seleção
consulta os detalhes atuais antes de aplicar o modelo.

O orçamento usa estimativa conservadora por bytes, reserva 25% e libera histórico
e resultados antigos preservando pares de chamadas/resultados. Não define
`max_tokens`/`num_predict`: a saída usa o padrão do provedor. Servidores podem ter
limites próprios inferiores aos metadados, e ainda existem cenários em que o
conteúdo fixo não cabe. Não há promessa de paridade com o controle de contexto Python.

O stream aceita até 16 MiB por chamada e eventos de até 1 MiB. A taxa mostrada
durante a geração é uma estimativa por bytes, a partir do primeiro trecho de texto
da resposta atual; não mede o desempenho nativo do servidor. O histórico visual mantém até 20 turnos, e o
contexto de conversa conserva até quatro trocas anteriores.

## Provedores, preferências e conversas

O diretório global também contém `providers.json` (até 20 provedores), `ui.json`
(favoritos e preferências visuais) e `models.json` (cache de catálogo). O cadastro
pela UI guarda URL/protocolo/TLS, mantendo chaves somente na memória. Para ter
credenciais disponíveis em próximas execuções, cada perfil aceita `api_key_env`:

```json
[
  {
    "name": "Ollama local",
    "settings": {
      "provider": "ollama",
      "base_url": "http://localhost:11434",
      "context_window": 8192
    }
  },
  {
    "name": "Servidor da equipe",
    "api_key_env": "TEAM_LLM_API_KEY",
    "settings": {
      "provider": "openai",
      "base_url": "https://llm.example.com/v1",
      "context_window": 8192
    }
  }
]
```

`context_window` nesse cadastro é fallback; a seleção resolve os dados da API.
Um `providers.json` inválido é preservado para correção manual. O seletor consulta
somente os provedores cadastrados e o provedor ativo; o cache não cadastra endpoints.

As últimas dez conversas ficam em `runs/<hash-do-diretório>/sessions/`, fora do
repositório, com até 20 turnos por conversa e limite de 4 MiB por arquivo. O salvamento
acontece ao terminar uma atividade, criar outra conversa ou sair. Sessões podem conter
código e texto da conversa; as chaves conhecidas dos provedores são mascaradas.
Pensamento e detalhes não são reenviados automaticamente ao modelo ao retomar: o
histórico usa somente as perguntas e respostas finais concluídas, e o plano entra
nas instruções da próxima atividade. Uma pergunta pendente não é reexecutada ao retomar.

A prévia visual limita respostas finais a 64 mil caracteres e texto em geração
a 24 mil, para evitar travar o terminal em saídas muito longas; `/export` preserva
o texto completo capturado. Detalhes de cada etapa usam prévias de até 6 mil caracteres.

A escolha de mostrar pensamento e detalhes é independente e fica em `ui.json`.
A rolagem para cima suspende o acompanhamento automático; Ctrl+End volta ao final.
Trocar de atividade ou receber novas ferramentas preserva o conteúdo das etapas;
concluir recolhe o processo somente quando o usuário está acompanhando o final.

## Fontes e diagnóstico

As ferramentas respeitam `.gitignore`, `.codaroignore` e exclusões de diretórios
gerados, credenciais e arquivos `.env`. Leituras usam `os.Root`, rejeitam symlinks,
binários e arquivos acima de 512 KB. Listagem limitada a 20 mil arquivos; busca a
2000 arquivos por consulta e páginas de até 20 ocorrências. Refine `path` quando
necessário. Listagens e mapas não comprovam comportamento do código.

`/status` mostra o caminho do último fluxo:

```text
<configuração>/codaro-go/runs/<hash-do-diretório>/prompt.json
```

O arquivo registra requisições, respostas, ferramentas, durações e estado final,
inclusive cancelamento. A gravação ocorre no término do fluxo; saídas muito longas
possuem prévias limitadas identificadas. Ele pode conter trechos do projeto e da
conversa, portanto revise-o antes de compartilhar. Não contém cabeçalhos HTTP.

## Validar

```bash
go test ./...
go test -race ./...
go vet ./...
go build -o bin/codaro-go ./cmd/codaro
# Unix: app real em PTY com servidor HTTP local simulado
python3 scripts/smoke_terminal.py
```

Os testes cobrem protocolos HTTP simulados, fragmentação de ferramentas,
cancelamento, contexto, política de arquivos, cursores Unicode e navegação da UI.
São uma base de avaliação; uma rodada com seu servidor/modelo continua necessária.

Bibliotecas: Bubble Tea, Bubbles, Lip Gloss e Glamour. Veja `go.mod` e as licenças
dos respectivos módulos. O código próprio segue a [licença MIT do Codaro](../../LICENSE).

## Referências do Crush

Consulta ao código em 27/10/2026, sem incorporar código FSL:

- [Catálogo de provedores](https://github.com/charmbracelet/crush/blob/main/internal/config/provider.go):
  dados Catwalk, cache e catálogo embarcado; não pressupõe que todos os limites
  venham da API do próprio provedor.
- [Seleção de modelos](https://github.com/charmbracelet/crush/blob/main/internal/ui/dialog/models.go):
  diálogo pesquisável e aplicação explícita da seleção.
- [Eventos e interação](https://github.com/charmbracelet/crush/blob/main/internal/ui/model/ui.go):
  mouse roteado primeiro para diálogos, regiões da interface e troca preservando
  o contexto; Shift+Tab alterna modos no Crush.

O Codaro aplica esses princípios com implementação própria: API como primeira
fonte, cache/fallback identificados, diálogos que preservam o rascunho e áreas de
clique calculadas pelo mesmo layout que renderiza o header.
