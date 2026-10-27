# Evolução das ferramentas do agente

## Implementado: continuação de leituras parciais

`read_lines` aceita `column` (índice de caractere, começando em zero, na primeira
linha pedida) e `max_chars` entre 200 e 6000. A resposta inclui `next_start_line`,
`next_column` e `file_hash`. Para continuar, envie os dois primeiros como `start`
e `column`, mantenha `end` válido e envie o hash recebido.

O hash é verificado antes de consultar o cache. Se o arquivo mudou, a leitura é
rejeitada e deve ser reiniciada. O limite de contexto pode reduzir a página ainda
mais; nesse caso, o cursor acompanha o trecho efetivamente entregue ao modelo.
O cache não considera uma resposta truncada como uma leitura completa.

Uma continuação a partir de outra coluna não autoriza edição da linha inteira.
A verificação de edições permanece conservadora: o trecho deve estar inteiramente
em linhas completas observadas no arquivo atual. Esta etapa resolve a recuperação
do conteúdo, sem ampliar permissões de escrita.

Testes: `tests/test_read_cursors.py`, incluindo linha Unicode, redução por orçamento,
mudança de arquivo, execução pelo loop nativo e proteção contra edição de texto não lido.

## Implementado: recuperação multilíngue e mapa automático

Busca/listagem com filtros, `grep_code`, `get_project_map`, `get_file_outline`,
`find_definition`, `find_references`, `record_finding` e `search_findings` estão
disponíveis. As ferramentas adicionais são carregadas sob demanda. O mapa é
construído automaticamente durante a interação, com uma projeção pequena no
contexto; detalhes podem ser recuperados por diretório e por páginas.

Os parsers abrangem diversas linguagens; referências LSP dependem de servidor
instalado/habilitado e alternativa textual é identificada. Veja
[Recuperação de código](RETRIEVAL.md) para contratos, níveis de suporte, memória
com hashes, índice incremental e embeddings locais opcionais.

## Próximas etapas ainda pendentes

Ainda não implementadas nesta etapa:

- Estados de atividade específicos para catálogo, diagnósticos e outras ferramentas.
- `get_changes`, `inspect_project` e `inspect_path`.
- Orçamento de contexto detalhado e consulta de tarefa também no modo Perguntar.

As novas ferramentas deverão ser carregadas sob demanda e reutilizar as políticas
de arquivos, revisão, validação e cancelamento existentes.

## Implementado na versão 0.2

- `get_tools_catalog`: busca por descrição/nome e categoria. Seleção antecipada
  respeita o orçamento; o registro completo continua acessível sob demanda.
- `apply_changes`: operações `edit` podem fornecer `replacements` (até 16 pares
  old_text/new_text sem sobreposição, observados no mesmo snapshot). Lotes usam
  `atomic: true` por padrão: preflight e tentativa de rollback com checkpoints,
  sem sobrescrever conflitos externos. Não há atomicidade contra queda do processo.
- `discover_checks`: comandos derivados dos manifestos Python, Node, Go, Rust,
  Java e .NET. `update_plan.criterion_checks` associa critérios aos IDs descobertos.
- `run_check`: executa um comando descoberto após aprovação; `get_validation_report`
  distingue critérios aprovados, pendentes ou indisponíveis na revisão atual.
- `start_command`, `get_command_output`, `cancel_command`: jobs próprios da sessão,
  saída paginada, até quatro ativos e cancelamento. IDs antigos não são restaurados
  como PIDs. `cwd` é relativo à raiz e aparece para o revisor.
- `get_language_support`: inventário paginado de linguagens, parsers e fixtures;
  `detail="full"` acrescenta aliases, membros do filtro e LSP local.

LSP e compiladores não são instalados automaticamente. O teste de uma linguagem
no parser não representa validação de build. Veja [RELEASES.md](RELEASES.md) para
limites de rollback, processos, sandbox e atualização.

## Catálogo de linguagens e aliases

`get_language_support` aceita `language`, `offset` (0–10000), `limit` (1–60,
padrão 20) e `detail` (`summary`, padrão, ou `full`). Continue com `next_offset`
até receber `null`. O cursor acompanha os registros efetivamente entregues,
inclusive quando a página é reduzida pelo orçamento de contexto.

Exemplos de argumentos:

```json
{"limit": 10}
{"offset": 10, "limit": 10}
{"language": "golang", "detail": "full"}
{"language": "ocaml", "limit": 1}
```

No terminal: `codaro languages --language go --full` ou
`codaro languages --offset 20 --limit 20`. Esses argumentos pertencem ao
subcomando de inventário, sem novas opções de inicialização do chat.

`search_code`, `grep_code` e `list_files` compartilham os filtros e aliases abaixo;
maiúsculas/minúsculas e espaços nas extremidades são normalizados. `ocaml`
inclui implementação `.ml` e interface `.mli`; `ocaml_interface` seleciona apenas
interfaces, com a gramática própria preservada. TSX mantém filtro separado.
Dockerfile/Containerfile, Makefile/GNUmakefile e CMakeLists.txt são reconhecidos
pelo nome (sem distinguir maiúsculas). Arquivos `.h` usam a heurística de C/C++
descrita em [RETRIEVAL.md](RETRIEVAL.md).

