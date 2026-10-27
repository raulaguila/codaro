// Package ui is an independent Bubble Tea client for the Go agent.
package ui

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/charmbracelet/bubbles/textarea"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/glamour"
	"github.com/charmbracelet/lipgloss"
	"github.com/raulaguila/codaro/experimental/go/internal/agent"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
	"github.com/raulaguila/codaro/experimental/go/internal/tools"
)

var accent = lipgloss.Color("#77D4D8")
var muted = lipgloss.NewStyle().Foreground(lipgloss.Color("#93A3B5"))
var bright = lipgloss.NewStyle().Foreground(accent).Bold(true)
var warning = lipgloss.NewStyle().Foreground(lipgloss.Color("#E7BB70"))
var panel = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(lipgloss.Color("#314553")).Padding(1, 2)

type step struct {
	Kind, Title, Body, Summary string
	Failed                     bool
	Name                       string
}
type turn struct {
	Question, Final, Rendered string
	Steps                     []step
	Draft                     strings.Builder
	Started                   time.Time
	Done                      bool
	Width                     int
	FirstToken                time.Time
	ProcessOpen               bool
	Finished                  time.Time
}
type envelope struct {
	ID    int
	Event agent.Event
}
type done struct {
	ID      int
	History []llm.Message
	Err     error
}
type tick time.Time
type catalogResult struct {
	ID       int
	Settings config.Settings
	Models   []llm.Model
	Err      error
}
type selectedResult struct {
	ID       int
	Settings config.Settings
	Model    llm.Model
	Err      error
}
type metadataResult struct {
	ID       int
	Settings config.Settings
	Model    llm.Model
	Err      error
}
type preset struct{ Name, Kind, URL string }

var presets = []preset{
	{"Ollama local", "ollama", "http://localhost:11434"},
	{"OpenAI compatible", "openai", "http://localhost:8080/v1"},
	{"OpenAI", "openai", "https://api.openai.com/v1"},
	{"Groq", "openai", "https://api.groq.com/openai/v1"},
	{"Gemini compatible", "openai", "https://generativelanguage.googleapis.com/v1beta/openai"},
}

type command struct{ Name, Description string }

var commands = []command{{"providers", "Conectar ou configurar provedor"}, {"models", "Escolher modelo"}, {"mode", "Escolher modo de trabalho"}, {"process", "Expandir ou recolher o processo"}, {"new", "Nova conversa"}, {"pwd", "Conferir diretório"}, {"status", "Contexto e diagnóstico"}, {"help", "Atalhos e recursos do protótipo"}, {"plan", "Acompanhar plano"}, {"cancel-plan", "Cancelar plano"}, {"thinking", "Mostrar ou recolher pensamento"}, {"details", "Mostrar detalhes das ferramentas"}, {"sessions", "Retomar conversa salva"}, {"diff", "Rever alterações no Git"}, {"permissions", "Consultar permissões"}, {"queue", "Editar última mensagem na fila"}, {"export", "Exportar conversa e processo completos"}}

