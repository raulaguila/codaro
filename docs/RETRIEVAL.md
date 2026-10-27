# Recuperação de código e entendimento do projeto

O Codaro combina um mapa automático, busca lexical e por símbolos, leitura de
trechos atuais e memória de investigação. O modelo recebe uma orientação pequena;
o restante pode ser consultado por ferramentas paginadas. Mapa, preview, memória
e outline ajudam a localizar código, mas não autorizam alterações nem substituem
a leitura da implementação atual.

## Mapa automático

Ao iniciar uma interação, o agente atualiza o índice e constrói o mapa local.
Arquivos sem alterações reutilizam os dados indexados; alterações, remoções e
mudanças de ignore atualizam o mapa. Não é necessário executar um comando antes
de conversar. Uma projeção pequena orienta as buscas; perguntas de arquitetura
também recebem candidatos a manifestos e pontos de entrada.

`get_project_map` permite aprofundar um diretório com `path` e recuperar o JSON
por `offset`/`limit`. `/map` ou `codaro map --repo .` também exibem o mapa. Na CLI,
`codaro map --repo . --path packages/api` restringe o escopo.

O mapa inclui:

- Diretórios e contagens de arquivos, árvore compacta e amostra de caminhos.
- Linguagens, manifestos, pacotes e candidatos a funções de inicialização.
- Scripts e dependências declarados em `package.json`, `pyproject.toml` e
  `Cargo.toml`; identificação de projetos Go, Java e .NET.
- Relações candidatas entre imports/includes e arquivos locais. Imports que não
  puderam ser relacionados são apresentados separadamente.

Os manifests são interpretados como dados: nenhum script de instalação, build,
teste ou inicialização é executado para produzir o mapa. Limites são indicados
por `truncated`; aprofunde `path` em projetos grandes.

Imports dinâmicos, aliases de bundlers, classpaths e configurações específicas
de build podem exigir inspeção adicional. Uma relação marcada
`static_path_candidate` não prova um fluxo de execução.

## Linguagens e níveis de suporte

As gramáticas Tree-sitter são distribuídas com a dependência
`tree-sitter-language-pack==0.13.0`: não há download de parsers ao abrir um projeto.
O catálogo `codaro.languages.EXTENSIONS` mapeia 78 linguagens e formatos,
incluindo Python, Go, Java, JavaScript, JSX, TypeScript, TSX, C, C++, C#, Rust,
SQL, Ruby, PHP, Kotlin, Swift, Scala, Bash, Lua, Dart, Elixir, Erlang, Haskell,
OCaml, R, Julia, Perl, PowerShell, Clojure, F#, Fortran, Zig, Solidity, Protobuf,
GraphQL, Vue e Svelte. Há ainda gramáticas para formatos de configuração,
infraestrutura, documentação e outras linguagens populares.

