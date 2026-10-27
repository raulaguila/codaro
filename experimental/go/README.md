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
3. Preencha URL, chave quando necessária e janela de trabalho. Tab navega;
   Ctrl+S consulta o catálogo sem fazer inferência. TLS sem verificação é opcional.
4. Busque um modelo, navegue com ↑/↓ e pressione Enter para selecionar.
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

- Chat com Markdown, streaming e input multilinha por Alt+Enter.
- Menu `/` e Ctrl+P com navegação por setas, Enter e Esc, preservando o rascunho.
- Catálogo de modelos, busca, metadados de contexto e configuração de TLS.
- Tool-calling nativo com listagem, mapa do projeto, leitura paginada e busca
  literal. Ferramentas executam somente leitura e não recebem um shell do modelo.
- Pensamento enviado pelo provedor e atividades em ordem. Texto intermediário
  continua no processo quando o modelo chama ferramentas; a resposta final fica
  separada. Ctrl+T ou `/process` expande e posiciona a leitura no processo atual.
- PgUp/PgDn e mouse para rolar; Ctrl+End volta ao acompanhamento do final.
- Cancelamento por contexto, limitação de iterações e recuperação limitada de
  resposta vazia, chamadas JSON em texto e rejeição de contexto pelo servidor.
- Diagnóstico da última interação em JSON, sem registrar a chave configurada.

Este fluxo é **Perguntar**, com histórico em memória. Planejamento persistente,
edições, execução de comandos, aprovações, MCP, LSP, embeddings e migração de sessões
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
| `CODARO_GO_CONTEXT_WINDOW` | Janela de trabalho escolhida, 4096–262144 tokens |
| `CODARO_GO_TLS_INSECURE` | `true` para o provedor configurado |
| `CODARO_GO_CONFIG_DIR` | Diretório alternativo para o experimento |

O protótipo distingue **capacidade do modelo informada pela API** e **janela de
trabalho escolhida**. O padrão de trabalho é 8192 tokens, mesmo se o modelo
suportar 131072; ajuste o campo de contexto para testar uma janela maior. Isso
evita reservar automaticamente uma janela grande no Ollama local. Se a API informar
uma capacidade menor, o limite ativo é reduzido. `/status` apresenta ambos os valores.

O orçamento usa estimativa conservadora por bytes, reserva 25% e libera histórico
e resultados antigos preservando pares de chamadas/resultados. Não define
`max_tokens`/`num_predict`: a saída usa o padrão do provedor. Servidores podem ter
limites próprios inferiores aos metadados, e ainda existem cenários em que o
conteúdo fixo não cabe. Não há promessa de paridade com o controle de contexto Python.

O stream aceita até 16 MiB por chamada e eventos de até 1 MiB. A taxa mostrada
durante a geração é uma estimativa, incluindo o tempo de investigação; não mede
o desempenho nativo do servidor. O histórico visual mantém até 20 turnos, e o
contexto de conversa conserva até quatro trocas anteriores.

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
```

Os testes cobrem protocolos HTTP simulados, fragmentação de ferramentas,
cancelamento, contexto, política de arquivos, cursores Unicode e navegação da UI.
São uma base de avaliação; uma rodada com seu servidor/modelo continua necessária.

Bibliotecas: Bubble Tea, Bubbles, Lip Gloss e Glamour. Veja `go.mod` e as licenças
dos respectivos módulos. O código próprio segue a [licença MIT do Codaro](../../LICENSE).
