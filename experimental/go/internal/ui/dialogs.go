package ui

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

type diffResult struct {
	Text string
	Err  error
}
type pickerRow struct {
	Text  string
	Index int
}

func (m Model) modelRows() []pickerRow {
	entries := m.entries()
	start, height := m.modelListPage()
	rows := []pickerRow{}
	group := ""
	for i := start; i < len(entries) && len(rows) < height; i++ {
		e := entries[i]
		if e.Provider != group {
			if len(rows)+1 >= height {
				break
			}
			rows = append(rows, pickerRow{muted.Render("  " + clean(e.Provider)), -1})
			group = e.Provider
		}
		meta := ""
		key := modelID(e.Settings, e.Model.ID)
		if has(m.preferences.Favorites, key) {
			meta += " ★"
		} else if has(m.preferences.Recent, key) {
			meta += " · recente"
		}
		if e.Model.ID == m.Settings.Model && providerID(e.Settings) == providerID(m.Settings) {
			meta += " · Atual"
		}
		label := ansi.Truncate(clean(e.Model.ID), max(8, min(76, m.Width-8)-10-ansi.StringWidth(meta)), "…") + meta
		if i == m.modelIndex {
			label = bright.Render("› " + label)
		} else {
			label = "  " + label
		}
		rows = append(rows, pickerRow{label, i})
	}
	return rows
}
func (m Model) extraDialog(width int) (string, bool) {
	var b strings.Builder
	switch m.modal {
	case "sessions":
		b.WriteString(bright.Render("Conversas deste projeto") + "\n\n")
		if len(m.sessions) == 0 {
			b.WriteString(muted.Render("Nenhuma conversa salva. Esc volta."))
		}
		start := max(0, m.sessionIndex-4)
		for i := start; i < min(start+5, len(m.sessions)); i++ {
			s := m.sessions[i]
			title := "Conversa"
			if len(s.Turns) > 0 {
				title = s.Turns[0].Question
			}
			label := fmt.Sprintf("%s · %s", s.Updated.Format("02/01 15:04"), title)
			label = ansi.Truncate(clean(label), width-8, "…")
			if i == m.sessionIndex {
				b.WriteString(bright.Render("› " + label))
			} else {
				b.WriteString("  " + label)
			}
			b.WriteString("\n")
		}
		b.WriteString("\n" + muted.Render("↑↓ escolher · Enter retoma · Esc volta"))
	case "history":
		b.WriteString(bright.Render("Buscar mensagens anteriores") + "\n\n" + m.historySearch.View() + "\n\n")
		matches := m.historyMatches()
		start := max(0, m.historyIndex-4)
		for i := start; i < min(start+5, len(matches)); i++ {
			label := ansi.Truncate(strings.ReplaceAll(clean(matches[i]), "\n", " "), width-8, "…")
			if i == m.historyIndex {
				b.WriteString(bright.Render("› " + label))
			} else {
				b.WriteString("  " + label)
			}
			b.WriteString("\n")
		}
		if len(matches) == 0 {
			b.WriteString(muted.Render("Nenhuma mensagem encontrada.\n"))
		}
		b.WriteString("\n" + muted.Render("↑↓ escolher · Enter leva ao input · Esc volta"))
	case "question":
		b.WriteString(bright.Render("Pergunta completa") + "\n\n" + m.diffView.View() + "\n\n" + muted.Render("↑↓ rolam · Esc volta à resposta"))
	case "diff":
		b.WriteString(bright.Render("Alterações do projeto · somente leitura") + "\n\n" + m.diffView.View() + "\n\n" + muted.Render("↑↓ / PgUp PgDn rolam · Esc volta"))
	case "permissions":
		b.WriteString(bright.Render("Permissões") + "\n\n" + wrap("Esta versão oferece somente leitura. Edições e comandos estão indisponíveis. A política de aprovação será habilitada junto ao modo Executar.", width-6) + "\n\n" + muted.Render("Esc volta"))
	default:
		return "", false
	}
	if m.modalError != "" {
		b.WriteString("\n\n" + warning.Render(wrap(shorten(m.modalError, 160), width-6)))
	}
	return b.String(), true
}
func (m *Model) updateExtraDialog(msg tea.KeyMsg) (bool, tea.Cmd) {
	key := msg.String()
	switch m.modal {
	case "sessions":
		if key == "up" {
			m.sessionIndex = max(0, m.sessionIndex-1)
		}
		if key == "down" {
			m.sessionIndex = min(max(0, len(m.sessions)-1), m.sessionIndex+1)
		}
		if key == "enter" {
			m.resumeSession(m.sessionIndex)
		}
		return true, nil
	case "history":
		matches := m.historyMatches()
		switch key {
		case "up":
			m.historyIndex = max(0, m.historyIndex-1)
		case "down":
			m.historyIndex = min(max(0, len(matches)-1), m.historyIndex+1)
		case "enter":
			if len(matches) > 0 {
				m.prompt.SetValue(matches[m.historyIndex%len(matches)])
				m.prompt.CursorEnd()
				m.modal = ""
				m.dirty = true
			}
		default:
			var cmd tea.Cmd
			m.historySearch, cmd = m.historySearch.Update(msg)
			m.historyIndex = 0
			return true, cmd
		}
		return true, nil
	case "diff", "question":
		var cmd tea.Cmd
		m.diffView, cmd = m.diffView.Update(msg)
		return true, cmd
	case "permissions":
		if key == "enter" {
			m.modal = ""
		}
		return true, nil
	}
	return false, nil
}
func (m *Model) openDiff() tea.Cmd {
	m.modal = "diff"
	m.modalError = ""
	m.diffView = viewport.New(max(16, min(76, m.Width-8)-6), max(3, m.Height-14))
	m.diffView.SetContent("Consultando alterações…")
	repo, root := m.Repo, m.workers.Context
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(root, 5*time.Second)
		defer cancel()
		text, err := repo.Diff(ctx)
		return diffResult{text, err}
	}
}