Os filtros usam um registro único de extensões, nomes e aliases em
`codaro.languages`. Dockerfile/Containerfile e Makefile/GNUmakefile são
reconhecidos por nome, além dos 78 registros por extensão; `text` cobre formatos
permitidos sem gramática. O catálogo completo, aliases e paginação de
`get_language_support` estão em [TOOLS.md](TOOLS.md#catálogo-de-linguagens-e-aliases).
O filtro `ocaml` inclui `.ml` e `.mli`, mantendo a gramática de interface
`ocaml_interface`; essa regra também vale para a busca semântica.

Os níveis são diferentes:

1. **Texto:** indexação e busca dos arquivos permitidos, mesmo sem símbolos.
2. **Estrutura:** extração de declarações reconhecidas pela gramática e pelo
   extrator, com nomes, tipos, assinatura e linhas. Os testes incluem exemplos
   representativos de 36 linguagens/formatos. Isso não garante todas as
   construções e dialetos de cada linguagem.
3. **Resolução:** definição/referências por posição com um servidor LSP instalado
   e explicitamente habilitado. Depende do servidor e da configuração do projeto.

Node.js utiliza as gramáticas JavaScript/TypeScript e metadados de `package.json`.
Vue/Svelte extraem funções de blocos `<script>`, preservando as linhas originais.
Groovy utiliza a gramática Java para declarações compatíveis. Em arquivos `.h`,
marcadores de sintaxe C++ orientam a extração; nomes e extensões ambíguos continuam
sujeitos à convenção do projeto. SQL possui diferenças entre dialetos: trechos
que não produzirem símbolos continuam disponíveis como texto.

## Busca e navegação

| Ferramenta | Uso |
| --- | --- |
| `search_code` | Termos, nomes exatos e ranking por cobertura, com filtros |
| `grep_code` | Texto literal ou regex Rust, com paginação |
| `list_files` | Caminhos permitidos com filtros e paginação |
| `get_project_map` | Orientação por diretórios, pacotes e imports |
| `get_file_outline` | Declarações do arquivo atual, sem enviar todos os corpos |
| `find_definition` | Definições por nome ou tentativa LSP por posição |
| `find_references` | Referências LSP ou ocorrências textuais identificadas |
| `read_symbol` / `read_lines` | Implementação atual e continuação com hash |
| `record_finding` / `search_findings` | Descobertas, hipóteses e pendências com fontes |

`search_code`, `grep_code`, `list_files` e navegação por nome aceitam `path`,
`glob`, `language` e `extension`. `search_code` aceita também `kind`.
Filtros são aplicados antes da seleção dos melhores resultados, evitando que
resultados de outros diretórios esgotem o limite. Alguns aliases de linguagem:
`node`/`js`, `ts`, `py`, `c++`, `c#`, `rs` e `shell`.

```bash
codaro search "authorize" --repo . --path src --language ts --json
codaro search "permissão usuário" --repo . --extension py --kind method
```

A busca combina BM25, correspondência exata e cobertura dos termos. Há expansão
limitada de conceitos comuns em português/inglês, não um tradutor geral. Símbolos
relevantes têm prioridade sobre janelas genéricas e resultados sobrepostos são
deduplicados. Comentários imediatamente anteriores ajudam a localizar declarações.

`grep_code` usa o ripgrep em texto lido através da política do repositório, sem
shell implícito. Regex inválida gera retorno de ferramenta para o agente corrigir.
Paginação usa `offset`/`next_offset`; uma redução pelo orçamento mantém o cursor
no primeiro resultado omitido. O padrão deve ser refinado se a busca exceder o prazo.

As ferramentas novas são carregadas sob demanda via `request_tools` ou descobertas
no catálogo. Isso evita anexar todos os schemas a cada solicitação, especialmente
em modelos com contexto pequeno.

## LSP opcional

Habilite LSP em `/features` e instale/configure o servidor apropriado. Existem
comandos padrão para Python, JS/TS, Go, Java, C/C++, C#, Rust, SQL, Ruby, PHP,
Kotlin, Swift, Lua, Dart, Elixir e Zig. O Codaro não instala esses executáveis.
Comandos personalizados ficam em `lsp.servers`, em `.codaro/features.json`.

Para navegar por LSP, o modelo envia `path`, `line` (base 1) e `column` (UTF-16,
base 0). O resultado informa `resolved: true` quando veio do servidor. URIs fora
dos arquivos permitidos são descartadas. Servidor indisponível/incompatível ou
arquivo alterado durante a consulta levam à alternativa local.

`find_references` com `resolved: false` inclui homônimos, strings e comentários.
`find_definition` sem posição retorna candidatos por nome; não resolve tipos.
Nenhuma dessas respostas registra o arquivo como lido para autorizar edição.

## Memória de investigação

`record_finding` registra `finding`, `hypothesis` ou `pending`, com até seis
fontes (`path`, `start_line`, `end_line`). A memória acrescenta hashes e guarda
até 120 notas, com limite de 256 KB, em `.codaro/findings.json`, com escrita privada
e atômica. Em execuções efêmeras as notas ficam em memória.

`search_findings` retorna `source_status`:

- `unchanged`: fontes mantêm o hash registrado; a interpretação ainda é do agente.
- `stale`: alguma fonte mudou, foi removida ou deixou de ser permitida.
- `unreferenced`: nota sem fonte, útil como hipótese ou pendência.

Notas relevantes podem ser recuperadas automaticamente na próxima interação,
inclusive após compactação. Pedidos curtos de continuação recuperam notas recentes.
Decisões do usuário continuam no mecanismo separado da conversa. Uma nota nunca
vira decisão, autorização, comprovação de execução ou evidência atual de código.

## Índice incremental e frescor

O índice SQLite é reconstruído automaticamente quando muda a versão do schema.
Metadados de tamanho, inode/dispositivo e tempos de modificação/alteração evitam
reler o conteúdo de arquivos intactos a cada busca. Uma verificação integral é
feita depois de 60 segundos ou com `update(force=True)`. Mudanças nos ignores
são consultadas em toda atualização, inclusive dentro desse intervalo.

As leituras usadas para editar continuam validando bytes/hashes atuais. Reusar
o índice não dispensa a proteção contra conflito e não concede permissões.

## Embeddings locais opcionais

```bash
pip install 'codaro[semantic]'
codaro search "check permission before editing" --repo . --semantic --json
```

No chat, habilite **Busca semântica local** em `/features`. A primeira busca
inicializa FastEmbed e pode baixar pesos; o código é processado localmente e não
é enviado ao provedor de chat para gerar embeddings. O modelo padrão é
`BAAI/bge-small-en-v1.5`, orientado a inglês. `CODARO_EMBEDDING_MODEL` permite escolher
outro modelo suportado pelo FastEmbed, inclusive adequado ao idioma/domínio.

Vetores normalizados ficam no mesmo SQLite, associados ao modelo e ao hash do
conteúdo. A recuperação combina ranking vetorial e lexical. Para limitar o
trabalho por pergunta, até 256 chunks são aquecidos por busca; `partial_index_warming`
indica cobertura parcial. Buscas seguintes continuam o aquecimento no escopo.
Arquivos alterados/removidos não reutilizam vetores antigos.

O ranking vetorial limita cada escopo a 10.000 chunks para conter uso de memória;
`scope_limited` indica que é necessário filtrar um diretório menor. A busca
lexical continua considerando todo o escopo permitido.

Se o extra/modelo estiver indisponível, a busca lexical continua e o diagnóstico
informa a indisponibilidade. Essa modalidade é opt-in: não há download de pesos
ao iniciar normalmente, nem garantia de ganho sem avaliação com o modelo escolhido.

## Avaliação e diagnóstico

`evaluations/cases.json` contém 19 casos: os seis anteriores e 13 cenários
adicionais de recuperação em um projeto poliglota, com perguntas naturais,
filtros e expectativas por símbolo. `codaro evaluate` registra recall de caminhos,
recall de símbolos quando aplicável, posição do primeiro caminho relevante,
estimativa dos tokens retornados, duração e estratégia de busca.

`prompt.json` inclui `project_map` e `retrieval_metrics` para buscas `search_code`:
consulta, filtros, candidatos, duração, quantidade retornada e disponibilidade
da busca semântica. Os resultados das ferramentas continuam no registro do fluxo.

Os testes usam parsers e ripgrep reais, repositórios temporários e um loop de
protocolo com respostas simuladas. Embeddings e servidores LSP são simulados
nos testes determinísticos. Não representam uma avaliação de entendimento de
modelos reais nem substituem testes de build em cada linguagem.

Uma verificação manual adicional carregou o modelo BGE padrão real e confirmou
a geração/cache de quatro vetores em dois arquivos, ranking híbrido e recuperação
do arquivo de autenticação. Ela não substitui um benchmark de embeddings em
projetos grandes ou uma validação do modelo de chat.
