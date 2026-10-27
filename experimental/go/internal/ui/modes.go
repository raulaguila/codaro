package ui

import (
	"github.com/charmbracelet/lipgloss"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
)

type modeOption struct {
	ID, Label, Description string
	Enabled                bool
}

var modes = []modeOption{
	{"ask", "Perguntar", "Entender o projeto e tirar dúvidas", true},
	{"plan", "Planejar", "Investigar e propor etapas e critérios", true},
	{"execute", "Executar", "Edições e comandos · próxima etapa do protótipo", false},
}

func modeLabel(value string) string {
	for _, mode := range modes {
		if mode.ID == value {
			return mode.Label
		}
	}
	return "Perguntar"
}

func (m Model) modeRow(index, width int) string {
	mode := modes[index]
	label := mode.Label + " · " + mode.Description
	if mode.ID == m.Settings.Mode || (mode.ID == "ask" && m.Settings.Mode == "") {
		label += " · Atual"
	}
	style := muted
	if index == m.modeIndex {
		style = bright
		label = "› " + label
	} else {
		label = "  " + label
	}
	if !mode.Enabled {
		style = muted
	}
	return style.Render(wrap(label, width-6))
}

func (m Model) modeHitRow(relativeY int) int {
	row := 0
	width := max(20, min(76, m.Width-8))
	for i := range modes {
		height := lipgloss.Height(m.modeRow(i, width))
		if relativeY >= row && relativeY < row+height {
			return i
		}
		row += height + 1
	}
	return -1
}

func (m *Model) openModes() {
	if m.busy {
		m.status = "Aguarde a conclusão ou cancele antes de trocar o modo"
		return
	}
	m.modal = "modes"
	m.modalError = ""
	m.modeIndex = 0
	for i, mode := range modes {
		if mode.ID == m.Settings.Mode {
			m.modeIndex = i
			break
		}
	}
}

func (m *Model) applyMode(value string) {
	if m.busy {
		m.status = "Aguarde a conclusão ou cancele antes de trocar o modo"
		return
	}
	available := false
	for _, mode := range modes {
		if mode.ID == value && mode.Enabled {
			available = true
		}
	}
	if !available {
		m.modalError = "Execução de alterações ainda não está habilitada neste protótipo."
		return
	}
	candidate := m.Settings
	candidate.Mode = value
	if !m.Demo {
		if err := config.Save(m.Directory, candidate); err != nil {
			m.modalError = "Não foi possível salvar o modo."
			return
		}
	}
	m.Settings = candidate
	m.modal = ""
	m.modalError = ""
	m.status = "Modo " + modeLabel(value) + " · conversa preservada"
	m.dirty = true
}
