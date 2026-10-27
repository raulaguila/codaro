package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
	"github.com/raulaguila/codaro/experimental/go/internal/agent"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
	"github.com/raulaguila/codaro/experimental/go/internal/tools"
)

func fixture(t *testing.T) Model {
	t.Helper()
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "main.go"), []byte("package main\nfunc main() {}\n"), 0600)
	r, err := tools.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.Close() })
	s := config.Default()
	s.Provider = "demo"
	s.Model = "demo-local"
	return New(r, s, t.TempDir(), true)
}
func update(m Model, msg tea.Msg) Model    { next, _ := m.Update(msg); return next.(Model) }
func press(m Model, key tea.KeyType) Model { return update(m, tea.KeyMsg{Type: key}) }

func TestCommandsUseArrowsAndEnterAndPreserveDraftOnEscape(t *testing.T) {
	m := fixture(t)
	m.prompt.SetValue("rascunho")
	m = press(m, tea.KeyCtrlP)
	if m.prompt.Value() != "/" {
		t.Fatal("palette not open")
	}
	m = press(m, tea.KeyEsc)
	if m.prompt.Value() != "rascunho" {
		t.Fatal("draft lost")
	}
	m.prompt.SetValue("/p")
	m = press(m, tea.KeyDown)
	m = press(m, tea.KeyEnter)
	if !m.expanded || m.busy || m.prompt.Value() != "" {
		t.Fatal("command not selected")
	}
}

func TestDemoStreamsToolsThenKeepsFinalOutsideCollapsedProcess(t *testing.T) {
	m := fixture(t)
	m.start("Analise este diretório")
	deadline := time.Now().Add(5 * time.Second)
	for m.busy && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
		m = update(m, tick(time.Now()))
	}
	m.Shutdown()
	if m.busy {
		t.Fatal("demo did not finish")
	}
	if m.expanded {
		t.Fatal("completed process not collapsed")
	}
	current := m.current()
	if current == nil || current.Final == "" || len(current.Steps) < 3 {
		t.Fatal("missing process/final")
	}
	m.refresh()
	view := ansi.Strip(m.View())
	if !strings.Contains(view, "Ver processo") || !strings.Contains(view, "main.go") {
		t.Fatal(view)
	}
	m = press(m, tea.KeyCtrlT)
	m.refresh()
	expanded := ansi.Strip(m.viewport.View())
	if !strings.Contains(expanded, "Raciocínio do modelo") {
		t.Fatal("process unavailable")
	}
}

func TestCancellationPreservesDraftAndDoesNotCommitPartialTurn(t *testing.T) {
	m := fixture(t)
	m.start("Explique")
	m.prompt.SetValue("rascunho novo")
	m = press(m, tea.KeyCtrlX)
	deadline := time.Now().Add(3 * time.Second)
	for m.busy && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		m = update(m, tick(time.Now()))
	}
	m.Shutdown()
	if m.busy || m.prompt.Value() != "rascunho novo" || len(m.history) != 0 {
		t.Fatal("cancel lost state")
	}
}

func TestProviderEscapeDoesNotLeaveDemoOrSaveCredentials(t *testing.T) {
	m := fixture(t)
	m.executeCommand("providers")
	m.fields[1].SetValue("secret")
	m = press(m, tea.KeyEsc)
	if !m.Demo || m.Settings.Provider != "demo" {
		t.Fatal("cancel changed active provider")
	}
	if _, err := os.Stat(filepath.Join(m.Directory, "config.json")); !os.IsNotExist(err) {
		t.Fatal("cancel wrote configuration")
	}
	m = update(m, selectedResult{ID: m.operationID, Settings: config.Default(), Model: llm.Model{ID: "llama", Context: 131072, Source: "API"}})
	if m.Demo || m.Settings.Model != "llama" || m.Settings.ContextWindow != 8192 {
		t.Fatal("selection not applied")
	}
}

func TestStaleCatalogResultCannotReopenCancelledDialog(t *testing.T) {
	m := fixture(t)
	m.openProvider()
	id := m.operationID
	m = press(m, tea.KeyEsc)
	m = update(m, catalogResult{ID: id, Models: []llm.Model{{ID: "late"}}})
	if m.modal != "" || len(m.models) != 0 {
		t.Fatal("late result reopened dialog")
	}
}

func TestViewsFitTerminal(t *testing.T) {
	for _, size := range [][2]int{{60, 24}, {80, 24}, {120, 35}} {
		for _, screen := range []string{"chat", "provider", "models"} {
			t.Run(screen+string(rune(size[0])), func(t *testing.T) {
				m := fixture(t)
				m = update(m, tea.WindowSizeMsg{Width: size[0], Height: size[1]})
				if screen == "provider" {
					m.openProvider()
				} else if screen == "models" {
					m.modal = "models"
					m.models = []llm.Model{{ID: "qwen3:27b"}, {ID: "llama3.1:8b"}}
				}
				m.refresh()
				view := ansi.Strip(m.View())
				lines := strings.Split(view, "\n")
				if len(lines) > size[1] {
					t.Fatalf("%s has %d lines for %d rows\n%s", screen, len(lines), size[1], view)
				}
				for _, line := range lines {
					if ansi.StringWidth(line) > size[0] {
						t.Fatalf("line exceeds terminal width: %q", line)
					}
				}
			})
		}
	}
}

func TestReasoningAndIntermediateTextArePreservedWhenToolsStart(t *testing.T) {
	m := fixture(t)
	m.turns = []*turn{{Question: "Teste", Started: time.Now()}}
	m.handleEvent(agent.Event{Kind: "reasoning", Text: "Vou consultar o projeto."})
	m.handleEvent(agent.Event{Kind: "delta", Text: "Vou listar arquivos."})
	m.handleEvent(agent.Event{Kind: "intermediate"})
	m.handleEvent(agent.Event{Kind: "tool_start", Text: "Listando arquivos"})
	if len(m.current().Steps) != 3 || m.current().Steps[1].Body != "Vou listar arquivos." {
		t.Fatal("intermediate text disappeared")
	}
}

func TestCompletionDrainsQueuedFinalBeforeCollapsing(t *testing.T) {
	m := fixture(t)
	m.runID = 1
	m.busy = true
	m.turns = []*turn{{Question: "Explique", Started: time.Now()}}
	for i := 0; i < 600; i++ {
		m.events <- envelope{1, agent.Event{Kind: "delta", Text: "x"}}
	}
	m.events <- envelope{1, agent.Event{Kind: "final", Text: "Resposta final concluída."}}
	m.results <- done{ID: 1}
	m = update(m, tick(time.Now()))
	if m.busy || m.current().Final != "Resposta final concluída." || m.current().Draft.Len() != 0 {
		t.Fatal("final lost behind completion")
	}
}

func TestRunningToolsShowReadableSummaryInsteadOfRawJSON(t *testing.T) {
	m := fixture(t)
	m.turns = []*turn{{Question: "Leia", Started: time.Now()}}
	m.expanded = true
	m.handleEvent(agent.Event{Kind: "tool_start", Text: "Lendo arquivo"})
	m.handleEvent(agent.Event{Kind: "tool_done", Name: "read_lines", Text: "Lendo arquivo", Detail: `{"path":"main.go","start_line":1,"end_line":2,"content":"package main"}`})
	m.refresh()
	view := ansi.Strip(m.viewport.View())
	if !strings.Contains(view, "main.go · linhas 1–2") || strings.Contains(view, `"content"`) {
		t.Fatal(view)
	}
}
