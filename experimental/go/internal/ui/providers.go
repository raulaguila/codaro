package ui

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/raulaguila/codaro/experimental/go/internal/agent"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

func (m *Model) openProvider() {
	m.modal = "provider"
	m.modalError = ""
	m.focus = 0
	m.presetIndex = 0
	m.tls = m.Settings.TLSInsecure
	for i, p := range presets {
		if p.URL == m.Settings.BaseURL {
			m.presetIndex = i
			break
		}
	}
	m.fields = make([]textinput.Model, 3)
	for i := range m.fields {
		m.fields[i] = textinput.New()
		m.fields[i].CharLimit = 1000
		m.fields[i].Width = max(15, min(65, m.Width-12))
	}
	m.fields[0].SetValue(m.Settings.BaseURL)
	m.fields[1].EchoMode = textinput.EchoPassword
	m.fields[1].EchoCharacter = '•'
	m.fields[1].SetValue(m.Settings.APIKey)
	m.fields[1].CharLimit = 16384
	m.fields[2].SetValue(strconv.Itoa(m.Settings.ContextWindow))
}

func (m *Model) loadCatalog(s config.Settings) tea.Cmd {
	m.operationID++
	id := m.operationID
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	m.operationCancel = cancel
	m.loading = true
	m.modalError = ""
	if m.modal == "" {
		m.modal = "models"
	}
	provider := m.provider(s)
	return func() tea.Msg {
		defer cancel()
		models, err := provider.Catalog(ctx)
		return catalogResult{id, s, models, err}
	}
}

func (m *Model) submitProvider() tea.Cmd {
	n, err := strconv.Atoi(strings.TrimSpace(m.fields[2].Value()))
	if err != nil {
		m.modalError = "A janela de trabalho deve ser um número inteiro"
		return nil
	}
	s := config.Settings{Provider: presets[m.presetIndex].Kind, BaseURL: strings.TrimSpace(m.fields[0].Value()), APIKey: strings.TrimSpace(m.fields[1].Value()), ContextWindow: n, TLSInsecure: m.tls}
	if err = s.Validate(); err != nil {
		m.modalError = err.Error()
		return nil
	}
	return m.loadCatalog(s)
}

func (m *Model) chooseModel() tea.Cmd {
	models := m.filteredModels()
	if len(models) == 0 {
		return nil
	}
	selected := models[min(m.modelIndex, len(models)-1)]
	s := m.pending
	if s.Provider == "" {
		s = m.Settings
	}
	m.operationID++
	id := m.operationID
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	m.operationCancel = cancel
	m.loading = true
	m.modalError = ""
	provider := m.provider(s)
	return func() tea.Msg {
		defer cancel()
		info, err := provider.Describe(ctx, selected.ID)
		return selectedResult{id, s, info, err}
	}
}

func (m Model) filteredModels() []llm.Model {
	query := strings.ToLower(m.modelSearch.Value())
	out := []llm.Model{}
	for _, model := range m.models {
		if strings.Contains(strings.ToLower(model.ID), query) {
			out = append(out, model)
		}
	}
	return out
}

func (m *Model) updateModal(msg tea.KeyMsg) tea.Cmd {
	key := msg.String()
	if key == "esc" {
		if m.operationCancel != nil {
			m.operationCancel()
		}
		m.operationID++
		m.loading = false
		m.operationCancel = nil
		m.modal = ""
		m.modalError = ""
		m.dirty = true
		return m.prompt.Focus()
	}
	if m.modal == "help" || m.modal == "status" {
		if key == "enter" {
			m.modal = ""
		}
		return nil
	}
	if m.loading {
		return nil
	}
	if m.modal == "models" {
		models := m.filteredModels()
		switch key {
		case "up":
			if len(models) > 0 {
				m.modelIndex = (m.modelIndex + len(models) - 1) % len(models)
			}
			return nil
		case "down":
			if len(models) > 0 {
				m.modelIndex = (m.modelIndex + 1) % len(models)
			}
			return nil
		case "enter":
			return m.chooseModel()
		}
		var cmd tea.Cmd
		m.modelSearch, cmd = m.modelSearch.Update(msg)
		m.modelIndex = 0
		return cmd
	}
	switch key {
	case "tab", "shift+tab":
		if key == "tab" {
			m.focus = (m.focus + 1) % 6
		} else {
			m.focus = (m.focus + 5) % 6
		}
	case "left", "right":
		if m.focus == 0 {
			direction := 1
			if key == "left" {
				direction = -1
			}
			m.presetIndex = (m.presetIndex + len(presets) + direction) % len(presets)
			m.fields[0].SetValue(presets[m.presetIndex].URL)
			m.fields[1].Reset()
			m.tls = false
			return nil
		}
	case "ctrl+s":
		return m.submitProvider()
	case "enter":
		if m.focus == 5 {
			return m.submitProvider()
		}
		if m.focus == 4 {
			m.tls = !m.tls
			return nil
		}
		m.focus = (m.focus + 1) % 6
	case " ":
		if m.focus == 4 {
			m.tls = !m.tls
			return nil
		}
	}
	var cmd tea.Cmd
	for i := range m.fields {
		if m.focus == i+1 {
			cmd = m.fields[i].Focus()
		} else {
			m.fields[i].Blur()
		}
	}
	if m.focus >= 1 && m.focus <= 3 {
		m.fields[m.focus-1], cmd = m.fields[m.focus-1].Update(msg)
	}
	return cmd
}

