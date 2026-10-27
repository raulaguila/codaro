package ui

import (
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/raulaguila/codaro/experimental/go/internal/agent"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
)

type exportResult struct {
	Path string
	Err  error
}

func (m Model) exportConversation() tea.Cmd {
	var b strings.Builder
	for _, t := range m.turns {
		b.WriteString("## Você\n\n" + t.Question + "\n\n")
		b.WriteString("### Processo\n\n")
		for _, s := range t.Steps {
			b.WriteString(s.Title + "\n\n" + s.Body + "\n\n")
		}
		b.WriteString("## Codaro\n\n" + t.Final + "\n\n")
	}
	text := config.Redact(b.String(), m.Settings.APIKey)
	for _, p := range m.profiles {
		text = config.Redact(text, p.Settings.APIKey)
	}
	path := filepath.Join(filepath.Dir(agent.TracePath(m.Directory, m.Repo.Path)), "conversation.md")
	return func() tea.Msg { err := config.WritePrivate(path, []byte(text)); return exportResult{path, err} }
}
