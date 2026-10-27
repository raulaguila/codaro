// Package ui is an independent Bubble Tea client for the Go agent.
package ui

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
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
var panel = lipgloss.NewStyle().Border(lipgloss.RoundedBorder()).BorderForeground(accent).Padding(1, 2)

type step struct {
	Kind, Title, Body, Summary string
	Failed                     bool
}
type turn struct {
	Question, Final, Rendered string
	Steps                     []step
	Draft, Reasoning          strings.Builder
	Started                   time.Time
	Done                      bool
	Width                     int
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
type preset struct{ Name, Kind, URL string }

var presets = []preset{
	{"Ollama local", "ollama", "http://localhost:11434"},
	{"OpenAI compatible", "openai", "http://localhost:8080/v1"},
	{"OpenAI", "openai", "https://api.openai.com/v1"},
	{"Groq", "openai", "https://api.groq.com/openai/v1"},
	{"Gemini compatible", "openai", "https://generativelanguage.googleapis.com/v1beta/openai"},
}

type command struct{ Name, Description string }

var commands = []command{{"providers", "Conectar ou configurar provedor"}, {"models", "Escolher modelo"}, {"process", "Expandir ou recolher o processo"}, {"new", "Nova conversa"}, {"pwd", "Conferir diretório"}, {"status", "Contexto e diagnóstico"}, {"help", "Atalhos e recursos do protótipo"}}

type workers struct {
	Cancel context.CancelFunc
	WG     sync.WaitGroup
}
type Model struct {
	Repo               *tools.Repository
	Settings           config.Settings
	Directory          string
	Demo               bool
	Width, Height      int
	prompt             textarea.Model
	viewport           viewport.Model
	turns              []*turn
	history            []llm.Message
	events             chan envelope
	results            chan done
	workers            *workers
	runID              int
	busy               bool
	dirty              bool
	follow             bool
	expanded           bool
	manualExpanded     bool
	processLine        int
	status             string
	contextUsed        int
	outputTokens       int
	commandIndex       int
	commandDraft       string
	modal              string
	fields             []textinput.Model
	focus, presetIndex int
	tls                bool
	loading            bool
	operationID        int
	operationCancel    context.CancelFunc
	pending            config.Settings
	models             []llm.Model
	modelSearch        textinput.Model
	modelIndex         int
	modalError         string
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
	p.SetHeight(3)
	p.Focus()
	search := textinput.New()
	search.Placeholder = "Buscar modelo…"
	search.CharLimit = 200
	return Model{Repo: repo, Settings: s, Directory: dir, Demo: demo, prompt: p, viewport: viewport.New(80, 20), Width: 80, Height: 30, events: make(chan envelope, 1024), results: make(chan done, 2), workers: &workers{}, dirty: true, follow: true, status: "Pronto · / comandos · Ctrl+P menu", modelSearch: search}
}

func (m Model) Init() tea.Cmd { return tea.Batch(textarea.Blink, timer()) }
func timer() tea.Cmd {
	return tea.Tick(50*time.Millisecond, func(t time.Time) tea.Msg { return tick(t) })
}
func (m Model) Shutdown() {
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
	ctx, cancel := context.WithCancel(context.Background())
	m.workers.Cancel = cancel
	m.busy = true
	m.runID++
	id := m.runID
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
	a := agent.Agent{Repository: m.Repo, Settings: m.Settings, Provider: m.provider(m.Settings), TracePath: agent.TracePath(m.Directory, m.Repo.Path)}
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
		t.Draft.WriteString(clean(e.Text))
		m.status = "Gerando resposta…"
	case "reasoning":
		if len(t.Steps) == 0 || t.Steps[len(t.Steps)-1].Kind != "reasoning" {
			t.Steps = append(t.Steps, step{Kind: "reasoning", Title: "Raciocínio do modelo"})
		}
		t.Steps[len(t.Steps)-1].Body += clean(e.Text)
		m.status = "Pensando…"
	case "intermediate", "retry":
		if t.Draft.Len() > 0 {
			t.Steps = append(t.Steps, step{Kind: "text", Title: "Etapa intermediária", Body: t.Draft.String()})
			t.Draft.Reset()
		}
		if e.Kind == "retry" {
			m.status = e.Text
			t.Steps = append(t.Steps, step{Kind: "phase", Title: e.Text})
		}
	case "tool_start":
		t.Steps = append(t.Steps, step{Kind: "tool", Title: e.Text})
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
						if !m.manualExpanded {
							m.expanded = false
						}
					}
				}
				m.dirty = true
			}
		default:
		}
		if m.dirty {
			m.refresh()
		}
		return m, timer()
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
		m.models = msg.Models
		m.modelIndex = 0
		m.modelSearch.Reset()
		m.modelSearch.Focus()
		m.modal = "models"
		return m, textinput.Blink
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
		m.Demo = candidate.Provider == "demo"
		m.modal = ""
		m.history = nil
		m.status = "Pronto · histórico do modelo anterior liberado"
		m.contextUsed = 0
		m.dirty = true
		return m, m.prompt.Focus()
	case tea.KeyMsg:
		key := msg.String()
		if key == "ctrl+q" {
			if m.workers.Cancel != nil {
				m.workers.Cancel()
			}
			if m.operationCancel != nil {
				m.operationCancel()
			}
			return m, tea.Quit
		}
		if m.modal != "" {
			return m, m.updateModal(msg)
		}
		if key == "ctrl+x" {
			if m.workers.Cancel != nil {
				m.workers.Cancel()
				m.status = "Cancelando…"
			}
			return m, nil
		}
		if key == "ctrl+t" {
			m.toggleProcess()
			return m, nil
		}
		if key == "ctrl+p" {
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
				m.prompt.Reset()
				m.commandDraft = ""
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
			m.start(q)
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
			m.dirty = true
		}
		return m, cmd
	case tea.MouseMsg:
		if m.modal == "" {
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
		return m.loadCatalog(m.Settings)
	case "process":
		m.toggleProcess()
	case "new":
		if m.busy {
			m.status = "Cancele a geração antes de começar outra conversa"
			break
		}
		m.turns = nil
		m.history = nil
		m.status = "Nova conversa"
		m.contextUsed = 0
		m.dirty = true
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
			out = append(out, c)
		}
	}
	return out
}

