package ui

import (
	"context"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

type filesResult struct {
	Files []string
	Err   error
}

func (m *Model) loadFiles() tea.Cmd {
	if m.filesLoading || m.filesLoaded {
		return nil
	}
	m.filesLoading = true
	repo, root := m.Repo, m.workers.Context
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(root, 5*time.Second)
		defer cancel()
		files, err := repo.Files(ctx)
		return filesResult{files, err}
	}
}
func (m Model) fileQuery() (string, bool) {
	value := m.prompt.Value()
	index := strings.LastIndex(value, "@")
	if index < 0 || m.completionHidden {
		return "", false
	}
	if index > 0 && !strings.ContainsAny(value[index-1:index], " \n\t") {
		return "", false
	}
	query := value[index+1:]
	if strings.ContainsAny(query, " \n\t\"") {
		return "", false
	}
	return query, true
}
func (m Model) fileMatches() []string {
	query, ok := m.fileQuery()
	if !ok {
		return nil
	}
	out := []string{}
	for _, f := range m.files {
		if fuzzy(query, f) {
			out = append(out, f)
			if len(out) == 100 {
				break
			}
		}
	}
	return out
}
func (m *Model) selectFile() {
	files := m.fileMatches()
	if len(files) == 0 {
		return
	}
	value := m.prompt.Value()
	index := strings.LastIndex(value, "@")
	name := files[m.fileIndex%len(files)]
	if strings.ContainsAny(name, " \t\"@") {
		name = fmt.Sprintf("%q", name)
	}
	m.prompt.SetValue(value[:index] + "@" + name + " ")
	m.prompt.CursorEnd()
	m.fileIndex = 0
	m.dirty = true
}
func (m Model) menuView() string {
	var b strings.Builder
	if files := m.fileMatches(); len(files) > 0 {
		start := max(0, m.fileIndex-3)
		for i := start; i < min(start+4, len(files)); i++ {
			line := shorten(files[i], max(12, m.Width-8))
			if i == m.fileIndex {
				b.WriteString("  " + bright.Render("› "+line) + "\n")
			} else {
				b.WriteString("    " + muted.Render(line) + "\n")
			}
		}
		return b.String()
	}
	matches := m.matchingCommands()
	start := max(0, m.commandIndex-4)
	for i := start; i < min(start+5, len(matches)); i++ {
		line := fmt.Sprintf("/%-11s %s", matches[i].Name, matches[i].Description)
		if i == m.commandIndex {
			b.WriteString("  " + bright.Render("› "+shorten(line, max(15, m.Width-7))) + "\n")
		} else {
			b.WriteString("    " + muted.Render(shorten(line, max(15, m.Width-7))) + "\n")
		}
	}
	return b.String()
}