type workers struct {
	Cancel  context.CancelFunc
	WG      sync.WaitGroup
	Context context.Context
	Close   context.CancelFunc
}
type Model struct {
	catalogRefreshing                           bool
	previewID                                   int
	previewCancel                               context.CancelFunc
	profilesInvalid, profilesDirty              bool
	Repo                                        *tools.Repository
	Settings                                    config.Settings
	Directory                                   string
	Demo                                        bool
	Width, Height                               int
	prompt                                      textarea.Model
	viewport                                    viewport.Model
	turns                                       []*turn
	history                                     []llm.Message
	events                                      chan envelope
	results                                     chan done
	workers                                     *workers
	runID                                       int
	busy                                        bool
	dirty                                       bool
	follow                                      bool
	expanded                                    bool
	manualExpanded                              bool
	processLine                                 int
	status                                      string
	contextUsed                                 int
	outputTokens                                int
	commandIndex                                int
	commandDraft                                string
	modal                                       string
	fields                                      []textinput.Model
	focus, presetIndex                          int
	tls                                         bool
	loading                                     bool
	operationID                                 int
	operationCancel                             context.CancelFunc
	pending                                     config.Settings
	models                                      []llm.Model
	modelSearch                                 textinput.Model
	modelIndex                                  int
	modalError                                  string
	metadataID                                  int
	metadataLoading                             bool
	deferredQuestion                            string
	catalogSettings                             config.Settings
	modeIndex                                   int
	hover                                       string
	providerFactory                             func(config.Settings) llm.Provider
	preferences                                 preferences
	profiles                                    []providerProfile
	catalogGroups                               []catalogGroup
	favoritesOnly                               bool
	showThinking, showDetails                   bool
	processHits                                 []processHit
	plan                                        []agent.Task
	planExpanded                                bool
	planSeen                                    bool
	planOffset                                  int
	question                                    *agent.Question
	questionReply                               chan string
	questionIndex                               int
	questionDraft                               string
	queued                                      []string
	files                                       []string
	filesLoaded, filesLoading, completionHidden bool
	fileIndex                                   int
	inputHistory                                []string
	historySearch                               textinput.Model
	historyIndex                                int
	sessions                                    []session
	sessionIndex                                int
	sessionID                                   string
	diffView                                    viewport.Model
}

func New(repo *tools.Repository, s config.Settings, dir string, demo bool) Model {
	p := textarea.New()
	p.Placeholder = "Pergunte sobre o projeto…"
	p.Prompt = "› "
	p.SetPromptFunc(2, func(line int) string {
		if line == 0 {
			return "› "
		}
		return "  "
	})
	p.ShowLineNumbers = false
	p.CharLimit = 12000
	p.SetHeight(1)
	p.Focus()
	search := textinput.New()
	search.Placeholder = "Buscar modelo…"
	search.CharLimit = 200
	ctx, cancel := context.WithCancel(context.Background())
	if s.ModelContext > 0 {
		s = llm.ResolveContext(s, llm.Model{ID: s.Model, Context: s.ModelContext, Source: "cache: " + s.ContextSource})
	}
	m := Model{Repo: repo, Settings: s, Directory: dir, Demo: demo, prompt: p, viewport: viewport.New(80, 20), Width: 80, Height: 30, events: make(chan envelope, 1024), results: make(chan done, 2), workers: &workers{Context: ctx, Close: cancel}, dirty: true, follow: true, status: "Pronto", modelSearch: search, historySearch: search, sessionID: fmt.Sprintf("%020d", time.Now().UnixNano())}
	if s.Model != "" && !demo {
		m.metadataLoading = true
		m.metadataID = 1
		m.status = "Consultando contexto do modelo…"
	}
	m.loadPreferences()
	return m
}

func (m Model) Init() tea.Cmd {
	cmds := []tea.Cmd{textarea.Blink, timer()}
	if m.metadataLoading {
		cmds = append(cmds, m.metadataCmd())
	}
	return tea.Batch(cmds...)
}
func timer() tea.Cmd {
	return tea.Tick(50*time.Millisecond, func(t time.Time) tea.Msg { return tick(t) })
}
func (m Model) Shutdown() {
	if m.workers.Close != nil {
		m.workers.Close()
	}
	if m.workers.Cancel != nil {
		m.workers.Cancel()
	}
	if m.operationCancel != nil {
		m.operationCancel()
	}
	m.workers.WG.Wait()
}
func (m Model) current() *turn {
	if len(m.turns) == 0 {
		return nil
	}
	return m.turns[len(m.turns)-1]
}
func (m Model) provider(s config.Settings) llm.Provider {
	if m.providerFactory != nil {
		return m.providerFactory(s)
	}
	if s.Provider == "demo" {
		return llm.Demo{}
	}
	return llm.New(s)
}

