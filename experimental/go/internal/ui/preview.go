package ui

import (
	"context"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

type previewRequest struct {
	ID    int
	Entry modelEntry
}
type previewResult struct {
	ID       int
	Settings config.Settings
	Model    llm.Model
	Err      error
}

// Debounce before starting HTTP, so arrow navigation doesn't query every passed row.
func (m *Model) previewSelected() tea.Cmd {
	m.previewID++
	if m.previewCancel != nil {
		m.previewCancel()
		m.previewCancel = nil
	}
	entries := m.entries()
	if len(entries) == 0 {
		return nil
	}
	entry := entries[min(m.modelIndex, len(entries)-1)]
	if entry.Model.Context > 0 && len(entry.Model.Capabilities) > 0 {
		return nil
	}
	id := m.previewID
	return tea.Tick(200*time.Millisecond, func(time.Time) tea.Msg { return previewRequest{ID: id, Entry: entry} })
}
func (m *Model) startPreview(msg previewRequest) tea.Cmd {
	if msg.ID != m.previewID || m.modal != "models" || m.loading {
		return nil
	}
	ctx, cancel := context.WithTimeout(m.workers.Context, 5*time.Second)
	m.previewCancel = cancel
	provider := m.provider(msg.Entry.Settings)
	return func() tea.Msg {
		defer cancel()
		model, err := provider.Describe(ctx, msg.Entry.Model.ID)
		return previewResult{ID: msg.ID, Settings: msg.Entry.Settings, Model: model, Err: err}
	}
}
func (m *Model) acceptPreview(msg previewResult) {
	if msg.ID != m.previewID || m.modal != "models" || msg.Err != nil {
		return
	}
	for i := range m.catalogGroups {
		if providerID(m.catalogGroups[i].Settings) == providerID(msg.Settings) {
			for j := range m.catalogGroups[i].Models {
				if m.catalogGroups[i].Models[j].ID == msg.Model.ID {
					m.catalogGroups[i].Models[j] = msg.Model
				}
			}
		}
	}
	if sameProvider(m.catalogSettings, msg.Settings) {
		for i := range m.models {
			if m.models[i].ID == msg.Model.ID {
				m.models[i] = msg.Model
			}
		}
	}
}
