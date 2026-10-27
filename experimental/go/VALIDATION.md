# Validação — 27/10/2026

## Evolução da experiência no terminal

- **99 testes aprovados** com `go test -race ./...` após a integração da UI.
- Cobertura do protocolo nativo de perguntas e atualização do plano, cancelamento
  durante a espera, referências reais de arquivos e preservação dos pares tool/result.
- Testes de UI para pergunta com opção ou texto livre, rascunhos, plano entre turnos,
  fila ordenada, sessões sem reexecução, chave via ambiente, favoritos por provedor,
  cache, respostas atrasadas de metadados, hover e geometria real de cliques.
- Telas verificadas em 60×24, 80×24 e 120×35, incluindo perguntas longas, plano,
  ajuda, catálogo, histórico, sessões e diff. Input cresce de uma a três linhas.
- PTY Linux real com Ollama HTTP **simulado localmente**: contexto 131072 aplicado,
  cinco requisições de chat, quatro ferramentas (plano/pergunta/leitura/plano),
  resposta selecionada com teclado e retomada da conversa sem novas requisições.
- O teste reproduzível está em `scripts/smoke_terminal.py`. Não faz inferência real.
- Ensaio adicional de demonstração em PTY 120×35: cliques nos seletores, troca de
  modo, ferramentas reais, processo recolhível e saída limpa com Ctrl+Q.
- `go vet`, `go mod verify`, `gofmt` e builds Linux amd64, Windows amd64 e macOS
  arm64 aprovados. Cross-build não comprova execução em Windows/macOS.
- 14 capturas do renderer Bubble Tea com projeto e catálogos sintéticos, incluindo
  plano, pergunta, sessões, histórico, revisão de diff e fila.

Não houve qualificação com um servidor real de LLM nesta rodada. Testes de
protocolo e simulações não comprovam a qualidade das respostas de um modelo.

## Contexto automático e seletores

- **75 testes aprovados** com detector de concorrência após os ajustes.
- Testes de protocolo comprovam que contexto 131072 retornado pela API é
  aplicado ao orçamento e enviado ao Ollama em `options.num_ctx`.
- Cobertura de `parameters.num_ctx`, arquitetura principal de modelos com visão,
  campos de APIs compatíveis, limites do servidor, override explícito e fallback.
- Testes de UI verificam atualização ao iniciar, migração do antigo teto de 8k,
  respostas atrasadas, cache, preservação da conversa e envio após resolver metadados.
- Cliques são testados a partir das posições do texto renderizado, incluindo
  descrições de modos quebradas em linhas, Unicode, hover e resize.
- O ensaio PTY real também clicou no header para abrir modelos e modos, selecionou
  Planejar e verificou `mode=plan` no diagnóstico da interação concluída.
- Perguntar e Planejar têm instruções distintas e as mesmas ferramentas de leitura.
  Executar continua indisponível; isso é indicado no seletor e validado pelo backend.

Os testes HTTP usam transporte simulado. Não houve inferência paga ou qualificação
com um servidor real de LLM nesta rodada.

## Primeira versão

Protótipo de consulta, independente da aplicação Python.

- **37 testes aprovados** com `go test -race ./...`, sem disputas detectadas.
- `go vet ./...`, `go mod verify` e formatação `gofmt` aprovados.
- Build nativo Linux amd64 e cross-builds Windows amd64 e macOS arm64 aprovados,
  com Go 1.25.0. Cross-build não comprova execução nos outros sistemas.
- Pergunta única `--demo --prompt` executou ferramentas reais sobre o próprio
  repositório e retornou uma prévia do README, com diagnóstico concluído.
- Teste em PTY real 120×35 verificou início, Ctrl+P, Esc, pergunta, duas ferramentas,
  expansão do processo com Ctrl+T, gravação do fluxo e saída zero por Ctrl+Q.
- Seis capturas do renderer em 100×32: início, comandos, provedor, modelos,
  processo e resposta final. Projeto e catálogo dessas capturas são sintéticos;
  a interface é a implementação Bubble Tea, sem geração de imagens por IA.

O relatório de terminal e as capturas desta rodada ficam localmente em
`artifacts/go-prototype/`, na raiz do repositório, fora do versionamento.
Tempos dessa demonstração não incluem inferência nem constituem uma comparação
controlada com a aplicação Python.

Não houve chamadas a provedores pagos ou qualificação com um modelo real nesta
rodada. Protocolos foram exercitados com transporte HTTP simulado e ferramentas
locais reais. O próximo teste é conectar o servidor/modelo do usuário pela UI.

## Reproduzir capturas ANSI

No diretório `experimental/go`, em um shell POSIX:

```bash
CODARO_GO_CAPTURE_DIR="$PWD/../../artifacts/go-prototype" \
  go test -tags capture ./internal/ui -run TestCaptureViews -count=1
```

O exporter está em `internal/ui/capture_test.go`, isolado pela tag `capture`.