func (m *Model) start(question string) {
	if m.Settings.Model == "" && !m.Demo {
		m.status = "Conecte um provedor em /providers"
		m.openProvider()
		return
	}
	if m.busy {
		m.status = "Aguarde a conclusão ou use Ctrl+X para cancelar"
		return
	}
	if m.metadataLoading {
		m.deferredQuestion = question
		m.status = "Consultando contexto antes de enviar…"
		return
	}
	ctx, cancel := context.WithCancel(m.workers.Context)
	m.workers.Cancel = cancel
	m.busy = true
	m.runID++
	id := m.runID
	if m.current() != nil {
		m.current().ProcessOpen = m.expanded
	}
	m.expanded = true
	m.manualExpanded = false
	m.follow = true
	m.contextUsed = 0
	m.outputTokens = 0
	m.turns = append(m.turns, &turn{Question: question, Started: time.Now()})
	if len(m.turns) > 20 {
		m.turns = m.turns[len(m.turns)-20:]
	}
	m.prompt.Reset()
	m.status = "Investigando…"
	m.dirty = true
	a := agent.Agent{Repository: m.Repo, Settings: m.Settings, Provider: m.provider(m.Settings), TracePath: agent.TracePath(m.Directory, m.Repo.Path), Interactive: true, Plan: append([]agent.Task(nil), m.plan...)}
	m.inputHistory = append(m.inputHistory, question)
	if len(m.inputHistory) > 100 {
		m.inputHistory = m.inputHistory[len(m.inputHistory)-100:]
	}
	history := append([]llm.Message(nil), m.history...)
	events, results := m.events, m.results
	m.workers.WG.Add(1)
	workers := m.workers
	go func() {
		defer workers.WG.Done()
		h, err := a.Run(ctx, question, history, func(event agent.Event) {
			select {
			case events <- envelope{id, event}:
			case <-ctx.Done():
			}
		})
		results <- done{id, h, err}
	}()
}

func (m *Model) handleEvent(e agent.Event) {
	t := m.current()
	if t == nil {
		return
	}
	m.dirty = true
	switch e.Kind {
	case "delta":
		if t.FirstToken.IsZero() {
			t.FirstToken = time.Now()
		}
		t.Draft.WriteString(clean(e.Text))
		m.status = "Gerando resposta…"
	case "reasoning":
		if len(t.Steps) == 0 || t.Steps[len(t.Steps)-1].Kind != "reasoning" {
			t.Steps = append(t.Steps, step{Kind: "reasoning", Title: "Raciocínio do modelo"})
		}
		t.Steps[len(t.Steps)-1].Body += clean(e.Text)
		m.status = "Pensando…"
	case "intermediate", "retry":
		t.FirstToken = time.Time{}
		if t.Draft.Len() > 0 {
			t.Steps = append(t.Steps, step{Kind: "text", Title: "Etapa intermediária", Body: t.Draft.String()})
			t.Draft.Reset()
		}
		if e.Kind == "retry" {
			m.status = e.Text
			t.Steps = append(t.Steps, step{Kind: "phase", Title: e.Text})
		}
	case "plan":
		m.plan = append([]agent.Task(nil), e.Tasks...)
		if !m.planSeen {
			m.planExpanded = true
			m.planSeen = true
		}
		for i, task := range m.plan {
			if task.Status == "in_progress" {
				m.planOffset = max(0, i-1)
				break
			}
		}
	case "question":
		m.question = e.Question
		m.questionReply = e.Reply
		m.questionIndex = 0
		m.questionDraft = m.prompt.Value()
		m.prompt.Reset()
		m.prompt.Placeholder = "Escreva sua resposta ou escolha uma opção…"
		m.status = "Aguardando sua resposta"
	case "tool_start":
		t.Steps = append(t.Steps, step{Kind: "tool", Title: e.Text, Name: e.Name})
		m.status = e.Text + "…"
	case "tool_done":
		for i := len(t.Steps) - 1; i >= 0; i-- {
			if t.Steps[i].Kind == "tool" && t.Steps[i].Body == "" {
				t.Steps[i].Title = e.Text + fmt.Sprintf(" · %d ms", e.Duration.Milliseconds())
				t.Steps[i].Body = clean(e.Detail)
				t.Steps[i].Summary = toolSummary(e.Name, e.Detail)
				t.Steps[i].Failed = e.Failed
				break
			}
		}
	case "final":
		t.Final = clean(e.Text)
		t.Draft.Reset()
		t.Rendered = ""
	case "context":
		m.contextUsed = e.Input
	case "usage":
		if e.Input > 0 {
			m.contextUsed = e.Input
		}
		if e.Output > 0 {
			m.outputTokens = e.Output
		}
	case "phase", "notice":
		t.Steps = append(t.Steps, step{Kind: "phase", Title: clean(e.Text)})
		m.status = clean(e.Text)
	}
}

