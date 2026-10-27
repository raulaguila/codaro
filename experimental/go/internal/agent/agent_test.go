package agent

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/raulaguila/codaro/experimental/go/internal/config"
	"github.com/raulaguila/codaro/experimental/go/internal/llm"
	"github.com/raulaguila/codaro/experimental/go/internal/tools"
)

type fakeProvider struct {
	Responses []llm.Response
	Requests  [][]llm.Message
	Failure   error
}

func (f *fakeProvider) Catalog(context.Context) ([]llm.Model, error)        { return nil, nil }
func (f *fakeProvider) Describe(context.Context, string) (llm.Model, error) { return llm.Model{}, nil }
func (f *fakeProvider) Chat(ctx context.Context, m []llm.Message, tools []llm.Tool, emit func(llm.Delta)) (llm.Response, error) {
	f.Requests = append(f.Requests, append([]llm.Message(nil), m...))
	if f.Failure != nil {
		e := f.Failure
		f.Failure = nil
		return llm.Response{}, e
	}
	if len(f.Responses) == 0 {
		return llm.Response{}, errors.New("unexpected request")
	}
	out := f.Responses[0]
	f.Responses = f.Responses[1:]
	emit(llm.Delta{Content: out.Content, Reasoning: out.Reasoning})
	return out, nil
}

func newAgent(t *testing.T, p llm.Provider) *Agent {
	t.Helper()
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "main.go"), []byte("package main\nfunc main() {}\n"), 0600)
	repo, err := tools.Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { repo.Close() })
	s := config.Default()
	s.Model = "test"
	return &Agent{Repository: repo, Provider: p, Settings: s, TracePath: filepath.Join(t.TempDir(), "prompt.json")}
}
func call(name, args string) llm.Response {
	return llm.Response{Calls: []llm.Call{{ID: "test", Type: "function", Function: llm.Function{Name: name, Arguments: args}}}}
}

func TestNativeLoopReadsTheSelectedProjectAndWritesRedactedTrace(t *testing.T) {
	p := &fakeProvider{Responses: []llm.Response{call("read_lines", `{"path":"main.go","start":1,"end":2}`), {Content: "main.go declara main. secret-test", Finish: "stop"}}}
	a := newAgent(t, p)
	a.Settings.APIKey = "secret-test"
	var events []Event
	history, err := a.Run(context.Background(), "Explique", nil, func(e Event) { events = append(events, e) })
	if err != nil {
		t.Fatal(err)
	}
	if len(history) != 2 || history[1].Content == "" {
		t.Fatal(history)
	}
	tool := p.Requests[1][len(p.Requests[1])-1]
	if tool.Role != "tool" || !strings.Contains(tool.Content, "func main") {
		t.Fatal(tool)
	}
	if !strings.Contains(p.Requests[0][0].Content, a.Repository.Path) {
		t.Fatal("wrong root")
	}
	raw, err := os.ReadFile(a.TracePath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), a.Settings.APIKey) {
		t.Fatal("secret in trace")
	}
	var trace Trace
	if err = json.Unmarshal(raw, &trace); err != nil {
		t.Fatal(err)
	}
	if trace.Status != "completed" || len(trace.Steps) != 1 || len(trace.Frames) != 2 {
		t.Fatal(trace)
	}
	var final int
	for _, e := range events {
		if e.Kind == "final" {
			final++
		}
	}
	if final != 1 {
		t.Fatal("not exactly one final")
	}
}

func TestToolErrorCanBeRepairedAndGeneralAnswerNeedsNoCitation(t *testing.T) {
	p := &fakeProvider{Responses: []llm.Response{call("read_lines", `{"path":"../secret","start":1,"end":1}`), {Content: "Não consegui ler esse arquivo."}}}
	a := newAgent(t, p)
	if _, err := a.Run(context.Background(), "Leia", nil, func(Event) {}); err != nil {
		t.Fatal(err)
	}
	p = &fakeProvider{Responses: []llm.Response{{Content: "Olá!"}}}
	a.Provider = p
	if _, err := a.Run(context.Background(), "Olá", nil, func(Event) {}); err != nil {
		t.Fatal(err)
	}
}

