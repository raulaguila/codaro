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
	m.fields[2].Placeholder = "Automático · API do modelo"
	if m.Settings.ContextLimit > 0 {
		m.fields[2].SetValue(strconv.Itoa(m.Settings.ContextLimit))
	}
}

func (m *Model) loadCatalog(s config.Settings) tea.Cmd {
	m.operationID++
	id := m.operationID
	ctx, cancel := context.WithTimeout(m.workers.Context, 30*time.Second)
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
	n := 0
	var err error
	if value := strings.TrimSpace(m.fields[2].Value()); value != "" {
		n, err = strconv.Atoi(value)
	}
	if err != nil {
		m.modalError = "A janela de trabalho deve ser um número inteiro"
		return nil
	}
	s := config.Default()
	s.Provider = presets[m.presetIndex].Kind
	s.BaseURL = strings.TrimSpace(m.fields[0].Value())
	s.APIKey = strings.TrimSpace(m.fields[1].Value())
	s.ContextLimit = n
	s.TLSInsecure = m.tls
	s.Mode = m.Settings.Mode
	if n > 0 {
		s.ContextWindow = n
	}
	if err = s.Validate(); err != nil {
		m.modalError = err.Error()
		return nil
	}
	return m.loadCatalog(s)
}

func (m *Model) chooseModel() tea.Cmd {
	if m.operationCancel != nil {
		m.operationCancel()
	}
	m.catalogRefreshing = false
	models := m.filteredModels()
	if len(models) == 0 {
		return nil
	}
	entry := m.entries()[min(m.modelIndex, len(models)-1)]
	selected := entry.Model
	s := entry.Settings
	s.Mode = m.Settings.Mode
	m.operationID++
	id := m.operationID
	ctx, cancel := context.WithTimeout(m.workers.Context, 30*time.Second)
	m.operationCancel = cancel
	m.loading = true
	m.modalError = ""
	provider := m.provider(s)
	return func() tea.Msg {
		defer cancel()
		info, err := provider.Describe(ctx, selected.ID)
		if err != nil && ctx.Err() == nil {
			info = selected
			if info.Source == "" {
				info.Source = "fallback: detalhes indisponíveis na API"
			}
			err = nil
		}
		return selectedResult{id, s, info, err}
	}
}

func (m Model) filteredModels() []llm.Model {
	out := []llm.Model{}
	for _, entry := range m.entries() {
		out = append(out, entry.Model)
	}
	return out
}