func (m Model) Update(message tea.Msg) (tea.Model, tea.Cmd) {
	var cmd tea.Cmd
	switch msg := message.(type) {
	case tea.WindowSizeMsg:
		m.Width = max(24, msg.Width)
		m.Height = max(12, msg.Height)
		m.prompt.SetWidth(max(16, m.Width-4))
		m.modelSearch.Width = max(15, min(65, m.Width-12))
		m.historySearch.Width = max(15, min(65, m.Width-14))
		m.diffView.Width = max(16, min(76, m.Width-8)-6)
		m.diffView.Height = max(3, m.Height-14)
		m.dirty = true
	case tick:
		for i := 0; i < 500; i++ {
			select {
			case item := <-m.events:
				if item.ID == m.runID {
					m.handleEvent(item.Event)
				}
			default:
				i = 500
			}
		}
		select {
		case result := <-m.results:
			if result.ID == m.runID {
				// Completion is queued after all events; consume the tail before collapsing.
				m.drainEvents()
				m.busy = false
				m.workers.Cancel = nil
				t := m.current()
				if t != nil {
					t.Done = true
					t.Finished = time.Now()
					if m.question != nil {
						m.question = nil
						m.questionReply = nil
						m.prompt.SetValue(m.questionDraft)
						m.questionDraft = ""
						m.prompt.Placeholder = "Pergunte sobre o projeto…"
					}
					if result.Err != nil {
						m.status = "Interrompido · rascunho preservado"
						if result.Err != context.Canceled {
							m.status = clean(config.Redact(result.Err.Error(), m.Settings.APIKey))
						}
						t.Steps = append(t.Steps, step{Kind: "phase", Title: m.status, Failed: true})
						if t.Draft.Len() > 0 {
							t.Steps = append(t.Steps, step{Kind: "text", Title: "Prévia interrompida", Body: t.Draft.String()})
							t.Draft.Reset()
						}
					} else {
						m.history = result.History
						m.status = "Pronto"
						if !m.manualExpanded && m.follow {
							m.expanded = false
						}
					}
				}
				m.dirty = true
				if result.Err != nil {
					m.queued = nil
				}
				save := m.saveSession()
				if result.Err == nil && len(m.queued) > 0 {
					next := m.queued[0]
					m.queued = m.queued[1:]
					draft := m.prompt.Value()
					m.start(next)
					m.prompt.SetValue(draft)
				}
				m.refresh()
				return m, tea.Batch(timer(), save)
			}
		default:
		}
		if m.dirty {
			m.refresh()
		}
		return m, timer()
	case previewRequest:
		return m, m.startPreview(msg)
	case previewResult:
		m.acceptPreview(msg)
		return m, nil
	case exportResult:
		m.status = "Conversa exportada: " + msg.Path
		if msg.Err != nil {
			m.status = "Não foi possível exportar a conversa"
		}
		return m, nil
	case storageResult:
		if msg.Err != nil {
			m.status = "Não foi possível salvar dados locais · confira permissões"
		}
		return m, nil
	case filesResult:
		m.filesLoading = false
		m.filesLoaded = msg.Err == nil
		m.files = msg.Files
		m.dirty = true
		if msg.Err != nil {
			m.status = "Não foi possível listar arquivos para @"
		}
	case sessionsResult:
		if m.modal == "sessions" {
			m.sessions = msg.Sessions
			if msg.Err != nil {
				m.modalError = "Não foi possível ler as conversas"
			}
		}
		return m, nil
	case diffResult:
		if m.modal == "diff" {
			m.diffView.SetContent(clean(msg.Text))
			if msg.Err != nil {
				m.modalError = msg.Err.Error()
			}
		}
		return m, nil
	case catalogsResult:
		if msg.ID != m.operationID {
			return m, nil
		}
		m.loading = false
		m.catalogRefreshing = false
		m.operationCancel = nil
		selectedKey := ""
		if entries := m.entries(); len(entries) > 0 {
			e := entries[min(m.modelIndex, len(entries)-1)]
			selectedKey = modelID(e.Settings, e.Model.ID)
		}
		for _, g := range msg.Groups {
			if g.Err != "" {
				m.modalError = "Provedor indisponível · mantendo modelos em cache, quando disponíveis"
				continue
			}
			found := false
			for i, old := range m.catalogGroups {
				if providerID(old.Settings) == providerID(g.Settings) {
					m.catalogGroups[i] = g
					found = true
					break
				}
			}
			if !found {
				m.catalogGroups = append(m.catalogGroups, g)
			}
		}
		m.modelIndex = 0
		for i, e := range m.entries() {
			if modelID(e.Settings, e.Model.ID) == selectedKey {
				m.modelIndex = i
				break
			}
		}
		m.modelSearch.Focus()
		return m, tea.Batch(textinput.Blink, m.previewSelected(), m.savePreferences())
	case catalogResult:
		if msg.ID != m.operationID {
			return m, nil
		}
		m.loading = false
		m.operationCancel = nil
		if msg.Err != nil {
			m.modalError = clean(config.Redact(msg.Err.Error(), msg.Settings.APIKey))
			return m, nil
		}
		m.pending = msg.Settings
		m.catalogGroups = nil
		if sameProvider(m.catalogSettings, msg.Settings) {
			for i := range msg.Models {
				if msg.Models[i].Context == 0 {
					for _, cached := range m.models {
						if cached.ID == msg.Models[i].ID && cached.Context > 0 {
							msg.Models[i] = cached
							if !strings.HasPrefix(msg.Models[i].Source, "cache:") {
								msg.Models[i].Source = "cache: " + cached.Source
							}
							break
						}
					}
				}
			}
		}
		m.catalogSettings = msg.Settings
		m.models = msg.Models
		if sameProvider(msg.Settings, m.Settings) && m.Settings.ModelContext > 0 {
			m.cacheModel(llm.Model{ID: m.Settings.Model, Context: m.Settings.ModelContext, Source: m.Settings.ContextSource})
		}
		m.modelIndex = 0
		for i, model := range m.models {
			if model.ID == m.Settings.Model {
				m.modelIndex = i
				break
			}
		}
		m.modelSearch.Reset()
		m.modelSearch.Focus()
		m.modal = "models"
		return m, tea.Batch(textinput.Blink, m.previewSelected())
	case selectedResult:
		if msg.ID != m.operationID {
			return m, nil
		}
		m.loading = false
		m.operationCancel = nil
		if msg.Err != nil {
			m.modalError = clean(config.Redact(msg.Err.Error(), msg.Settings.APIKey))
			return m, nil
		}
		candidate := llm.ResolveContext(msg.Settings, msg.Model)
		if candidate.Provider != "demo" {
			if err := config.Save(m.Directory, candidate); err != nil {
				m.modalError = "Não foi possível salvar a configuração: " + err.Error()
				return m, nil
			}
		}
		m.Settings = candidate
		m.rememberProvider(candidate)
		key := modelID(candidate, candidate.Model)
		recent := []string{key}
		for _, v := range m.preferences.Recent {
			if v != key && len(recent) < 20 {
				recent = append(recent, v)
			}
		}
		m.preferences.Recent = recent
		m.cacheModel(msg.Model)
		m.metadataID++
		m.metadataLoading = false
		m.deferredQuestion = ""
		m.Demo = candidate.Provider == "demo"
		m.modal = ""
		m.status = "Pronto · conversa preservada"
		m.contextUsed = 0
		m.dirty = true
		return m, tea.Batch(m.prompt.Focus(), m.savePreferences())
	case metadataResult:
		return m, m.acceptMetadata(msg)
	case tea.KeyMsg:
		key := msg.String()
		if key == "ctrl+q" {
			m.queued = nil
			if m.workers.Cancel != nil {
				m.workers.Cancel()
			}
			if m.operationCancel != nil {
				m.operationCancel()
			}
			save := m.saveSession()
			if save == nil {
				return m, tea.Quit
			}
			return m, func() tea.Msg { save(); return tea.Quit() }
		}
		if m.modal != "" {
			return m, m.updateModal(msg)
		}
		if key == "ctrl+x" || key == "esc" && m.question != nil {
			m.queued = nil
			m.deferredQuestion = ""
			if m.workers.Cancel != nil {
				m.workers.Cancel()
				m.status = "Cancelando…"
			}
			return m, nil
		}
		if key == "ctrl+b" {
			m.planExpanded = !m.planExpanded
			m.planSeen = true
			m.dirty = true
			return m, nil
		}
		if (key == "alt+up" || key == "alt+down") && len(m.plan) > 0 {
			delta := 1
			if key == "alt+up" {
				delta = -1
			}
			m.planOffset = max(0, min(max(0, len(m.plan)-3), m.planOffset+delta))
			m.dirty = true
			return m, nil
		}
		if key == "ctrl+o" && m.question != nil {
			m.modal = "question"
			m.diffView = viewport.New(max(16, min(76, m.Width-8)-6), max(3, m.Height-14))
			m.diffView.SetContent(wrap(clean(m.question.Text), m.diffView.Width))
			return m, nil
		}
		if key == "ctrl+r" {
			m.modal = "history"
			m.historySearch.Reset()
			m.historySearch.Placeholder = "Buscar mensagem…"
			m.historySearch.Focus()
			m.historyIndex = 0
			return m, textinput.Blink
		}
		if m.question != nil {
			if key == "up" && len(m.question.Options) > 0 {
				m.questionIndex = (m.questionIndex + len(m.question.Options) - 1) % len(m.question.Options)
				return m, nil
			}
			if key == "down" && len(m.question.Options) > 0 {
				m.questionIndex = (m.questionIndex + 1) % len(m.question.Options)
				return m, nil
			}
			if key == "enter" {
				m.answerQuestion()
				return m, nil
			}
		}
		if key == "ctrl+l" {
			return m, m.openModels()
		}
		if key == "shift+tab" {
			next := "plan"
			if m.Settings.Mode == "plan" {
				next = "ask"
			}
			m.applyMode(next)
			return m, nil
		}
		if key == "ctrl+t" {
			m.toggleProcess()
			return m, nil
		}
		if key == "ctrl+p" && m.question == nil {
			m.commandDraft = m.prompt.Value()
			m.prompt.SetValue("/")
			m.commandIndex = 0
			m.dirty = true
			return m, nil
		}
		if key == "pgup" || key == "pgdown" || key == "ctrl+home" || key == "ctrl+end" {
			if key == "ctrl+home" {
				m.viewport.GotoTop()
			} else if key == "ctrl+end" {
				m.viewport.GotoBottom()
				m.follow = true
			} else {
				m.viewport, cmd = m.viewport.Update(msg)
				m.follow = m.viewport.AtBottom()
			}
			if key != "ctrl+end" {
				m.follow = false
			}
			return m, cmd
		}
		files := m.fileMatches()
		if len(files) > 0 {
			switch key {
			case "up":
				m.fileIndex = (m.fileIndex + len(files) - 1) % len(files)
				return m, nil
			case "down":
				m.fileIndex = (m.fileIndex + 1) % len(files)
				return m, nil
			case "enter", "tab":
				m.selectFile()
				return m, nil
			case "esc":
				m.completionHidden = true
				m.dirty = true
				return m, nil
			}
		}
		available := m.matchingCommands()
		if len(available) > 0 {
			switch key {
			case "up":
				m.commandIndex = (m.commandIndex + len(available) - 1) % len(available)
				return m, nil
			case "down":
				m.commandIndex = (m.commandIndex + 1) % len(available)
				return m, nil
			case "esc":
				m.prompt.SetValue(m.commandDraft)
				if strings.HasPrefix(m.commandDraft, "/") {
					m.prompt.Reset()
				}
				m.commandDraft = ""
				m.dirty = true
				return m, nil
			case "enter", "tab":
				selected := available[m.commandIndex%len(available)]
				draft := m.commandDraft
				m.commandDraft = ""
				m.prompt.SetValue(draft)
				return m, m.executeCommand(selected.Name)
			}
		}
		if key == "enter" && !msg.Alt {
			q := strings.TrimSpace(m.prompt.Value())
			if q == "" {
				return m, nil
			}
			if strings.HasPrefix(q, "/") {
				m.prompt.Reset()
				return m, m.executeCommand(strings.TrimPrefix(q, "/"))
			}
			if m.busy {
				if len(m.queued) >= 5 {
					m.status = "Fila cheia · aguarde ou cancele"
				} else {
					m.queued = append(m.queued, q)
					m.dirty = true
					m.prompt.Reset()
					m.status = "Mensagem adicionada à fila"
				}
				return m, nil
			}
			m.start(q)
			return m, nil
		}
		if key == "up" && m.prompt.Value() == "" && len(m.inputHistory) > 0 {
			m.prompt.SetValue(m.inputHistory[len(m.inputHistory)-1])
			m.prompt.CursorEnd()
			return m, nil
		}
		if key == "alt+enter" {
			m.prompt.InsertString("\n")
			return m, nil
		}
		before := m.prompt.Value()
		m.prompt, cmd = m.prompt.Update(msg)
		if m.prompt.Value() != before {
			m.commandIndex = 0
			m.fileIndex = 0
			m.completionHidden = false
			m.dirty = true
			if _, ok := m.fileQuery(); ok {
				return m, tea.Batch(cmd, m.loadFiles())
			}
		}
		return m, cmd
	case tea.MouseMsg:
		if m.modal != "" {
			return m, m.modalMouse(msg)
		}
		if handled, command := m.headerMouse(msg); handled {
			return m, command
		}
		if m.processMouse(msg) {
			return m, nil
		}
		if msg.Button == tea.MouseButtonWheelUp || msg.Button == tea.MouseButtonWheelDown {
			m.viewport, cmd = m.viewport.Update(msg)
			m.follow = m.viewport.AtBottom()
			return m, cmd
		}
	}
	if m.dirty {
		m.refresh()
	}
	if m.modal == "" {
		m.prompt, cmd = m.prompt.Update(message)
	}
	return m, cmd
}