func (m *Model) refresh() {
	width := max(16, m.Width-4)
	menu := len(m.matchingCommands())
	if menu > 5 {
		menu = 5
	}
	m.viewport.Width = width
	m.viewport.Height = max(2, m.Height-8-menu)
	var b strings.Builder
	if len(m.turns) == 0 {
		b.WriteString("\n" + bright.Render("Codaro, agora em Go.") + "\n\n")
		b.WriteString("Pergunte sobre o projeto e acompanhe as fontes consultadas.\n\n")
		if m.Settings.Model == "" && !m.Demo {
			b.WriteString(muted.Render("Digite /providers para conectar seu modelo."))
		} else if m.Demo {
			b.WriteString(warning.Render("Demonstração local · sem chamadas a modelos de IA."))
		}
	} else {
		for _, t := range m.turns {
			b.WriteString(muted.Render("Você") + "\n" + wrap(clean(t.Question), width) + "\n\n")
			if len(t.Steps) > 0 {
				if t == m.current() {
					m.processLine = strings.Count(b.String(), "\n")
				}
				expanded := !t.Done || (t == m.current() && m.expanded)
				if t == m.current() {
					expanded = m.expanded
				}
				if !expanded {
					b.WriteString(muted.Render(fmt.Sprintf("▸ Ver processo · %d etapas · Ctrl+T", len(t.Steps))) + "\n\n")
				} else {
					for _, s := range t.Steps {
						title := s.Title
						if s.Kind == "tool" {
							title = "↳ " + title
						}
						style := muted
						if s.Failed {
							style = warning
						}
						b.WriteString(style.Render(wrap(title, width)) + "\n")
						if s.Body != "" {
							limit := 500
							body := s.Body
							if s.Kind == "tool" && !m.manualExpanded {
								body = s.Summary
							}
							if s.Kind == "reasoning" {
								limit = 900
							}
							b.WriteString(muted.Render(wrap(shorten(body, limit), width)) + "\n")
						}
						b.WriteString("\n")
					}
				}
			}
			if t.Final != "" {
				if t.Rendered == "" || t.Width != width {
					t.Rendered = markdown(shorten(t.Final, 24000), width)
					t.Width = width
				}
				b.WriteString(bright.Render("Codaro") + "\n" + t.Rendered + "\n\n")
			} else if t.Draft.Len() > 0 {
				b.WriteString(bright.Render("Codaro · gerando") + "\n" + wrap(shorten(t.Draft.String(), 16000), width) + "\n\n")
			}
		}
	}
	m.viewport.SetContent(b.String())
	if m.follow {
		m.viewport.GotoBottom()
	}
	m.dirty = false
}