func (m *Model) updateModal(msg tea.KeyMsg) tea.Cmd {
	key := msg.String()
	if key == "esc" {
		m.previewID++
		if m.previewCancel != nil {
			m.previewCancel()
			m.previewCancel = nil
		}
		if m.operationCancel != nil {
			m.operationCancel()
		}
		m.operationID++
		m.loading = false
		m.catalogRefreshing = false
		m.operationCancel = nil
		m.modal = ""
		m.modalError = ""
		m.dirty = true
		return m.prompt.Focus()
	}
	if handled, cmd := m.updateExtraDialog(msg); handled {
		return cmd
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
	if m.modal == "modes" {
		switch key {
		case "up":
			m.modeIndex = (m.modeIndex + len(modes) - 1) % len(modes)
		case "down":
			m.modeIndex = (m.modeIndex + 1) % len(modes)
		case "enter":
			m.applyMode(modes[m.modeIndex].ID)
		}
		return nil
	}
	if m.modal == "models" {
		models := m.filteredModels()
		switch key {
		case "up":
			if len(models) > 0 {
				m.modelIndex = (m.modelIndex + len(models) - 1) % len(models)
			}
			return m.previewSelected()
		case "down":
			if len(models) > 0 {
				m.modelIndex = (m.modelIndex + 1) % len(models)
			}
			return m.previewSelected()
		case "enter":
			return m.chooseModel()
		case "ctrl+r":
			return m.loadAllCatalogs()
		case "ctrl+f":
			m.favoritesOnly = !m.favoritesOnly
			m.modelIndex = 0
			return nil
		case "ctrl+s":
			entries := m.entries()
			if len(entries) == 0 {
				return nil
			}
			e := entries[min(m.modelIndex, len(entries)-1)]
			key := modelID(e.Settings, e.Model.ID)
			if has(m.preferences.Favorites, key) {
				out := []string{}
				for _, v := range m.preferences.Favorites {
					if v != key {
						out = append(out, v)
					}
				}
				m.preferences.Favorites = out
			} else {
				m.preferences.Favorites = append(m.preferences.Favorites, key)
			}
			m.modelIndex = 0
			return m.savePreferences()
		}
		var cmd tea.Cmd
		m.modelSearch, cmd = m.modelSearch.Update(msg)
		m.modelIndex = 0
		return tea.Batch(cmd, m.previewSelected())
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
	if extra, ok := m.extraDialog(w); ok {
		body.WriteString(extra)
	} else if m.modal == "modes" {
		body.WriteString(bright.Render("Como você quer trabalhar?") + "\n\n")
		for i := range modes {
			body.WriteString(m.modeRow(i, w) + "\n\n")
		}
		body.WriteString(muted.Render("Clique ou ↑↓ · Enter seleciona · Esc volta"))
	} else if m.modal == "status" {
		body.WriteString(bright.Render("Sessão do protótipo") + "\n\n")
		body.WriteString(wrap(fmt.Sprintf("Diretório: %s\nModelo: %s\nModo: %s\nContexto ativo: %d tokens\nContexto informado pela API: %d\nLimite manual (0 = automático): %d\nOrigem: %s\n\nÚltimo fluxo JSON:\n%s", m.Repo.Path, m.Settings.Model, modeLabel(m.Settings.Mode), m.Settings.ContextWindow, m.Settings.ModelContext, m.Settings.ContextLimit, m.Settings.ContextSource, agent.TracePath(m.Directory, m.Repo.Path)), w-6))
		body.WriteString("\n\n" + muted.Render("Esc volta · estimativas conservadoras de contexto"))
	} else if m.modal == "help" {
		body.WriteString(bright.Render("Codaro Go · experimento") + "\n\n")
		body.WriteString("Ctrl+P    Menu de comandos\nCtrl+L    Escolher modelo\nShift+Tab Alternar modo\nCtrl+T    Ver processo\nCtrl+B    Recolher ou expandir plano\nCtrl+R    Buscar mensagens\nCtrl+X    Cancelar atividade\nCtrl+Q    Sair\n\n@ arquivo · /sessions conversas · /diff alterações\n\n")
		body.WriteString(muted.Render("Somente leitura · Esc volta"))
	} else if m.modal == "provider" {
		body.WriteString(bright.Render("Conectar provedor") + "\n\n")
		line := "‹ " + presets[m.presetIndex].Name + " ›"
		if m.focus == 0 {
			line = bright.Render(line)
		}
		body.WriteString("Provedor  " + line + "\n\n")
		labels := []string{"URL base", "API key · somente nesta sessão", "Contexto · vazio usa a API"}
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
		for _, row := range m.modelRows() {
			body.WriteString(row.Text + "\n")
		}

		if len(models) == 0 && !m.loading {
			body.WriteString(muted.Render("Nenhum modelo nesta busca.") + "\n")
		}
		source := "contexto consultado ao selecionar"
		if len(models) > 0 {
			entry := m.entries()[min(m.modelIndex, len(models)-1)]
			selected := entry.Model
			if selected.Context > 0 {
				source = fmt.Sprintf("Contexto %s", compactNumber(selected.Context))
				if strings.HasPrefix(selected.Source, "cache:") {
					source += " (cache)"
				}
				if len(selected.Capabilities) > 0 {
					source += " · " + strings.Join(selected.Capabilities, ", ")
				} else {
					source += " · capacidades não informadas"
				}
				if source == "" {
					source = "contexto informado pelo provedor"
				}
			}
		}
		body.WriteString(fmt.Sprintf("\n%d modelos · %s\n", len(models), shorten(source, max(15, w-22))))
		body.WriteString(muted.Render("Enter usa · Ctrl+S ★ · Ctrl+F favoritos · Ctrl+R atualiza"))
	}
	if m.catalogRefreshing && !m.loading {
		body.WriteString("\n" + muted.Render("Atualizando catálogo… · seleção disponível"))
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
