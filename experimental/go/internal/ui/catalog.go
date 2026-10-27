package ui

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

// Profiles never serialize credentials. api_key_env is resolved only at runtime.
type providerProfile struct {
	Name     string          `json:"name"`
	Settings config.Settings `json:"settings"`
	KeyEnv   string          `json:"api_key_env,omitempty"`
}
type preferences struct {
	Favorites []string `json:"favorites,omitempty"`
	Recent    []string `json:"recent,omitempty"`
	Thinking  bool     `json:"show_thinking,omitempty"`
	Details   bool     `json:"show_details,omitempty"`
}
type modelEntry struct {
	Model    llm.Model
	Settings config.Settings
	Provider string
}
type catalogGroup struct {
	Settings config.Settings
	Models   []llm.Model
	Err      string
}
type catalogsResult struct {
	ID     int
	Groups []catalogGroup
}

func providerID(s config.Settings) string         { return s.Provider + "|" + s.BaseURL }
func modelID(s config.Settings, id string) string { return providerID(s) + "|" + id }
func providerLabel(s config.Settings) string {
	for _, p := range presets {
		if p.URL == s.BaseURL {
			return p.Name
		}
	}
	u, _ := url.Parse(s.BaseURL)
	if u != nil && u.Host != "" {
		return u.Host
	}
	return s.Provider
}
func has(values []string, key string) bool {
	for _, v := range values {
		if v == key {
			return true
		}
	}
	return false
}
func boundedRead(path string, target any, limit int64) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	if info.Size() > limit {
		return &os.PathError{Op: "read", Path: path, Err: os.ErrInvalid}
	}
	raw, err := io.ReadAll(io.LimitReader(f, limit+1))
	if err != nil {
		return err
	}
	if int64(len(raw)) > limit {
		return fmt.Errorf("arquivo excede o limite")
	}
	return json.Unmarshal(raw, target)
}
func (m *Model) loadPreferences() {
	_ = boundedRead(filepath.Join(m.Directory, "ui.json"), &m.preferences, 128*1024)
	m.showThinking = m.preferences.Thinking
	m.showDetails = m.preferences.Details
	var profiles []providerProfile
	if err := boundedRead(filepath.Join(m.Directory, "providers.json"), &profiles, 128*1024); err != nil && !os.IsNotExist(err) {
		m.profilesInvalid = true
		m.status = "Cadastro de provedores inválido · confira providers.json"
		return
	}
	if len(profiles) > 20 {
		m.profilesInvalid = true
		m.status = "providers.json: máximo de 20 provedores"
		return
	}
	for _, p := range profiles {
		if p.KeyEnv != "" {
			p.Settings.APIKey = os.Getenv(p.KeyEnv)
		}
		if p.Settings.ContextWindow == 0 {
			p.Settings.ContextWindow = 8192
		}
		if p.Settings.Validate() != nil || p.Settings.Provider == "demo" {
			m.profilesInvalid = true
			m.status = "Provedor inválido em providers.json"
			continue
		}
		m.profiles = append(m.profiles, p)
		if providerID(p.Settings) == providerID(m.Settings) && m.Settings.APIKey == "" {
			m.Settings.APIKey = p.Settings.APIKey
		}
	}
	var groups []catalogGroup
	if boundedRead(filepath.Join(m.Directory, "models.json"), &groups, 1024*1024) == nil && len(groups) <= 20 {
		for _, g := range groups {
			var active config.Settings
			found := false
			if providerID(g.Settings) == providerID(m.Settings) {
				active = m.Settings
				found = true
			} else {
				for _, p := range m.profiles {
					if providerID(p.Settings) == providerID(g.Settings) {
						active = p.Settings
						found = true
						break
					}
				}
			}
			if !found || len(g.Models) > 10000 {
				continue
			}
			g.Settings = active
			for i := range g.Models {
				g.Models[i].Source = "cache: " + g.Models[i].Source
			}
			m.catalogGroups = append(m.catalogGroups, g)
		}
	}
}
func (m *Model) rememberProvider(s config.Settings) {
	if s.Provider == "demo" || m.profilesInvalid {
		return
	}
	m.profilesDirty = true
	for i, p := range m.profiles {
		if providerID(p.Settings) == providerID(s) {
			m.profiles[i].Settings = s
			return
		}
	}
	if len(m.profiles) < 20 {
		m.profiles = append(m.profiles, providerProfile{Name: providerLabel(s), Settings: s})
	}
}
func (m Model) savePreferences() tea.Cmd {
	prefsCopy := m.preferences
	prefsCopy.Thinking = m.showThinking
	prefsCopy.Details = m.showDetails
	prefs, _ := json.MarshalIndent(prefsCopy, "", "  ")
	profiles, _ := json.MarshalIndent(m.profiles, "", "  ")
	cache, _ := json.Marshal(m.catalogGroups)
	dir := m.Directory
	saveProfiles := m.profilesDirty && !m.profilesInvalid
	return func() tea.Msg {
		if err := config.WritePrivate(filepath.Join(dir, "ui.json"), prefs); err != nil {
			return storageResult{Err: err}
		}
		if len(cache) > 2 && len(cache) <= 1024*1024 {
			if err := config.WritePrivate(filepath.Join(dir, "models.json"), cache); err != nil {
				return storageResult{Err: err}
			}
		}
		if !saveProfiles {
			return storageResult{}
		}
		if err := config.WritePrivate(filepath.Join(dir, "providers.json"), profiles); err != nil {
			return storageResult{Err: err}
		}
		return storageResult{}
	}
}
func (m Model) entries() []modelEntry {
	entries := []modelEntry{}
	groups := m.catalogGroups
	if len(groups) == 0 {
		groups = []catalogGroup{{Settings: m.catalogSettings, Models: m.models}}
	}
	query := strings.ToLower(m.modelSearch.Value())
	for _, g := range groups {
		s := g.Settings
		if s.Provider == "" {
			s = m.pending
		}
		if s.Provider == "" {
			s = m.Settings
		}
		name := providerLabel(s)
		for _, profile := range m.profiles {
			if providerID(profile.Settings) == providerID(s) && strings.TrimSpace(profile.Name) != "" {
				name = clean(profile.Name)
				break
			}
		}
		for _, model := range g.Models {
			key := modelID(s, model.ID)
			if m.favoritesOnly && !has(m.preferences.Favorites, key) {
				continue
			}
			if fuzzy(query, name+" "+model.ID) {
				entries = append(entries, modelEntry{model, s, name})
			}
		}
	}
	sort.SliceStable(entries, func(i, j int) bool {
		a, b := entries[i], entries[j]
		if a.Provider != b.Provider {
			return a.Provider < b.Provider
		}
		score := func(e modelEntry) int {
			key := modelID(e.Settings, e.Model.ID)
			if has(m.preferences.Favorites, key) {
				return 0
			}
			if has(m.preferences.Recent, key) {
				return 1
			}
			return 2
		}
		return score(a) < score(b)
	})
	return entries
}
func fuzzy(query, value string) bool {
	q := []rune(strings.ToLower(query))
	if len(q) == 0 {
		return true
	}
	i := 0
	for _, r := range strings.ToLower(value) {
		if r == q[i] {
			i++
			if i == len(q) {
				return true
			}
		}
	}
	return false
}
func (m *Model) loadAllCatalogs() tea.Cmd {
	m.operationID++
	id := m.operationID
	m.loading = len(m.entries()) == 0
	m.catalogRefreshing = true
	m.modal = "models"
	m.modalError = ""
	profiles := append([]providerProfile(nil), m.profiles...)
	found := false
	for i, p := range profiles {
		if providerID(p.Settings) == providerID(m.Settings) {
			profiles[i].Settings = m.Settings
			found = true
		}
	}
	if !found {
		profiles = append(profiles, providerProfile{Settings: m.Settings})
	}
	ctx, cancel := context.WithTimeout(m.workers.Context, 30*time.Second)
	m.operationCancel = cancel
	providers := make([]llm.Provider, len(profiles))
	for i, p := range profiles {
		providers[i] = m.provider(p.Settings)
	}
	return func() tea.Msg {
		defer cancel()
		ch := make(chan catalogGroup, len(profiles))
		for i, p := range profiles {
			go func(s config.Settings, provider llm.Provider) {
				models, err := provider.Catalog(ctx)
				g := catalogGroup{Settings: s, Models: models}
				if err != nil {
					g.Err = config.Redact(err.Error(), s.APIKey)
				}
				ch <- g
			}(p.Settings, providers[i])
		}
		groups := make([]catalogGroup, 0, len(profiles))
		for range profiles {
			select {
			case g := <-ch:
				groups = append(groups, g)
			case <-ctx.Done():
				return catalogsResult{ID: id, Groups: groups}
			}
		}
		return catalogsResult{ID: id, Groups: groups}
	}
}
