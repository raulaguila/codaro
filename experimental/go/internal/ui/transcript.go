package ui

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
)

func compactNumber(n int) string {
	if n >= 1000000 {
		return fmt.Sprintf("%.1fM", float64(n)/1000000)
	}
	if n >= 1000 {
		return fmt.Sprintf("%.1fk", float64(n)/1000)
	}
	return fmt.Sprint(n)
}

func (m *Model) refresh() {
	width := max(16, m.Width-4)
	rows := 0
	for _, line := range strings.Split(m.prompt.Value(), "\n") {
		rows += max(1, (ansi.StringWidth(line)+max(1, width-2)-1)/max(1, width-2))
	}
	m.prompt.SetHeight(min(3, max(1, rows)))
	m.viewport.Width = width
	m.viewport.Height = max(2, m.Height-3-lipgloss.Height(m.menuView()+m.composerView()+m.footer()))
	var b strings.Builder
	m.processHits = nil
	if len(m.turns) == 0 {
		b.WriteString("\n" + bright.Render("O que vamos explorar?") + "\n\n" + muted.Render("Pergunte sobre o projeto ou descreva uma atividade.") + "\n")
		if m.Demo {
			b.WriteString("\n" + warning.Render("Demonstração local · sem chamadas a modelos de IA."))
		}
	} else {
		for index, t := range m.turns {
			b.WriteString(muted.Render("Você") + "\n" + wrap(clean(t.Question), width) + "\n\n")
			if len(t.Steps) > 0 {
				line := strings.Count(b.String(), "\n")
				m.processHits = append(m.processHits, processHit{Line: line, Turn: index})
				if t == m.current() {
					m.processLine = line
				}
				expanded := t.ProcessOpen
				if t == m.current() {
					expanded = m.expanded
				}
				marker := "▸"
				if expanded {
					marker = "▾"
				}
				elapsed := time.Since(t.Started)
				if t.Done && !t.Finished.IsZero() {
					elapsed = t.Finished.Sub(t.Started)
				}
				title := fmt.Sprintf("%s Ver processo · %d etapas", marker, len(t.Steps))
				if t.Done {
					title += fmt.Sprintf(" · %ds", max(0, int(elapsed.Seconds())))
				}
				b.WriteString(muted.Render(title) + "\n")
				if expanded {
					for i := 0; i < len(t.Steps); i++ {
						s := t.Steps[i]
						style := muted
						if s.Failed {
							style = warning
						}
						if s.Kind == "tool" && !m.showDetails && !s.Failed && s.Body != "" {
							j := i + 1
							for j < len(t.Steps) && t.Steps[j].Kind == "tool" && t.Steps[j].Name == s.Name && !t.Steps[j].Failed && t.Steps[j].Body != "" {
								j++
							}
							if j-i > 1 {
								label := fmt.Sprintf("%s · %d ações", s.Title, j-i)
								switch s.Name {
								case "read_lines":
									label = fmt.Sprintf("Leu %d arquivos", j-i)
								case "search_code":
									label = fmt.Sprintf("Fez %d buscas", j-i)
								case "list_files":
									label = fmt.Sprintf("Listou %d páginas", j-i)
								}
								b.WriteString(style.Render(wrap("  ↳ "+label, width)) + "\n")
								i = j - 1
								continue
							}
						}
						if s.Kind == "reasoning" {
							b.WriteString(muted.Render("  "+s.Title+" · /thinking") + "\n")
							if m.showThinking {
								b.WriteString(muted.Render(wrap(shorten(s.Body, 6000), width)) + "\n")
							}
							continue
						}
						if s.Kind == "text" {
							b.WriteString(wrap(shorten(s.Body, 3000), width) + "\n\n")
							continue
						}
						prefix := "  "
						if s.Kind == "tool" {
							prefix += "↳ "
						}
						if s.Kind == "decision" {
							prefix = "  Sua decisão · "
						}
						b.WriteString(style.Render(wrap(prefix+s.Title, width)) + "\n")
						body := s.Body
						if s.Kind == "tool" && !m.showDetails {
							body = s.Summary
						}
						if body != "" {
							b.WriteString(style.Render(wrap(shorten(body, 6000), width)) + "\n")
						}
					}
				}
				b.WriteString("\n")
			}
			if t.Final != "" {
				if t.Rendered == "" || t.Width != width {
					t.Rendered = markdown(shorten(t.Final, 64000), width)
					if len([]rune(t.Final)) > 64000 {
						t.Rendered += "\n\n" + muted.Render("Resposta longa · /export salva o conteúdo completo")
					}
					t.Width = width
				}
				b.WriteString(bright.Render("Codaro") + "\n" + t.Rendered + "\n\n")
			} else if t.Draft.Len() > 0 {
				b.WriteString(bright.Render("Codaro · gerando") + "\n" + wrap(shorten(t.Draft.String(), 24000), width) + "\n\n")
			}
		}
	}
	if len(m.queued) > 0 {
		b.WriteString(muted.Render("Na fila") + "\n")
		for _, q := range m.queued {
			b.WriteString(muted.Render("  › "+ansi.Truncate(strings.ReplaceAll(clean(q), "\n", " "), max(10, width-4), "…")) + "\n")
		}
	}
	oldOffset := m.viewport.YOffset
	m.viewport.SetContent(b.String())
	if m.follow {
		m.viewport.GotoBottom()
	} else {
		m.viewport.SetYOffset(oldOffset)
	}
	m.dirty = false
}

func (m *Model) toggleProcess() {
	m.expanded = !m.expanded
	m.manualExpanded = true
	m.dirty = true
	if m.expanded {
		m.follow = false
	}
	m.refresh()
	if m.expanded {
		m.viewport.SetYOffset(m.processLine)
	}
}
