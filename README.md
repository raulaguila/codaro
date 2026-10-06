# Codaro CLI

Assistente de IA no terminal para investigar repositórios locais, com fontes no código e recuperação progressiva de contexto.

> Busca, leitura, explicação e edição de trechos com diff e aprovação. Execução de comandos, referências via LSP e interrupção imediata de conexões ociosas estão no roadmap.

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

O chat tem painéis de conversa, repositório/modelo e atividade; a lateral é ocultada em terminais menores que 90 colunas. `Ctrl+L` limpa a conversa, `Ctrl+X` solicita cancelamento e `Ctrl+Q` encerra. O cancelamento é verificado entre fragmentos da resposta e chamadas de ferramentas; se o servidor estiver parado sem enviar dados, aguarda o próximo fragmento ou o timeout. O histórico fica apenas na memória da sessão. O modelo deve suportar `tools` na API de chat completions; a confiabilidade das chamadas varia conforme modelo e servidor.

## Interface e streaming

- `codaro chat` mostra a resposta enquanto ela chega, em Markdown, com títulos, listas, tabelas e destaque de sintaxe em blocos de código.
- Cada ação mostra consulta ou arquivo/símbolo, quantidade de resultados ou linhas, duração e indicação de leitura parcial ou reutilização de conteúdo.
- As ações também aparecem na conversa para ficarem visíveis em terminais estreitos. A lateral mantém o histórico de atividades.
- A barra de status mostra o volume de contexto enviado em caracteres, não tokens.
- `codaro ask` também atualiza a resposta progressivamente no terminal e envia as informações de atividade para stderr.
- O provedor usa SSE da API OpenAI-compatible. Se o servidor responder com JSON comum, a resposta é exibida de uma vez.
- Uma resposta interrompida ou cancelada não é salva no histórico. O chat remove o bloco parcial e informa a interrupção.

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
- Cada arquivo é aprovado e aplicado separadamente: não há transação entre arquivos, criação/exclusão de arquivos, execução de testes, undo automático ou persistência de propostas entre sessões. A escrita atômica evita arquivos parcialmente escritos; não impede toda corrida com um processo hostil que altera caminhos simultaneamente.

## API compatível com OpenAI

```bash
export CODARO_BASE_URL=https://api.openai.com/v1
export CODARO_MODEL=gpt-4.1-mini
export CODARO_API_KEY='sua-chave'
codaro chat --repo /caminho/do/projeto
```

Na investigação com IA, a raiz absoluta do projeto, a pergunta, o histórico e os trechos lidos são enviados ao endpoint configurado. Os comandos locais `index`, `search` e `read` não chamam modelos por padrão. Não coloque chaves no código ou no Git.

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
- Benchmark com perguntas reais, Recall@5, latência e volume de contexto.
- Reranking quando houver ganho medido.
- Definições e referências via LSP, com novos parsers Tree-sitter.
- Interrupção imediata de conexões que estejam sem enviar fragmentos.
- Execução controlada de testes, criação de arquivos e reversão de edições.


## Diagnóstico e recuperação

`codaro doctor` verifica ripgrep, SQLite FTS5 e a configuração do modelo, sem chamar a API ou mostrar a chave. Retorna código 1 se um requisito local estiver ausente.

- Configure `CODARO_TIMEOUT` entre 1 e 300 segundos (padrão: 90). É um limite por operação HTTP, não um prazo total de investigação.
- Respostas 429, 502, 503 e 504 têm até duas novas tentativas com espera curta. Uma conexão interrompida depois de começar a resposta não é repetida automaticamente, para evitar duplicações. Outros erros retornam uma mensagem sem expor o corpo remoto.
- Use um modelo/servidor compatível com ferramentas na API OpenAI. Modelos só de completions não bastam. O modelo padrão é `qwen2.5:7b`; você pode substituí-lo por outro com suporte a tools.
- O índice é um cache derivado. Se estiver corrompido, feche processos Codaro, renomeie a pasta `.codaro` e execute `codaro index --repo ...` novamente. Formatos antigos conhecidos são reconstruídos ao atualizar a versão do schema.
- Conteúdo binário e texto fora de UTF-8 são ignorados no índice e rejeitados na leitura. Arquivos vazios são suportados.
- A atualização calcula hashes dos arquivos para detectar mudanças. Em repositórios grandes, esse I/O pode ser significativo; use `.codaroignore` para manter o escopo útil.