func (m Model) modalView() string {
	w := max(20, min(76, m.Width-8))
	var body strings.Builder
	if m.modal == "status" {
		body.WriteString(bright.Render("Sessão do protótipo") + "\n\n")
		body.WriteString(wrap(fmt.Sprintf("Diretório: %s\nModelo: %s\nContexto de trabalho: %d tokens\nCapacidade informada pela API: %d\nOrigem: %s\n\nÚltimo fluxo JSON:\n%s", m.Repo.Path, m.Settings.Model, m.Settings.ContextWindow, m.Settings.ModelContext, m.Settings.ContextSource, agent.TracePath(m.Directory, m.Repo.Path)), w-6))
		body.WriteString("\n\n" + muted.Render("Esc volta · estimativas conservadoras de contexto"))
	} else if m.modal == "help" {
		body.WriteString(bright.Render("Codaro Go · experimento") + "\n\n")
		body.WriteString("Consulta ao projeto com ferramentas somente leitura.\n\n")
		body.WriteString("/providers    Configurar Ollama ou API OpenAI compatible\n/models       Buscar e escolher modelo\n/process      Ver pensamento e ferramentas\n/new          Começar outra conversa\n/pwd          Conferir diretório\n/status       Contexto e caminho do prompt.json\n\n")
		body.WriteString("Ctrl+X cancela · Ctrl+Q sai · Ctrl+T processo\nPgUp/PgDn navegam · Alt+Enter nova linha\n\n")
		body.WriteString(muted.Render("Sem edições, shell, MCP ou migração de sessões nesta versão.\nEsc volta"))
	} else if m.modal == "provider" {
		body.WriteString(bright.Render("Conectar provedor") + "\n\n")
		line := "‹ " + presets[m.presetIndex].Name + " ›"
		if m.focus == 0 {
			line = bright.Render(line)
		}
		body.WriteString("Provedor  " + line + "\n\n")
		labels := []string{"URL base", "API key · somente nesta sessão", "Janela de trabalho · tokens"}
		for i, label := range labels {
			style := muted
			if m.focus == i+1 {
				style = bright
			}
			body.WriteString(style.Render(label) + "\n" + m.fields[i].View() + "\n\n")
		}
		tls := "[ ] TLS sem verificação"
		if m.tls {
			tls = "[x] TLS sem verificação"
		}
		if m.focus == 4 {
			tls = bright.Render(tls)
		}
		body.WriteString(tls + "\n\n")
		button := "Testar catálogo e escolher modelo"
		if m.focus == 5 {
			button = bright.Render("[ " + button + " ]")
		}
		body.WriteString(button + "\n")
		body.WriteString(muted.Render("Consulta sem inferência. A chave não é salva no projeto.\nTab navega · ←→ provedor · Ctrl+S conecta · Esc volta"))
	} else {
		body.WriteString(bright.Render("Escolher modelo") + "\n\n" + m.modelSearch.View() + "\n\n")
		models := m.filteredModels()
		height := max(2, min(10, m.Height-14))
		start := max(0, m.modelIndex-height+1)
		for i := start; i < min(start+height, len(models)); i++ {
			line := shorten(models[i].ID, w-6)
			if models[i].Context > 0 {
				line += fmt.Sprintf(" · %dk", models[i].Context/1024)
			}
			if i == m.modelIndex {
				body.WriteString(bright.Render("› "+line) + "\n")
			} else {
				body.WriteString("  " + line + "\n")
			}
		}
		if len(models) == 0 && !m.loading {
			body.WriteString(muted.Render("Nenhum modelo nesta busca.") + "\n")
		}
		body.WriteString(fmt.Sprintf("\n%d modelos · contexto consultado ao selecionar\n", len(models)))
		body.WriteString(muted.Render("↑↓ navega · digite para filtrar · Enter usa · Esc volta"))
	}
	if m.loading {
		body.WriteString("\n\n" + bright.Render("Consultando API… · Esc cancela"))
	}
	if m.modalError != "" {
		body.WriteString("\n\n" + warning.Render(wrap(shorten(m.modalError, 220), w-4)))
	}
	style := panel
	if m.Height <= 26 {
		style = style.Padding(0, 2)
	}
	return style.Width(w).Render(body.String())
}
