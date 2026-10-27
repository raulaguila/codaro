package ui

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/raulaguila/codaro/experimental/go/internal/agent"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

type storedTurn struct {
	Question, Final string
	Steps           []step
	Started         time.Time
}
type session struct {
	Version int           `json:"version"`
	Root    string        `json:"root"`
	ID      string        `json:"id"`
	Updated time.Time     `json:"updated"`
	Turns   []storedTurn  `json:"turns"`
	History []llm.Message `json:"history"`
	Plan    []agent.Task  `json:"plan,omitempty"`
	Draft   string        `json:"draft,omitempty"`
}
type storageResult struct{ Err error }
type sessionsResult struct {
	Sessions []session
	Err      error
}

var sessionWriteMu sync.Mutex

func (m Model) sessionDirectory() string {
	return filepath.Join(filepath.Dir(agent.TracePath(m.Directory, m.Repo.Path)), "sessions")
}
func (m Model) snapshot() session {
	s := session{Version: 1, Root: m.Repo.Path, ID: m.sessionID, Updated: time.Now(), History: append([]llm.Message(nil), m.history...), Plan: append([]agent.Task(nil), m.plan...), Draft: m.prompt.Value()}
	for _, t := range m.turns {
		s.Turns = append(s.Turns, storedTurn{Question: t.Question, Final: t.Final, Steps: append([]step(nil), t.Steps...), Started: t.Started})
	}
	return s
}
func (m Model) saveSession() tea.Cmd {
	if len(m.turns) == 0 && len(m.plan) == 0 {
		return nil
	}
	s := m.snapshot()
	raw, err := json.Marshal(s)
	if err != nil || len(raw) > 4*1024*1024 {
		return func() tea.Msg { return storageResult{Err: fmt.Errorf("sessão excede o limite de armazenamento")} }
	}
	content := config.Redact(string(raw), m.Settings.APIKey)
	for _, p := range m.profiles {
		content = config.Redact(content, p.Settings.APIKey)
	}
	dir := m.sessionDirectory()
	name := s.ID + ".json"
	return func() tea.Msg {
		sessionWriteMu.Lock()
		defer sessionWriteMu.Unlock()
		path := filepath.Join(dir, name)
		var old session
		if boundedRead(path, &old, 4*1024*1024) == nil && old.Updated.After(s.Updated) {
			return storageResult{}
		}
		if e := config.WritePrivate(path, []byte(content)); e != nil {
			return storageResult{Err: e}
		}
		entries, _ := os.ReadDir(dir)
		sort.Slice(entries, func(i, j int) bool { return newerEntry(entries[i], entries[j]) })
		for i, e := range entries {
			if i >= 10 && !e.IsDir() && strings.HasSuffix(e.Name(), ".json") {
				_ = os.Remove(filepath.Join(dir, e.Name()))
			}
		}
		return storageResult{}
	}
}
func (m *Model) openSessions() tea.Cmd {
	if m.busy {
		m.status = "Cancele a atividade antes de trocar a conversa"
		return nil
	}
	m.modal = "sessions"
	m.sessionIndex = 0
	m.sessions = nil
	m.modalError = ""
	dir, root := m.sessionDirectory(), m.Repo.Path
	return func() tea.Msg {
		entries, err := os.ReadDir(dir)
		if os.IsNotExist(err) {
			return sessionsResult{}
		}
		if err != nil {
			return sessionsResult{Err: err}
		}
		out := []session{}
		sort.Slice(entries, func(i, j int) bool { return newerEntry(entries[i], entries[j]) })
		for _, entry := range entries {
			if len(out) >= 10 {
				break
			}
			var s session
			if entry.IsDir() {
				continue
			}
			if boundedRead(filepath.Join(dir, entry.Name()), &s, 4*1024*1024) == nil && s.Version == 1 && s.Root == root && validSession(s) {
				out = append(out, s)
			}
		}
		return sessionsResult{Sessions: out}
	}
}
func (m *Model) resumeSession(index int) {
	if index < 0 || index >= len(m.sessions) || m.busy {
		return
	}
	s := m.sessions[index]
	m.turns = nil
	for _, t := range s.Turns {
		m.turns = append(m.turns, &turn{Question: t.Question, Final: t.Final, Steps: t.Steps, Started: t.Started, Done: true})
	}
	m.sessionID = s.ID
	m.history = s.History
	m.plan = s.Plan
	m.planSeen = true
	m.planExpanded = planActive(s.Plan)
	m.prompt.SetValue(s.Draft)
	m.modal = ""
	m.contextUsed = 0
	m.expanded = false
	m.follow = true
	m.dirty = true
	m.status = "Conversa retomada · nenhuma ação reexecutada"
}
func (m Model) historyMatches() []string {
	query := m.historySearch.Value()
	out := []string{}
	for i := len(m.turns) - 1; i >= 0; i-- {
		q := m.turns[i].Question
		if fuzzy(query, q) && !has(out, q) {
			out = append(out, q)
		}
	}
	for i := len(m.inputHistory) - 1; i >= 0; i-- {
		q := m.inputHistory[i]
		if fuzzy(query, q) && !has(out, q) {
			out = append(out, q)
		}
	}
	return out
}

func validSession(s session) bool {
	if len(s.ID) != 20 || len(s.Turns) > 20 || len(s.History) > 40 || len(s.History)%2 != 0 || len(s.Plan) > 12 {
		return false
	}
	for _, r := range s.ID {
		if r < '0' || r > '9' {
			return false
		}
	}
	for i, m := range s.History {
		role := "user"
		if i%2 == 1 {
			role = "assistant"
		}
		if m.Role != role || len(m.ToolCalls) > 0 || m.ToolCallID != "" {
			return false
		}
	}
	if len(s.Plan) > 0 {
		_, err := agent.ParsePlan(llm.Encode(map[string]any{"tasks": s.Plan}))
		if err != nil {
			return false
		}
	}
	return true
}

func newerEntry(a, b os.DirEntry) bool {
	ai, ae := a.Info()
	bi, be := b.Info()
	if ae == nil && be == nil && !ai.ModTime().Equal(bi.ModTime()) {
		return ai.ModTime().After(bi.ModTime())
	}
	return a.Name() > b.Name()
}
