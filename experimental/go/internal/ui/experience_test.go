package ui

import (
	"context"
	"encoding/json"
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
)

func TestQuestionUsesComposerOptionsAndCustomTextWithoutLosingDraft(t *testing.T) {
	for _, custom := range []bool{false, true} {
		m := fixture(t)
		m.turns = []*turn{{Question: "Investigue", Started: time.Now()}}
		m.prompt.SetValue("rascunho guardado")
		reply := make(chan string, 1)
		m.handleEvent(agent.Event{Kind: "question", Question: &agent.Question{Text: "Qual módulo?", Options: []string{"API", "CLI"}}, Reply: reply})
		m.refresh()
		if !strings.Contains(ansi.Strip(m.composerView()), "Qual módulo?") {
			t.Fatal("question outside composer")
		}
		m = press(m, tea.KeyDown)
		if custom {
			m.prompt.SetValue("Outro módulo")
		}
		m = press(m, tea.KeyEnter)
		want := "CLI"
		if custom {
			want = "Outro módulo"
		}
		select {
		case got := <-reply:
			if got != want {
				t.Fatal(got)
			}
		default:
			t.Fatal("no answer")
		}
		if m.question != nil || m.prompt.Value() != "rascunho guardado" {
			t.Fatal("draft lost")
		}
	}
}
func TestPlanStaysVisibleAndCollapseChoiceSurvivesUpdatesAndTurns(t *testing.T) {
	m := fixture(t)
	m.turns = []*turn{{Question: "Investigue", Started: time.Now()}}
	tasks := []agent.Task{{Title: "Ler código", Status: "in_progress"}, {Title: "Propor melhorias", Status: "pending"}}
	m.handleEvent(agent.Event{Kind: "plan", Tasks: tasks})
	m = press(m, tea.KeyCtrlB)
	m.handleEvent(agent.Event{Kind: "plan", Tasks: tasks})
	if m.planExpanded || !strings.Contains(m.planView(), "Plano · 0/2") {
		t.Fatal("plan collapse not retained")
	}
	m.start("Continue")
	if len(m.plan) != 2 {
		t.Fatal("new turn cleared plan")
	}
	m.Shutdown()
}
func TestComposerAndAllDialogsFitSmallTerminals(t *testing.T) {
	for _, width := range []int{60, 80, 120} {
		for _, screen := range []string{"question", "sessions", "history", "diff", "permissions", "models"} {
			m := fixture(t)
			m = update(m, tea.WindowSizeMsg{Width: width, Height: 24})
			if screen == "question" {
				m.turns = []*turn{{Question: "Teste", Started: time.Now()}}
				m.handleEvent(agent.Event{Kind: "question", Question: &agent.Question{Text: strings.Repeat("Uma pergunta longa. ", 24), Options: []string{strings.Repeat("Opção longa ", 12), "B", "C", "D"}}, Reply: make(chan string, 1)})
				m.handleEvent(agent.Event{Kind: "plan", Tasks: []agent.Task{{Title: "A", Status: "pending"}, {Title: "B", Status: "pending"}, {Title: "C", Status: "pending"}, {Title: "D", Status: "pending"}}})
			} else {
				m.modal = screen
				m.diffView.Width = max(16, min(76, width-8)-6)
				m.diffView.Height = 10
				m.diffView.SetContent("diff de teste")
				m.models = []llm.Model{{ID: "模型-" + strings.Repeat("x", 100), Context: 131072}}
				m.catalogSettings = m.Settings
			}
			m.refresh()
			view := ansi.Strip(m.View())
			if len(strings.Split(view, "\n")) > 24 {
				t.Fatalf("%s %d too tall\n%s", screen, width, view)
			}
			for _, line := range strings.Split(view, "\n") {
				if ansi.StringWidth(line) > width {
					t.Fatalf("%s %d overflow %q", screen, width, line)
				}
			}
		}
	}
}
func TestReadingPositionAndCollapsedProcessDoNotChangeWhenToolsArrive(t *testing.T) {
	m := fixture(t)
	m.turns = []*turn{{Question: strings.Repeat("Contexto\n", 40), Started: time.Now()}}
	m.expanded = false
	m.manualExpanded = true
	m.follow = false
	m.refresh()
	m.viewport.SetYOffset(3)
	m.handleEvent(agent.Event{Kind: "tool_start", Text: "Lendo arquivo"})
	m.refresh()
	if m.expanded || m.viewport.YOffset != 3 {
		t.Fatal("read position changed")
	}
	if !strings.Contains(ansi.Strip(m.footer()), "Novas atividades") {
		t.Fatal("missing follow affordance")
	}
}
func TestFileAutocompleteFiltersAndKeepsQuestion(t *testing.T) {
	m := fixture(t)
	m.files = []string{"src/main.go", "src/model.go", "README.md"}
	m.filesLoaded = true
	m.prompt.SetValue("Explique @smdg")
	m = press(m, tea.KeyTab)
	if m.prompt.Value() != "Explique @src/model.go " || m.busy {
		t.Fatal(m.prompt.Value())
	}
}
func TestQueuedMessagesRunInOrderAndPreserveDraft(t *testing.T) {
	m := fixture(t)
	m.providerFactory = func(config.Settings) llm.Provider { return metadataProvider{} }
	m.start("Primeira")
	m.prompt.SetValue("Segunda")
	m = press(m, tea.KeyEnter)
	m.prompt.SetValue("Rascunho")
	deadline := time.Now().Add(3 * time.Second)
	for (m.busy || len(m.queued) > 0) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		m = update(m, tick(time.Now()))
	}
	m.Shutdown()
	if m.busy || len(m.turns) != 2 || m.turns[1].Question != "Segunda" || m.prompt.Value() != "Rascunho" {
		t.Fatal("queue lost state")
	}
}
func TestSessionRoundTripPreservesPlanAndRedactsKeys(t *testing.T) {
	m := fixture(t)
	m.Settings.APIKey = "secret-api-key"
	m.turns = []*turn{{Question: "Investigue secret-api-key", Final: "Concluído", Done: true}}
	m.history = []llm.Message{{Role: "user", Content: "Olá"}, {Role: "assistant", Content: "Oi"}}
	m.plan = []agent.Task{{Title: "Revisar", Status: "pending"}}
	m.prompt.SetValue("Rascunho")
	if result := m.saveSession()().(storageResult); result.Err != nil {
		t.Fatal(result.Err)
	}
	raw, err := os.ReadFile(filepath.Join(m.sessionDirectory(), m.sessionID+".json"))
	if err != nil || strings.Contains(string(raw), "secret-api-key") {
		t.Fatal("unsafe session", err)
	}
	result := m.openSessions()().(sessionsResult)
	m = update(m, result)
	m.resumeSession(0)
	if len(m.turns) != 1 || len(m.plan) != 1 || m.prompt.Value() != "Rascunho" || m.busy {
		t.Fatal("resume lost state or executed actions")
	}
}
func TestModelGroupsDisambiguateSameIDAndPersistFavoritesWithoutKeys(t *testing.T) {
	m := fixture(t)
	a := config.Default()
	a.Model = "shared"
	a.APIKey = "secret"
	b := a
	b.BaseURL = "https://example.com/v1"
	b.Provider = "openai"
	b.APIKey = "other-secret"
	m.catalogGroups = []catalogGroup{{Settings: a, Models: []llm.Model{{ID: "shared"}}}, {Settings: b, Models: []llm.Model{{ID: "shared"}}}}
	entries := m.entries()
	if len(entries) != 2 || modelID(entries[0].Settings, "shared") == modelID(entries[1].Settings, "shared") {
		t.Fatal("provider identities collide")
	}
	m.preferences.Favorites = []string{modelID(b, "shared")}
	m.favoritesOnly = true
	if got := m.entries(); len(got) != 1 || got[0].Settings.BaseURL != b.BaseURL {
		t.Fatal(got)
	}
	m.rememberProvider(a)
	m.rememberProvider(b)
	if result := m.savePreferences()().(storageResult); result.Err != nil {
		t.Fatal(result.Err)
	}
	raw, _ := os.ReadFile(filepath.Join(m.Directory, "providers.json"))
	if strings.Contains(string(raw), "secret") {
		t.Fatal("key persisted")
	}
	var profiles []providerProfile
	if json.Unmarshal(raw, &profiles) != nil || len(profiles) != 2 {
		t.Fatal("profiles lost")
	}
}
func TestProviderKeysResolveFromEnvironmentOnly(t *testing.T) {
	m := fixture(t)
	t.Setenv("CODARO_TEST_PROVIDER_KEY", "secret")
	profiles := []providerProfile{{Name: "Local", Settings: config.Default(), KeyEnv: "CODARO_TEST_PROVIDER_KEY"}}
	raw, _ := json.Marshal(profiles)
	os.WriteFile(filepath.Join(m.Directory, "providers.json"), raw, 0600)
	m.loadPreferences()
	if len(m.profiles) != 1 || m.profiles[0].Settings.APIKey != "secret" {
		t.Fatal("env key not resolved")
	}
}
func TestProcessAndPlanCanBeClickedAtTheirRenderedLocation(t *testing.T) {
	m := fixture(t)
	m = update(m, tea.WindowSizeMsg{Width: 100, Height: 32})
	m.turns = []*turn{{Question: "Teste", Final: "Pronto", Done: true, Steps: []step{{Kind: "phase", Title: "Investigando"}}}}
	m.plan = []agent.Task{{Title: "Continuar", Status: "pending"}}
	m.refresh()
	x, y := renderedPoint(t, m, "Ver processo")
	m, _ = click(m, x, y)
	if !m.expanded {
		t.Fatal("process click failed")
	}
	m.refresh()
	x, y = renderedPoint(t, m, "Plano ·")
	m, _ = click(m, x, y)
	if !m.planExpanded {
		t.Fatal("plan click failed", y)
	}
}
func TestCancelledQueueDoesNotStartAnotherTurn(t *testing.T) {
	m := fixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	m.workers.Cancel = cancel
	m.busy = true
	m.turns = []*turn{{Question: "Primeira"}}
	m.queued = []string{"Segunda"}
	m = press(m, tea.KeyCtrlX)
	if ctx.Err() == nil || len(m.queued) != 0 {
		t.Fatal("cancel left queued work")
	}
	m.results <- done{ID: m.runID, Err: context.Canceled}
	m = update(m, tick(time.Now()))
	if len(m.turns) != 1 {
		t.Fatal("queue ran after cancellation")
	}
}

