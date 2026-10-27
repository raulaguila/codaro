package ui

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

type metadataProvider struct {
	Model llm.Model
	Err   error
}

func (p metadataProvider) Catalog(context.Context) ([]llm.Model, error) {
	return []llm.Model{p.Model}, p.Err
}
func (p metadataProvider) Describe(context.Context, string) (llm.Model, error) { return p.Model, p.Err }
func (p metadataProvider) Chat(context.Context, []llm.Message, []llm.Tool, func(llm.Delta)) (llm.Response, error) {
	return llm.Response{Content: "Resposta pronta."}, nil
}

func click(m Model, x, y int) (Model, tea.Cmd) {
	next, cmd := m.Update(tea.MouseMsg{X: x, Y: y, Action: tea.MouseActionPress, Button: tea.MouseButtonLeft})
	return next.(Model), cmd
}

func renderedPoint(t *testing.T, m Model, label string) (int, int) {
	t.Helper()
	for y, line := range strings.Split(ansi.Strip(m.View()), "\n") {
		if index := strings.Index(line, label); index >= 0 {
			return ansi.StringWidth(line[:index]), y
		}
	}
	t.Fatalf("label not rendered: %s", label)
	return 0, 0
}

func TestHeaderClickOpensModelAndModeAndKeepsDraft(t *testing.T) {
	for _, width := range []int{60, 80, 120} {
		m := fixture(t)
		m = update(m, tea.WindowSizeMsg{Width: width, Height: 30})
		m.prompt.SetValue("rascunho")
		m.models = []llm.Model{{ID: "demo-local"}}
		m.catalogSettings = m.Settings
		layout := m.headerLayout()
		m, _ = click(m, layout.Model.Min.X+1, 0)
		if m.modal != "models" || m.loading || m.prompt.Value() != "rascunho" {
			t.Fatal("model click failed", width)
		}
		m = press(m, tea.KeyEsc)
		layout = m.headerLayout()
		m, _ = click(m, layout.Mode.Min.X+1, 0)
		if m.modal != "modes" || m.prompt.Value() != "rascunho" {
			t.Fatal("mode click failed", width)
		}
	}
}

func TestHeaderLayoutHandlesUnicodeAndDoesNotGrowOnHover(t *testing.T) {
	m := fixture(t)
	m.Demo = false
	m.Settings.Model = strings.Repeat("模型", 30)
	m.Width = 80
	before := m.headerLayout()
	if ansi.StringWidth(ansi.Strip(before.Text)) > 80 {
		t.Fatal("header overflow")
	}
	m.hover = "model"
	after := m.headerLayout()
	if before.Model != after.Model || before.Mode != after.Mode || lipgloss.Width(before.Text) != lipgloss.Width(after.Text) {
		t.Fatal("hover moved selectors")
	}
}

func TestMouseModelSelectionUsesFreshAPIContextAndPreservesHistory(t *testing.T) {
	m := fixture(t)
	m.models = []llm.Model{{ID: "new-model"}}
	m.catalogSettings = m.Settings
	m.history = []llm.Message{{Role: "user", Content: "Decisão anterior"}, {Role: "assistant", Content: "Registrado"}}
	m.providerFactory = func(config.Settings) llm.Provider {
		return metadataProvider{Model: llm.Model{ID: "new-model", Context: 131072, Source: "API: context_length"}}
	}
	layout := m.headerLayout()
	m, _ = click(m, layout.Model.Min.X+1, 0)
	x, y := renderedPoint(t, m, "new-model")
	var cmd tea.Cmd
	m, cmd = click(m, x, y)
	if cmd == nil {
		t.Fatal("model row did not start metadata lookup")
	}
	m = update(m, cmd())
	if m.Settings.Model != "new-model" || m.Settings.ContextWindow != 131072 || len(m.history) != 2 || m.modal != "" {
		t.Fatal(m.Settings, len(m.history), m.modal)
	}
	if m.models[0].Context != 131072 {
		t.Fatal("model picker cache missing API context")
	}
}

