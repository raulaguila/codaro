package ui

import (
	"image"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

type headerLayout struct {
	Text        string
	Model, Mode image.Rectangle
}

// Rendering and hit testing use the same cell-based layout, including wide Unicode.
func (m Model) headerLayout() headerLayout {
	width := max(20, m.Width-4)
	model := clean(m.Settings.Model)
	if model == "" {
		model = "Conectar modelo"
	}
	if m.Demo {
		model = "demo-local"
	}
	mode := modeLabel(m.Settings.Mode)
	left := bright.Render("◆ Codaro Go") + muted.Render(" · "+clean(filepath.Base(m.Repo.Path)))
	if m.Width < 40 {
		left = ""
		model = "Modelo"
		mode = "Modo"
	}
	modeText := mode + " ▾"
	modeWidth := ansi.StringWidth(modeText) + 2
	modelWidth := min(32, max(8, width-modeWidth-17))
	modelText := ansi.Truncate(model, modelWidth, "…") + " ▾"
	button := func(label, name string) string {
		style := lipgloss.NewStyle().Foreground(lipgloss.Color("#B4C5D6")).Background(lipgloss.Color("#1B2735")).Padding(0, 1)
		if m.hover == name && !m.busy {
			style = style.Foreground(accent).Background(lipgloss.Color("#263E48"))
		}
		if m.busy {
			style = style.Foreground(lipgloss.Color("#687A8F"))
		}
		return style.Render(label)
	}
	modelButton, modeButton := button(modelText, "model"), button(modeText, "mode")
	rightWidth := lipgloss.Width(modelButton) + 2 + lipgloss.Width(modeButton)
	left = ansi.Truncate(left, max(0, width-rightWidth-1), "…")
	gap := max(0, width-lipgloss.Width(left)-rightWidth)
	modelX := 2 + lipgloss.Width(left) + gap
	modeX := modelX + lipgloss.Width(modelButton) + 2
	text := "  " + left + strings.Repeat(" ", gap) + modelButton + "  " + modeButton
	return headerLayout{Text: text, Model: image.Rect(modelX, 0, modelX+lipgloss.Width(modelButton), 1), Mode: image.Rect(modeX, 0, modeX+lipgloss.Width(modeButton), 1)}
}

func (m *Model) headerMouse(msg tea.MouseMsg) (bool, tea.Cmd) {
	layout := m.headerLayout()
	point := image.Pt(msg.X, msg.Y)
	target := ""
	if point.In(layout.Model) {
		target = "model"
	} else if point.In(layout.Mode) {
		target = "mode"
	}
	if msg.Action == tea.MouseActionMotion {
		m.hover = target
		return target != "", nil
	}
	if msg.Action != tea.MouseActionPress || msg.Button != tea.MouseButtonLeft || target == "" {
		return false, nil
	}
	if m.busy {
		m.status = "Aguarde a conclusão ou use Ctrl+X antes de alterar a sessão"
		return true, nil
	}
	if target == "model" {
		return true, m.openModels()
	}
	m.openModes()
	return true, nil
}

func (m Model) dialogOrigin() (int, int) {
	view := m.modalView()
	return max(0, (m.Width-lipgloss.Width(view))/2), 2 + max(0, (m.Height-2-lipgloss.Height(view))/2)
}

func (m *Model) modalMouse(msg tea.MouseMsg) tea.Cmd {
	if !m.loading && m.modal == "models" && (msg.Button == tea.MouseButtonWheelUp || msg.Button == tea.MouseButtonWheelDown) {
		count := len(m.filteredModels())
		if count > 0 {
			direction := 1
			if msg.Button == tea.MouseButtonWheelUp {
				direction = -1
			}
			m.modelIndex = max(0, min(count-1, m.modelIndex+direction))
		}
		return m.previewSelected()
	}
	if msg.Action != tea.MouseActionPress || msg.Button != tea.MouseButtonLeft {
		return nil
	}
	x, y := m.dialogOrigin()
	innerY := y + 1
	if m.Height > 26 {
		innerY++
	}
	left := x + 3
	right := x + lipgloss.Width(m.modalView()) - 3
	if msg.X < left || msg.X >= right {
		return nil
	}
	if m.loading {
		return nil
	}
	switch m.modal {
	case "modes":
		row := m.modeHitRow(msg.Y - (innerY + 2))
		if row >= 0 {
			m.modeIndex = row
			m.applyMode(modes[row].ID)
		}
	case "models":
		if msg.Y == innerY+2 {
			m.modelSearch.Focus()
			return nil
		}
		start, height := m.modelListPage()
		row := msg.Y - (innerY + 4)
		rows := m.modelRows()
		_ = start
		_ = height
		if row >= 0 && row < len(rows) && rows[row].Index >= 0 {
			m.modelIndex = rows[row].Index
			return m.chooseModel()
		}
	}
	return nil
}

func (m Model) modelListPage() (int, int) {
	height := max(3, min(9, m.Height-15))
	if m.modalError != "" {
		height = max(3, height-lipgloss.Height(wrap(shorten(m.modalError, 220), max(16, min(76, m.Width-8)-4))))
	}
	if m.catalogRefreshing && !m.loading {
		height = max(3, height-1)
	}
	return max(0, m.modelIndex-(height-2)/2), height
}