O filtro `text` seleciona formatos sem gramática reconhecida entre os arquivos
permitidos; não libera acesso a arquivos ignorados ou extensões bloqueadas.
Manifestos preservam seu formato: `package.json` é JSON, `pyproject.toml` é TOML,
e `go.mod` continua texto. O ecossistema é identificado separadamente pelo mapa.

A tabela é gerada de `codaro.languages`; atualize com
`python scripts/update_language_docs.py` e confira com `--check`.

<!-- language-catalog:start -->
| Filtro | Extensões | Nomes de arquivo | Aliases |
| --- | --- | --- | --- |
| `ada` | `.adb`, `.ads` | — | — |
| `apex` | `.cls`, `.trigger` | — | — |
| `asm` | `.asm`, `.s` | — | — |
| `bash` | `.sh`, `.bash`, `.zsh` | — | `shell` |
| `bicep` | `.bicep` | — | — |
| `c` | `.c`, `.h` | — | — |
| `clojure` | `.clj`, `.cljs`, `.cljc`, `.edn` | — | — |
| `cmake` | `.cmake` | `CMakeLists.txt` | — |
| `cobol` | `.cob`, `.cbl` | — | — |
| `commonlisp` | `.lisp`, `.lsp` | — | — |
| `cpp` | `.cc`, `.cpp`, `.cxx`, `.hpp`, `.hh`, `.hxx` | — | `c++` |
| `csharp` | `.cs` | — | `c#`, `c-sharp` |
| `css` | `.css` | — | — |
| `cuda` | `.cu`, `.cuh` | — | — |
| `d` | `.d` | — | — |
| `dart` | `.dart` | — | — |
| `dockerfile` | — | `Dockerfile`, `Containerfile` | — |
| `elisp` | `.el` | — | — |
| `elixir` | `.ex`, `.exs` | — | — |
| `elm` | `.elm` | — | — |
| `erlang` | `.erl`, `.hrl` | — | — |
| `fish` | `.fish` | — | — |
| `fortran` | `.f`, `.f90`, `.f95` | — | — |
| `fsharp` | `.fs`, `.fsi`, `.fsx` | — | — |
| `gdscript` | `.gd` | — | — |
| `gleam` | `.gleam` | — | — |
| `glsl` | `.glsl`, `.vert`, `.frag` | — | — |
| `go` | `.go` | — | `golang` |
| `graphql` | `.graphql`, `.gql` | — | — |
| `groovy` | `.groovy`, `.gradle` | — | — |
| `haskell` | `.hs` | — | — |
| `haxe` | `.hx` | — | — |
| `hcl` | `.tf`, `.hcl` | — | — |
| `hlsl` | `.hlsl` | — | — |
| `html` | `.html`, `.htm` | — | — |
| `ini` | `.ini`, `.cfg` | — | — |
| `java` | `.java` | — | — |
| `javascript` | `.js`, `.jsx`, `.mjs`, `.cjs` | — | `js`, `node`, `nodejs` |
| `json` | `.json` | — | — |
| `julia` | `.jl` | — | — |
| `kotlin` | `.kt`, `.kts` | — | — |
| `lua` | `.lua` | — | — |
| `make` | — | `Makefile`, `GNUmakefile` | — |
| `markdown` | `.md`, `.mdx` | — | — |
| `matlab` | `.matlab` | — | — |
| `nim` | `.nim` | — | — |
| `nix` | `.nix` | — | — |
| `objc` | `.m`, `.mm` | — | `objective-c` |
| `ocaml` | `.ml`, `.mli` | — | — |
| `ocaml_interface` | `.mli` | — | — |
| `pascal` | `.pas`, `.pp` | — | — |
| `perl` | `.pl`, `.pm` | — | — |
| `php` | `.php` | — | — |
| `powershell` | `.ps1`, `.psm1` | — | — |
| `prisma` | `.prisma` | — | — |
| `proto` | `.proto` | — | `protobuf` |
| `python` | `.py`, `.pyi` | — | `py` |
| `r` | `.r` | — | — |
| `racket` | `.rkt` | — | — |
| `rst` | `.rst` | — | — |
| `ruby` | `.rb`, `.rake` | — | — |
| `rust` | `.rs` | — | `rs` |
| `scala` | `.scala`, `.sc` | — | — |
| `scheme` | `.scm` | — | — |
| `scss` | `.scss` | — | — |
| `solidity` | `.sol` | — | — |
| `sql` | `.sql` | — | — |
| `svelte` | `.svelte` | — | — |
| `swift` | `.swift` | — | — |
| `tcl` | `.tcl` | — | — |
| `text` | — | — | — |
| `toml` | `.toml` | — | — |
| `tsx` | `.tsx` | — | — |
| `typescript` | `.ts`, `.mts`, `.cts` | — | `ts` |
| `verilog` | `.v`, `.sv`, `.svh` | — | — |
| `vhdl` | `.vhd`, `.vhdl` | — | — |
| `vue` | `.vue` | — | — |
| `wgsl` | `.wgsl` | — | — |
| `xml` | `.xml`, `.csproj`, `.fsproj`, `.vbproj`, `.props`, `.targets` | — | — |
| `yaml` | `.yaml`, `.yml` | — | — |
| `zig` | `.zig` | — | — |
<!-- language-catalog:end -->

O inventário distingue `fixture` (exemplos estruturais exercitados),
`parser_only` (parser disponível) e `text_only`. A paridade de filtros é testada
para todo o catálogo e aliases; isso não comprova todos os dialetos ou builds.