func (m *Model) toggleProcess() {
	m.expanded = !m.expanded
	m.manualExpanded = true
	m.follow = !m.expanded
	m.dirty = true
	m.refresh()
	if m.expanded {
		m.viewport.SetYOffset(m.processLine)
	}
}

func (m Model) View() string {
	model := m.Settings.Model
	if model == "" {
		model = "Conectar modelo"
	}
	if m.Demo {
		model = "demo-local"
	}
	header := bright.Render("◆ Codaro Go") + muted.Render(" · "+filepath.Base(m.Repo.Path))
	right := muted.Render(shorten(model, 28) + " · Perguntar")
	if m.Width >= 70 {
		header += strings.Repeat(" ", max(1, m.Width-lipgloss.Width(header)-lipgloss.Width(right)-4)) + right
	}
	header = "  " + header + "\n" + muted.Render(strings.Repeat("─", m.Width))
	if m.modal != "" {
		return header + "\n" + lipgloss.Place(m.Width, max(8, m.Height-2), lipgloss.Center, lipgloss.Center, m.modalView())
	}
	var menu strings.Builder
	matches := m.matchingCommands()
	if len(matches) > 0 {
		start := max(0, m.commandIndex-4)
		for i := start; i < min(start+5, len(matches)); i++ {
			line := fmt.Sprintf("/%-11s %s", matches[i].Name, matches[i].Description)
			if i == m.commandIndex {
				menu.WriteString("  " + bright.Render("› "+shorten(line, max(15, m.Width-7))) + "\n")
			} else {
				menu.WriteString("    " + muted.Render(shorten(line, max(15, m.Width-7))) + "\n")
			}
		}
	}
	status := m.status
	if m.busy {
		if t := m.current(); t != nil {
			status += fmt.Sprintf(" · %ds", int(time.Since(t.Started).Seconds()))
		}
	}
	meta := ""
	if m.contextUsed > 0 {
		meta = fmt.Sprintf("Contexto ≈%d/%d", m.contextUsed, m.Settings.ContextWindow)
	}
	if t := m.current(); t != nil && m.busy && t.Draft.Len() > 0 {
		elapsed := time.Since(t.Started).Seconds()
		if elapsed > 0 {
			meta = fmt.Sprintf("≈%.0f t/s · %s", float64(t.Draft.Len())/4/elapsed, meta)
		}
	}
	if m.Width > 90 && meta != "" {
		status = shorten(status, m.Width-len(meta)-8) + strings.Repeat(" ", max(1, m.Width-lipgloss.Width(status)-len(meta)-4)) + meta
	}
	hint := "Enter envia · Alt+Enter nova linha · / comandos · Ctrl+Q sair"
	if m.busy {
		hint = "Ctrl+X cancela · PgUp/PgDn histórico · Ctrl+T processo · Ctrl+Q sair"
	}
	if len(matches) > 0 {
		hint = "↑↓ navegar · Enter selecionar · Esc fechar"
	}
	return header + "\n" + lipgloss.NewStyle().Padding(0, 2).Render(m.viewport.View()) + "\n" + menu.String() + muted.Render("  "+strings.Repeat("─", max(1, m.Width-4))) + "\n  " + m.prompt.View() + "\n  " + muted.Render(shorten(status, max(15, m.Width-4))) + "\n  " + muted.Render(shorten(hint, max(15, m.Width-4)))
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
