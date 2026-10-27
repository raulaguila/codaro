//go:build capture

package ui

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
	"github.com/raulaguila/codaro/experimental/go/internal/agent"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

// TestCaptureViews exports the actual renderer with a synthetic, credential-free project.
func TestCaptureViews(t *testing.T) {
	dir := os.Getenv("CODARO_GO_CAPTURE_DIR")
	if dir == "" {
		t.Skip("set CODARO_GO_CAPTURE_DIR")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	profile := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	defer lipgloss.SetColorProfile(profile)
	m := fixture(t)
	m = update(m, tea.WindowSizeMsg{Width: 100, Height: 32})
	capture := func(name string) {
		t.Helper()
		m.refresh()
		if err := os.WriteFile(filepath.Join(dir, name+".ansi"), []byte(m.View()), 0600); err != nil {
			t.Fatal(err)
		}
	}
	capture("01-inicio")
	m.prompt.SetValue("/")
	capture("02-comandos")
	m.prompt.Reset()
	m.openProvider()
	capture("03-provedor")
	m.modal = ""
	m.modal = "models"
	m.models = []llm.Model{{ID: "qwen3:27b", Context: 131072}, {ID: "llama3.1:8b", Context: 131072}, {ID: "qwen2.5-coder:7b", Context: 32768}}
	m.catalogSettings = m.Settings
	m.models[0].Capabilities = []string{"tools"}
	other := config.Default()
	other.Provider = "openai"
	other.BaseURL = "https://example.invalid/v1"
	m.catalogGroups = []catalogGroup{{Settings: m.Settings, Models: m.models}, {Settings: other, Models: []llm.Model{{ID: "modelo-cloud", Context: 131072, Capabilities: []string{"tools"}, Source: "API simulada"}}}}
	m.preferences.Favorites = []string{modelID(m.Settings, "qwen3:27b")}
	capture("04-modelos")
	m.modal = ""
	m.start("Explique a estrutura deste projeto.")
	deadline := time.Now().Add(5 * time.Second)
	captured := false
	for m.busy && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
		m = update(m, tick(time.Now()))
		if !captured && m.current().Draft.Len() > 40 {
			capture("05-processo")
			captured = true
		}
	}
	m.Shutdown()
	if m.busy {
		t.Fatal("demo did not complete")
	}
	capture("06-resposta")
	m.openModes()
	capture("07-modos")
	m.modal = ""
	m.Settings.ModelContext = 131072
	m.Settings.ContextWindow = 131072
	m.Settings.ContextSource = "API: model_info.context_length (fixture)"
	m.modal = "status"
	capture("08-contexto")
	m.modal = ""
	m.plan = []agent.Task{{Title: "Entender os pontos de entrada", Status: "completed"}, {Title: "Revisar o fluxo do agente", Status: "in_progress"}, {Title: "Preparar recomendações", Status: "pending"}}
	m.planExpanded = true
	capture("09-plano")
	m.question = &agent.Question{Text: "Qual área você quer priorizar nesta revisão?", Options: []string{"Recuperação de contexto", "Interface e experiência", "Confiabilidade dos provedores"}}
	m.prompt.Placeholder = "Ou escreva sua própria resposta…"
	m.status = "Aguardando sua resposta"
	capture("10-pergunta")
	m.question = nil
	m.plan = nil
	m.modal = "sessions"
	m.sessions = []session{{ID: m.sessionID, Updated: time.Now(), Turns: []storedTurn{{Question: "Revisão da arquitetura"}}}}
	capture("11-sessoes")
	m.modal = "history"
	m.historySearch.Placeholder = "Buscar mensagens…"
	m.historySearch.SetValue("projeto")
	capture("12-historico")
	m.modal = "diff"
	m.diffView.Width = 68
	m.diffView.Height = 12
	m.diffView.SetContent("diff --git a/main.go b/main.go\n--- a/main.go\n+++ b/main.go\n@@ -1 +1 @@\n-package main\n+package exemplo\n\nDiff de demonstração, sem alterações neste teste.")
	capture("13-diff")
	m.modal = ""
	m.queued = []string{"Depois, investigue a configuração dos provedores."}
	m.status = "Mensagem adicionada à fila"
	capture("14-fila")
}
