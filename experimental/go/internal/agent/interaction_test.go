package agent

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

func TestInteractiveLoopWaitsForAnswerAndUpdatesPlanThroughNativeProtocol(t *testing.T) {
	p := &fakeProvider{Responses: []llm.Response{call("update_plan", `{"tasks":[{"title":"Investigar","status":"in_progress"}]}`), call("ask_user_question", `{"question":"Qual módulo?","options":["API","CLI"]}`), call("update_plan", `{"tasks":[{"title":"Investigar","status":"completed"}]}`), {Content: "Plano preparado para CLI."}}}
	a := newAgent(t, p)
	a.Interactive = true
	plans, questions := 0, 0
	_, err := a.Run(context.Background(), "Prepare um plano", nil, func(e Event) {
		if e.Kind == "plan" {
			plans++
		}
		if e.Kind == "question" {
			questions++
			e.Reply <- "CLI"
		}
	})
	if err != nil || plans != 2 || questions != 1 {
		t.Fatal(err, plans, questions)
	}
	last := p.Requests[2][len(p.Requests[2])-1]
	if last.Role != "tool" || last.ToolCallID != "test" || !strings.Contains(last.Content, "CLI") {
		t.Fatal(last)
	}
}
func TestCancellingAQuestionUnblocksAgentWithoutCommittingHistory(t *testing.T) {
	p := &fakeProvider{Responses: []llm.Response{call("ask_user_question", `{"question":"Continuar?"}`)}}
	a := newAgent(t, p)
	a.Interactive = true
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	h, err := a.Run(ctx, "Investigue", nil, func(e Event) {
		if e.Kind == "question" {
			cancel()
		}
	})
	if !errors.Is(err, context.Canceled) || h != nil {
		t.Fatal(h, err)
	}
}
func TestPlanValidationRejectsInvalidStatesAndMultipleCurrentTasks(t *testing.T) {
	for _, raw := range []string{`{"tasks":[]}`, `{"tasks":[{"title":"","status":"pending"}]}`, `{"tasks":[{"title":"A","status":"done"}]}`, `{"tasks":[{"title":"A","status":"in_progress"},{"title":"B","status":"in_progress"}]}`} {
		if _, err := ParsePlan(raw); err == nil {
			t.Fatal(raw)
		}
	}
}
func TestExplicitFileReferencesSupplyBoundedRealContentWithToolPairs(t *testing.T) {
	p := &fakeProvider{Responses: []llm.Response{{Content: "main.go define main"}}}
	a := newAgent(t, p)
	_, err := a.Run(context.Background(), "Explique @main.go", nil, func(Event) {})
	if err != nil {
		t.Fatal(err)
	}
	m := p.Requests[0]
	if len(m) != 4 || len(m[2].ToolCalls) != 1 || m[2].ToolCalls[0].ID != m[3].ToolCallID || !strings.Contains(m[3].Content, "func main") {
		t.Fatal(m)
	}
}

func TestQuotedFileReferenceSupportsSpacesAndDoesNotReadEmailAddresses(t *testing.T) {
	p := &fakeProvider{Responses: []llm.Response{{Content: "Arquivo consultado"}}}
	a := newAgent(t, p)
	os.WriteFile(filepath.Join(a.Repository.Path, "space file.go"), []byte("package main\n"), 0600)
	_, err := a.Run(context.Background(), `Contate dev@example.com e leia @"space file.go"`, nil, func(Event) {})
	if err != nil {
		t.Fatal(err)
	}
	messages := p.Requests[0]
	if len(messages) != 4 || !strings.Contains(messages[3].Content, "space file.go") || strings.Contains(messages[3].Content, "example.com") {
		t.Fatal(messages)
	}
}
