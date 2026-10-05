# Codaro CLI

Assistente de IA no terminal para investigar repositórios locais, com fontes no código e recuperação progressiva de contexto.

> MVP de investigação: busca, leitura e explicação. Edição de arquivos, execução de comandos, streaming e referências via LSP estão no roadmap.

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
```

O chat tem painéis de conversa, repositório/modelo e atividade; a lateral é ocultada em terminais menores que 90 colunas. `Ctrl+L` limpa a conversa, `Ctrl+X` solicita cancelamento e `Ctrl+Q` encerra. O cancelamento impede novas ações e descarta a resposta, mas aguarda uma requisição de rede ativa terminar ou atingir o timeout. O histórico fica apenas na memória da sessão. O modelo deve suportar `tools` na API de chat completions; a confiabilidade das chamadas varia conforme modelo e servidor.

## API compatível com OpenAI

```bash
export CODARO_BASE_URL=https://api.openai.com/v1
export CODARO_MODEL=gpt-4.1-mini
export CODARO_API_KEY='sua-chave'
codaro chat --repo /caminho/do/projeto
```

Na investigação com IA, a pergunta, o histórico e os trechos lidos são enviados ao endpoint configurado. Os comandos locais `index`, `search` e `read` não chamam modelos por padrão. Não coloque chaves no código ou no Git.

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
- Histórico de perguntas e respostas limitado a 16.000 caracteres, sem reter corpos de ferramentas entre turnos. Antes de cada chamada, turnos antigos são removidos para respeitar o limite de 64.000 caracteres da requisição serializada, incluindo mensagens e schemas. Isso não garante caber na janela de tokens de todo modelo; ajuste os limites do `Agent` conforme o provedor.
- Leituras idênticas não repetem o conteúdo dentro do mesmo turno quando o hash do arquivo não mudou. Erros podem ser tentados novamente; buscas continuam verificando o conteúdo atual.
- Até 20.000 arquivos de 512 KB cada. Caminhos externos e links simbólicos são rejeitados. No POSIX, leituras usam descritores de diretório e `O_NOFOLLOW`; o caminho alternativo para plataformas sem esse recurso não oferece a mesma proteção contra substituições concorrentes de diretórios.
- `.env*`, nomes contendo `secret`/`credential`, chaves privadas e diretórios gerados são excluídos. Isso não detecta todos os segredos; revise as exclusões antes de usar uma API externa.

Para excluir arquivos adicionais, crie `.codaroignore` na raiz do projeto:

```gitignore
private/**
customer_data.json
```

Arquivos do repositório são tratados como dados pelo prompt do agente. Esta versão oferece somente ferramentas de leitura, com validação de argumentos no programa.

## Desenvolvimento

```bash
pytest -q
ruff check .
ruff format --check .
```

Os testes verificam recuperação, atualização e migração do índice, rollback, restrições de arquivos, limites do agente, contratos HTTP, CLI e interação real com a interface Textual em modo headless. Os modelos e respostas HTTP são simulados; não há chamadas pagas ou acesso externo nos testes. O CI está configurado para Python 3.11, 3.12 e 3.13 em Linux. A auditoria local foi executada em Python 3.12.

Veja os achados e as limitações em [AUDIT.md](AUDIT.md).

## Próximas entregas

- Embeddings multilíngues opcionais e combinação com a busca textual.
- Benchmark com perguntas reais, Recall@5, latência e volume de contexto.
- Reranking quando houver ganho medido.
- Definições e referências via LSP, com novos parsers Tree-sitter.
- Streaming e interrupção imediata da requisição de rede no chat.
- Edição por patches, revisão de diff e execução controlada de testes.


## Diagnóstico e recuperação

`codaro doctor` verifica ripgrep, SQLite FTS5 e a configuração do modelo, sem chamar a API ou mostrar a chave. Retorna código 1 se um requisito local estiver ausente.

- Configure `CODARO_TIMEOUT` entre 1 e 300 segundos (padrão: 90). É um limite por operação HTTP, não um prazo total de investigação.
- Respostas 429, 502, 503 e 504 têm até duas novas tentativas com espera curta. Outros erros retornam uma mensagem sem expor o corpo remoto.
- Use um modelo/servidor compatível com ferramentas na API OpenAI. Modelos só de completions não bastam. O modelo padrão é `qwen2.5:7b`; você pode substituí-lo por outro com suporte a tools.
- O índice é um cache derivado. Se estiver corrompido, feche processos Codaro, renomeie a pasta `.codaro` e execute `codaro index --repo ...` novamente. Formatos antigos conhecidos são reconstruídos ao atualizar a versão do schema.
- Conteúdo binário e texto fora de UTF-8 são ignorados no índice e rejeitados na leitura. Arquivos vazios são suportados.
- A atualização calcula hashes dos arquivos para detectar mudanças. Em repositórios grandes, esse I/O pode ser significativo; use `.codaroignore` para manter o escopo útil.