func TestTextualToolCallGetsProtocolRepairInsteadOfBecomingFinal(t *testing.T) {
	p := &fakeProvider{Responses: []llm.Response{{Content: `{"name":"list_files","arguments":{}}`}, call("list_files", `{}`), {Content: "Encontrei main.go."}}}
	a := newAgent(t, p)
	if _, err := a.Run(context.Background(), "Liste", nil, func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if len(p.Requests) != 3 {
		t.Fatal(len(p.Requests))
	}
}

func TestOrdinaryJSONIsNotMistakenForAToolCall(t *testing.T) {
	answer := `{"name":"Codaro","function":{"example":true}}`
	p := &fakeProvider{Responses: []llm.Response{{Content: answer}}}
	a := newAgent(t, p)
	out, err := a.Run(context.Background(), "Retorne um objeto JSON", nil, func(Event) {})
	if err != nil || out[len(out)-1].Content != answer {
		t.Fatal(out, err)
	}
}

func TestCompactionPreservesCurrentQuestionAndToolPairs(t *testing.T) {
	m := []llm.Message{{Role: "system", Content: "system"}, {Role: "user", Content: strings.Repeat("old", 5000)}, {Role: "assistant", Content: "old answer"}, {Role: "user", Content: "current"}, {Role: "assistant", ToolCalls: call("read_lines", `{}`).Calls}, {Role: "tool", ToolCallID: "test", Content: strings.Repeat("source", 4000)}}
	got, changed := compact(m, nil, 1500)
	if !changed || estimate(got, nil) > 1500 {
		t.Fatal("not compacted")
	}
	if got[1].Content != "current" {
		t.Fatal("current question lost")
	}
	if len(got) != 4 || got[2].ToolCalls[0].ID != got[3].ToolCallID {
		t.Fatal("orphan tool result")
	}
}

func TestContextFailureRetriesAndCancellationDoesNotCommitHistory(t *testing.T) {
	p := &fakeProvider{Failure: llm.ErrContext, Responses: []llm.Response{{Content: "Resposta curta."}}}
	a := newAgent(t, p)
	if _, err := a.Run(context.Background(), "Olá", nil, func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if len(p.Requests) != 2 {
		t.Fatal("did not retry")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	history, err := a.Run(ctx, "Outra pergunta", nil, func(Event) {})
	if !errors.Is(err, context.Canceled) || history != nil {
		t.Fatal(history, err)
	}
	raw, _ := os.ReadFile(a.TracePath)
	var trace Trace
	json.Unmarshal(raw, &trace)
	if trace.Status != "cancelled" {
		t.Fatal(trace.Status)
	}
}

func TestDemoUsesRealToolsWithoutAPI(t *testing.T) {
	a := newAgent(t, llm.Demo{})
	out, err := a.Run(context.Background(), "O que há aqui?", nil, func(Event) {})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out[len(out)-1].Content, "main.go") {
		t.Fatal(out)
	}
}

func TestPlanningModeChangesInstructionsAndTraceWithoutAddingWriteTools(t *testing.T) {
	p := &fakeProvider{Responses: []llm.Response{{Content: "Plano: investigar main.go e definir critérios."}}}
	a := newAgent(t, p)
	a.Settings.Mode = "plan"
	if _, err := a.Run(context.Background(), "Planeje a melhoria", nil, func(Event) {}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(p.Requests[0][0].Content, "Modo Planejar") || !strings.Contains(p.Requests[0][0].Content, "Não implemente") {
		t.Fatal("planning policy missing")
	}
	raw, _ := os.ReadFile(a.TracePath)
	var trace Trace
	json.Unmarshal(raw, &trace)
	if trace.Mode != "plan" {
		t.Fatal(trace.Mode)
	}
	for _, definition := range trace.Frames[0].Tools {
		if strings.Contains(definition.Function.Name, "edit") || strings.Contains(definition.Function.Name, "command") {
			t.Fatal("planning exposes mutations")
		}
	}
}