func TestWrappedModeRowsCanBeClickedAndExecutionRemainsDisabled(t *testing.T) {
	m := fixture(t)
	m = update(m, tea.WindowSizeMsg{Width: 60, Height: 24})
	m.prompt.SetValue("manter")
	m.openModes()
	x, y := renderedPoint(t, m, "Planejar")
	m, _ = click(m, x, y)
	if m.Settings.Mode != "plan" || m.modal != "" || m.prompt.Value() != "manter" {
		t.Fatal("plan click failed")
	}
	m.openModes()
	m.modeIndex = 2
	m = press(m, tea.KeyEnter)
	if m.Settings.Mode != "plan" || m.modal != "modes" || m.modalError == "" {
		t.Fatal("execution incorrectly activated")
	}
	m = press(m, tea.KeyEsc)
	m = update(m, tea.KeyMsg{Type: tea.KeyShiftTab})
	if m.Settings.Mode != "ask" {
		t.Fatal("mode shortcut failed")
	}
}

func TestBusySelectorsDoNotChangeModelOrMode(t *testing.T) {
	m := fixture(t)
	m.busy = true
	layout := m.headerLayout()
	m, _ = click(m, layout.Mode.Min.X, 0)
	m, _ = click(m, layout.Model.Min.X, 0)
	if m.modal != "" || m.loading {
		t.Fatal("busy selector changed session")
	}
}

func TestStartupRefreshMigratesOldEightKLimitAndPreservesDraft(t *testing.T) {
	base := fixture(t)
	s := config.Default()
	s.Model = "llama"
	s.ModelContext = 131072
	s.ContextWindow = 8192
	s.ContextSource = "API: context_length"
	m := New(base.Repo, s, t.TempDir(), false)
	defer m.Shutdown()
	m.prompt.SetValue("rascunho")
	m.providerFactory = func(config.Settings) llm.Provider {
		return metadataProvider{Model: llm.Model{ID: "llama", Context: 131072, Source: "API: model_info.context_length"}}
	}
	if !m.metadataLoading {
		t.Fatal("startup did not request metadata")
	}
	m = update(m, m.metadataCmd()())
	if m.metadataLoading || m.Settings.ContextWindow != 131072 || m.prompt.Value() != "rascunho" {
		t.Fatal(m.Settings)
	}
}

func TestMissingMetadataUsesLabeledFallbackAndLateResponseIsIgnored(t *testing.T) {
	m := fixture(t)
	m.Demo = false
	m.Settings.Provider = "ollama"
	m.Settings.Model = "test"
	m.metadataID = 1
	m.metadataLoading = true
	original := m.Settings
	m = update(m, metadataResult{ID: 1, Settings: original, Err: errors.New("offline")})
	if m.Settings.ContextWindow != 8192 || !strings.Contains(m.Settings.ContextSource, "fallback") || m.metadataLoading {
		t.Fatal(m.Settings)
	}
	m.Settings.Model = "new"
	m = update(m, metadataResult{ID: 1, Settings: original, Model: llm.Model{ID: "test", Context: 131072}})
	if m.Settings.Model != "new" || m.Settings.ContextWindow != 8192 {
		t.Fatal("stale metadata replaced model")
	}
}

func TestQuestionWaitsForMetadataBeforeUsingTheProvider(t *testing.T) {
	m := fixture(t)
	m.Demo = false
	m.Settings.Provider = "ollama"
	m.Settings.Model = "test"
	m.metadataID = 1
	m.metadataLoading = true
	m.providerFactory = func(s config.Settings) llm.Provider {
		if s.ContextWindow != 131072 {
			t.Error("generation used stale context")
		}
		return metadataProvider{}
	}
	m.prompt.SetValue("Olá")
	m.start("Olá")
	if m.busy || m.prompt.Value() != "Olá" {
		t.Fatal("question sent before metadata")
	}
	m = update(m, metadataResult{ID: 1, Settings: m.Settings, Model: llm.Model{ID: "test", Context: 131072, Source: "API"}})
	deadline := time.Now().Add(time.Second)
	for m.busy && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		m = update(m, tick(time.Now()))
	}
	m.Shutdown()
	if m.busy || m.current() == nil || m.current().Final != "Resposta pronta." {
		t.Fatal("queued question not completed")
	}
}
