//go:build capture

package ui

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

// TestCaptureViews exports the actual renderer with a synthetic, credential-free project.
func TestCaptureViews(t *testing.T) {
	dir := os.Getenv("CODARO_GO_CAPTURE_DIR")
	if dir == "" {
		t.Skip("set CODARO_GO_CAPTURE_DIR")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	profile := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	defer lipgloss.SetColorProfile(profile)
	m := fixture(t)
	m = update(m, tea.WindowSizeMsg{Width: 100, Height: 32})
	capture := func(name string) {
		t.Helper()
		m.refresh()
		if err := os.WriteFile(filepath.Join(dir, name+".ansi"), []byte(m.View()), 0600); err != nil {
			t.Fatal(err)
		}
	}
	capture("01-inicio")
	m.prompt.SetValue("/")
	capture("02-comandos")
	m.prompt.Reset()
	m.openProvider()
	capture("03-provedor")
	m.modal = ""
	m.modal = "models"
	m.models = []llm.Model{{ID: "qwen3:27b", Context: 131072}, {ID: "llama3.1:8b", Context: 131072}, {ID: "qwen2.5-coder:7b", Context: 32768}}
	capture("04-modelos")
	m.modal = ""
	m.start("Explique a estrutura deste projeto.")
	deadline := time.Now().Add(5 * time.Second)
	captured := false
	for m.busy && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
		m = update(m, tick(time.Now()))
		if !captured && m.current().Draft.Len() > 40 {
			capture("05-processo")
			captured = true
		}
	}
	m.Shutdown()
	if m.busy {
		t.Fatal("demo did not complete")
	}
	capture("06-resposta")
}