func (m *Model) drainEvents() {
	for {
		select {
		case item := <-m.events:
			if item.ID == m.runID {
				m.handleEvent(item.Event)
			}
		default:
			return
		}
	}
}

func (m *Model) executeCommand(name string) tea.Cmd {
	switch name {
	case "providers":
		if m.busy {
			m.status = "Cancele a geração antes de trocar o provedor"
			return nil
		}
		m.openProvider()
	case "models":
		if m.busy {
			m.status = "Cancele a geração antes de trocar o modelo"
			return nil
		}
		return m.openModels()
	case "mode":
		m.openModes()
	case "process":
		m.toggleProcess()
	case "thinking":
		m.showThinking = !m.showThinking
		m.dirty = true
		if m.showThinking && !m.expanded {
			m.toggleProcess()
		}
		return m.savePreferences()
	case "details":
		m.showDetails = !m.showDetails
		m.dirty = true
		if m.showDetails && !m.expanded {
			m.toggleProcess()
		}
		return m.savePreferences()
	case "plan":
		m.planExpanded = !m.planExpanded
		m.planSeen = true
		m.dirty = true
	case "cancel-plan":
		if m.busy && m.workers.Cancel != nil {
			m.workers.Cancel()
			m.queued = nil
		}
		for i := range m.plan {
			if m.plan[i].Status == "pending" || m.plan[i].Status == "in_progress" {
				m.plan[i].Status = "cancelled"
			}
		}
		m.planExpanded = false
		m.dirty = true
		return m.saveSession()
	case "sessions":
		return m.openSessions()
	case "diff":
		return m.openDiff()
	case "export":
		return m.exportConversation()
	case "permissions":
		m.modal = "permissions"
	case "queue":
		if len(m.queued) > 0 {
			m.prompt.SetValue(m.queued[len(m.queued)-1])
			m.queued = m.queued[:len(m.queued)-1]
			m.dirty = true
			m.status = "Última mensagem retirada da fila para editar"
		}
	case "new":
		if m.busy {
			m.status = "Cancele a geração antes de começar outra conversa"
			break
		}
		save := m.saveSession()
		m.turns = nil
		m.history = nil
		m.plan = nil
		m.planSeen = false
		m.queued = nil
		m.sessionID = fmt.Sprintf("%020d", time.Now().UnixNano())
		m.status = "Nova conversa"
		m.contextUsed = 0
		m.dirty = true
		return save
	case "pwd":
		m.status = m.Repo.Path
	case "status":
		m.modal = "status"
	case "help":
		m.modal = "help"
	default:
		m.status = "Comando desconhecido · / para ver opções"
	}
	return nil
}

