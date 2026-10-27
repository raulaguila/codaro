# Validação inicial — 27/10/2026

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
