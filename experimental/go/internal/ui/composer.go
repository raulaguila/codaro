package ui

import (
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/raulaguila/codaro/experimental/go/internal/agent"
)

type processHit struct{ Line, Turn int }

func planActive(tasks []agent.Task) bool {
	for _, t := range tasks {
		if t.Status == "pending" || t.Status == "in_progress" {
			return true
		}
	}
	return false
}
func (m Model) planView() string {
	if len(m.plan) == 0 || !planActive(m.plan) && !m.planExpanded {
		return ""
	}
	complete := 0
	for _, t := range m.plan {
		if t.Status == "completed" {
			complete++
		}
	}
	marker := "▸"
	if m.planExpanded {
		marker = "▾"
	}
	var b strings.Builder
	b.WriteString(muted.Render(fmt.Sprintf("%s Plano · %d/%d · Ctrl+B", marker, complete, len(m.plan))) + "\n")
	if m.planExpanded {
		start := m.planOffset
		start = max(0, min(start, len(m.plan)-1))
		for i := start; i < min(start+3, len(m.plan)); i++ {
			task := m.plan[i]
			icon := "○"
			style := muted
			switch task.Status {
			case "completed":
				icon = "✓"
			case "cancelled":
				icon = "−"
			case "in_progress":
				icon = "●"
				style = bright
			}
			b.WriteString(style.Render("  "+icon+" "+ansi.Truncate(clean(task.Title), max(12, m.Width-12), "…")) + "\n")
		}
		if len(m.plan) > 3 {
			b.WriteString(muted.Render(fmt.Sprintf("  %d–%d de %d · Alt+↑↓", start+1, min(start+3, len(m.plan)), len(m.plan))) + "\n")
		}
	}
	return b.String()
}
func (m Model) questionView() string {
	if m.question == nil {
		return ""
	}
	var b strings.Builder
	b.WriteString(bright.Render("? Sua decisão") + "\n")
	lines := strings.Split(wrap(clean(m.question.Text), max(16, m.Width-8)), "\n")
	if len(lines) > 3 {
		lines = append(lines[:2], "… Ctrl+O para pergunta completa")
	}
	b.WriteString(strings.Join(lines, "\n") + "\n")
	for i, option := range m.question.Options {
		mark := "○ "
		style := muted
		if i == m.questionIndex {
			mark = "● "
			style = bright
		}
		b.WriteString(style.Render(mark+ansi.Truncate(clean(option), max(12, m.Width-10), "…")) + "\n")
	}
	return b.String()
}
func (m Model) composerView() string {
	line := muted.Render("  "+strings.Repeat("─", max(1, m.Width-4))) + "\n"
	var b strings.Builder
	b.WriteString(line)
	if content := m.planView() + m.questionView(); content != "" {
		b.WriteString(lipgloss.NewStyle().Padding(0, 2).Render(strings.TrimSuffix(content, "\n")) + "\n")
	}
	b.WriteString(lipgloss.NewStyle().Padding(0, 2).Render(m.prompt.View()) + "\n")
	if m.question != nil {
		b.WriteString("  " + muted.Render(ansi.Truncate("↑↓ opções · Enter envia opção ou texto · Esc cancela atividade", max(16, m.Width-4), "…")) + "\n")
	}
	return b.String()
}
func (m *Model) answerQuestion() bool {
	if m.question == nil {
		return false
	}
	answer := strings.TrimSpace(m.prompt.Value())
	if answer == "" && len(m.question.Options) > 0 {
		answer = m.question.Options[m.questionIndex%len(m.question.Options)]
	}
	if answer == "" {
		m.status = "Escolha uma opção ou escreva sua resposta"
		return true
	}
	select {
	case m.questionReply <- answer:
		if t := m.current(); t != nil {
			t.Steps = append(t.Steps, step{Kind: "decision", Title: clean(m.question.Text), Body: clean(answer)})
		}
		m.question = nil
		m.questionReply = nil
		m.prompt.SetValue(m.questionDraft)
		m.questionDraft = ""
		m.prompt.Placeholder = "Pergunte sobre o projeto…"
		m.status = "Continuando…"
		m.dirty = true
	default:
		m.status = "A resposta já está sendo enviada"
	}
	return true
}
func (m Model) footer() string {
	status := m.status
	if m.busy {
		if t := m.current(); t != nil {
			status += fmt.Sprintf(" · %ds", int(time.Since(t.Started).Seconds()))
		}
	}
	if !m.follow && len(m.turns) > 0 {
		status = "↓ Novas atividades · Ctrl+End para acompanhar"
	}
	if len(m.queued) > 0 {
		status += fmt.Sprintf(" · %d na fila", len(m.queued))
	}
	meta := ""
	if m.contextUsed > 0 {
		percent := m.contextUsed * 100 / max(1, m.Settings.ContextWindow)
		meta = fmt.Sprintf("Contexto %d%%", percent)
	}
	if t := m.current(); t != nil && m.busy && !t.FirstToken.IsZero() && t.Draft.Len() > 0 {
		elapsed := max(0.1, time.Since(t.FirstToken).Seconds())
		meta = fmt.Sprintf("≈%.0f t/s · %s", float64(t.Draft.Len())/4/elapsed, meta)
	}
	width := max(16, m.Width-4)
	if m.Width > 90 && meta != "" {
		status = ansi.Truncate(status, max(12, width-ansi.StringWidth(meta)-2), "…")
		status += strings.Repeat(" ", max(1, width-ansi.StringWidth(status)-ansi.StringWidth(meta))) + meta
	}
	hint := "Enter envia · / comandos · @ arquivo · Ctrl+P menu"
	if m.busy {
		hint = "Enter adiciona à fila · Ctrl+X cancela · Ctrl+T processo"
	}
	if m.question != nil {
		hint = "Sua resposta retoma a atividade"
	}
	if m.question == nil && (len(m.matchingCommands()) > 0 || len(m.fileMatches()) > 0) {
		hint = "↑↓ navegar · Enter selecionar · Tab completar · Esc fechar"
	}
	if m.hover == "model" && !m.busy {
		hint = "Clique para trocar modelo · Ctrl+L"
	}
	if m.hover == "mode" && !m.busy {
		hint = "Clique para escolher modo · Shift+Tab"
	}
	if m.question != nil {
		return "  " + muted.Render(ansi.Truncate(status, width, "…"))
	}
	return "  " + muted.Render(ansi.Truncate(status, width, "…")) + "\n  " + muted.Render(ansi.Truncate(hint, width, "…"))
}
func (m Model) View() string {
	header := m.headerLayout().Text + "\n" + muted.Render(strings.Repeat("─", m.Width))
	if m.modal != "" {
		return header + "\n" + lipgloss.Place(m.Width, max(8, m.Height-2), lipgloss.Center, lipgloss.Center, m.modalView())
	}
	return header + "\n" + lipgloss.NewStyle().Padding(0, 2).Render(m.viewport.View()) + "\n" + m.menuView() + m.composerView() + m.footer()
}