func TestPaletteSelectionPreservesDraftAndMetadataPreviewIgnoresStaleResults(t *testing.T) {
	m := fixture(t)
	m.prompt.SetValue("Rascunho importante")
	m = press(m, tea.KeyCtrlP)
	m.prompt.SetValue("/mode")
	m = press(m, tea.KeyEnter)
	if m.prompt.Value() != "Rascunho importante" || m.modal != "modes" {
		t.Fatal("palette selection lost draft")
	}
	m.modal = "models"
	m.models = []llm.Model{{ID: "a"}}
	m.catalogSettings = m.Settings
	m.previewID = 2
	m.acceptPreview(previewResult{ID: 1, Settings: m.Settings, Model: llm.Model{ID: "a", Context: 131072}})
	if m.models[0].Context != 0 {
		t.Fatal("stale preview applied")
	}
	m.acceptPreview(previewResult{ID: 2, Settings: m.Settings, Model: llm.Model{ID: "a", Context: 131072, Capabilities: []string{"tools"}}})
	if m.models[0].Context != 131072 {
		t.Fatal("current preview lost")
	}
}
func TestInvalidProviderConfigurationIsNeverOverwrittenByUI(t *testing.T) {
	m := fixture(t)
	path := filepath.Join(m.Directory, "providers.json")
	os.WriteFile(path, []byte("{broken"), 0600)
	m.loadPreferences()
	m.rememberProvider(config.Default())
	m.savePreferences()()
	raw, _ := os.ReadFile(path)
	if string(raw) != "{broken" {
		t.Fatal("invalid user config overwritten")
	}
}
func TestCachedCatalogReopensWithoutBlockingAndKeepsProviderCredentialsInMemory(t *testing.T) {
	m := fixture(t)
	s := config.Default()
	s.APIKey = "secret"
	m.Settings = s
	m.rememberProvider(s)
	m.catalogGroups = []catalogGroup{{Settings: s, Models: []llm.Model{{ID: "cached", Context: 131072}}}}
	m.savePreferences()()
	next := New(m.Repo, s, m.Directory, false)
	defer next.Shutdown()
	if len(next.catalogGroups) != 1 || next.catalogGroups[0].Settings.APIKey != "secret" {
		t.Fatal("cache not hydrated")
	}
	next.openModels()
	if next.loading || !next.catalogRefreshing {
		t.Fatal("cached catalog blocked navigation")
	}
	raw, _ := os.ReadFile(filepath.Join(m.Directory, "models.json"))
	if strings.Contains(string(raw), "secret") {
		t.Fatal("catalog persisted credential")
	}
}

func TestExportPreservesFullAnswerAndProcessWhileRedactingCredentials(t *testing.T) {
	m := fixture(t)
	m.Settings.APIKey = "my-secret"
	answer := strings.Repeat("conteúdo ", 10000) + "FIM"
	m.turns = []*turn{{Question: "Leia", Final: answer, Steps: []step{{Title: "Pensamento", Body: "Detalhes my-secret"}}}}
	result := m.exportConversation()().(exportResult)
	if result.Err != nil {
		t.Fatal(result.Err)
	}
	raw, err := os.ReadFile(result.Path)
	if err != nil || !strings.Contains(string(raw), answer) || strings.Contains(string(raw), "my-secret") {
		t.Fatal("export incomplete or unsafe", err)
	}
}