func (m Model) matchingCommands() []command {
	value := m.prompt.Value()
	if !strings.HasPrefix(value, "/") || strings.Contains(value, "\n") {
		return nil
	}
	query := strings.ToLower(strings.TrimPrefix(value, "/"))
	out := []command{}
	for _, c := range commands {
		if strings.HasPrefix(c.Name, query) {
			if c.Name == query {
				out = append([]command{c}, out...)
			} else {
				out = append(out, c)
			}
		}
	}
	return out
}

func clean(s string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsControl(r) && r != '\n' && r != '\t' {
			return -1
		}
		return r
	}, s)
}
func shorten(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:max(0, n-1)]) + "…"
}
func wrap(s string, width int) string { return lipgloss.NewStyle().Width(max(10, width)).Render(s) }
func markdown(s string, width int) string {
	r, err := glamour.NewTermRenderer(glamour.WithStandardStyle("dark"), glamour.WithWordWrap(min(width, 100)))
	if err != nil {
		return wrap(s, width)
	}
	out, err := r.Render(s)
	if err != nil {
		return wrap(s, width)
	}
	return strings.TrimSpace(out)
}

func toolSummary(name, raw string) string {
	var doc map[string]any
	if json.Unmarshal([]byte(raw), &doc) != nil {
		return "Resultado recebido"
	}
	if err, ok := doc["error"].(string); ok {
		return clean(err)
	}
	switch name {
	case "list_files":
		if files, ok := doc["files"].([]any); ok {
			return fmt.Sprintf("%d arquivos nesta página · %v no projeto", len(files), doc["total"])
		}
	case "read_lines":
		return fmt.Sprintf("%v · linhas %v–%v", doc["path"], doc["start_line"], doc["end_line"])
	case "search_code":
		if rows, ok := doc["results"].([]any); ok {
			return fmt.Sprintf("%d ocorrências nesta página", len(rows))
		}
	case "get_project_map":
		return fmt.Sprintf("%v arquivos · mapa disponível no diagnóstico", doc["files"])
	case "get_repository_info":
		if root, ok := doc["root"].(string); ok {
			return root
		}
	}
	return "Resultado recebido · Ctrl+T para detalhes"
}