func (m *Model) processMouse(msg tea.MouseMsg) bool {
	if msg.Action != tea.MouseActionPress || msg.Button != tea.MouseButtonLeft {
		return false
	}
	// The composer shares the same cell layout used for rendering.
	composerY := 2 + m.viewport.Height + strings.Count(m.menuView(), "\n")
	if m.planView() != "" && msg.Y == composerY+1 {
		m.planExpanded = !m.planExpanded
		m.dirty = true
		return true
	}
	if m.question != nil {
		start := composerY + 1 + strings.Count(m.planView(), "\n") + strings.Count(m.questionView(), "\n") - len(m.question.Options)
		if index := msg.Y - start; index >= 0 && index < len(m.question.Options) {
			m.questionIndex = index
			return true
		}
	}
	if !m.follow && msg.Y == composerY+strings.Count(m.composerView(), "\n") {
		m.follow = true
		m.viewport.GotoBottom()
		return true
	}
	if msg.Y < 2 || msg.Y >= 2+m.viewport.Height {
		return false
	}
	line := msg.Y - 2 + m.viewport.YOffset
	for _, hit := range m.processHits {
		if line == hit.Line {
			t := m.turns[hit.Turn]
			if hit.Turn == len(m.turns)-1 {
				m.expanded = !m.expanded
				m.manualExpanded = true
			} else {
				t.ProcessOpen = !t.ProcessOpen
			}
			m.dirty = true
			return true
		}
	}
	return false
}
