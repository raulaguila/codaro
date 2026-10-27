package ui

import (
	"context"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

func sameProvider(a, b config.Settings) bool {
	return a.Provider == b.Provider && a.BaseURL == b.BaseURL && a.APIKey == b.APIKey && a.TLSInsecure == b.TLSInsecure
}

func (m Model) metadataCmd() tea.Cmd {
	id, s := m.metadataID, m.Settings
	provider := m.provider(s)
	root := m.workers.Context
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(root, 15*time.Second)
		defer cancel()
		model, err := provider.Describe(ctx, s.Model)
		return metadataResult{ID: id, Settings: s, Model: model, Err: err}
	}
}

func (m *Model) acceptMetadata(msg metadataResult) tea.Cmd {
	if msg.ID != m.metadataID || !sameProvider(msg.Settings, m.Settings) || msg.Settings.Model != m.Settings.Model {
		return nil
	}
	m.metadataLoading = false
	if msg.Err != nil || msg.Model.Context == 0 {
		if m.Settings.ModelContext > 0 {
			m.Settings = llm.ResolveContext(m.Settings, llm.Model{ID: m.Settings.Model, Context: m.Settings.ModelContext, Source: "cache: contexto obtido anteriormente da API"})
			m.status = "Pronto · contexto em cache; API sem atualização"
		} else {
			m.Settings = llm.ResolveContext(m.Settings, llm.Model{ID: m.Settings.Model})
			m.status = "Pronto · API sem contexto; usando fallback identificado em /status"
		}
	} else {
		m.Settings = llm.ResolveContext(m.Settings, msg.Model)
		m.status = "Pronto"
	}
	if msg.Err == nil && msg.Model.Context > 0 {
		m.cacheModel(msg.Model)
		if err := config.Save(m.Directory, m.Settings); err != nil {
			m.status = "Pronto · contexto atualizado apenas nesta sessão"
		}
	}
	m.dirty = true
	question := m.deferredQuestion
	m.deferredQuestion = ""
	if question != "" && question == strings.TrimSpace(m.prompt.Value()) && m.modal == "" && !m.busy {
		m.start(question)
	}
	return nil
}

func (m *Model) openModels() tea.Cmd {
	if m.busy {
		m.status = "Aguarde a conclusão ou cancele antes de trocar o modelo"
		return nil
	}
	m.pending = m.Settings
	m.modal = "models"
	m.modalError = ""
	m.modelSearch.Reset()
	m.modelSearch.Focus()
	m.modelIndex = 0
	if len(m.catalogGroups) == 0 && len(m.models) > 0 && sameProvider(m.catalogSettings, m.Settings) {
		m.catalogGroups = []catalogGroup{{Settings: m.Settings, Models: m.models}}
	}
	for i, e := range m.entries() {
		if e.Model.ID == m.Settings.Model && providerID(e.Settings) == providerID(m.Settings) {
			m.modelIndex = i
			break
		}
	}
	return tea.Batch(textinput.Blink, m.loadAllCatalogs(), m.previewSelected())
}

func (m *Model) cacheModel(model llm.Model) {
	for i := range m.catalogGroups {
		if providerID(m.catalogGroups[i].Settings) == providerID(m.Settings) {
			for j := range m.catalogGroups[i].Models {
				if m.catalogGroups[i].Models[j].ID == model.ID {
					m.catalogGroups[i].Models[j] = model
				}
			}
		}
	}
	if !sameProvider(m.catalogSettings, m.Settings) {
		return
	}
	for i := range m.models {
		if m.models[i].ID == model.ID {
			m.models[i] = model
			return
		}
	}
}
