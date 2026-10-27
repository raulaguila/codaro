package llm

import (
	"context"
	"encoding/json"
	"errors"
)

var ErrContext = errors.New("limite de contexto do provedor")

type Function struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
}
type Call struct {
	ID       string   `json:"id"`
	Type     string   `json:"type"`
	Function Function `json:"function"`
}
type Message struct {
	Role       string `json:"role"`
	Content    string `json:"content,omitempty"`
	ToolCalls  []Call `json:"tool_calls,omitempty"`
	ToolCallID string `json:"tool_call_id,omitempty"`
	Name       string `json:"name,omitempty"`
}
type Definition struct {
	Name        string         `json:"name"`
	Description string         `json:"description"`
	Parameters  map[string]any `json:"parameters"`
}
type Tool struct {
	Type     string     `json:"type"`
	Function Definition `json:"function"`
}
type Usage struct {
	Input  int `json:"input"`
	Output int `json:"output"`
}
type Response struct {
	Content   string `json:"content"`
	Reasoning string `json:"reasoning,omitempty"`
	Calls     []Call `json:"tool_calls,omitempty"`
	Finish    string `json:"finish_reason,omitempty"`
	Usage     Usage  `json:"usage"`
}
type Model struct {
	Capabilities []string
	ID           string
	Context      int
	Source       string
}
type Delta struct {
	Content   string
	Reasoning string
}
type Provider interface {
	Catalog(context.Context) ([]Model, error)
	Describe(context.Context, string) (Model, error)
	Chat(context.Context, []Message, []Tool, func(Delta)) (Response, error)
}

func Encode(value any) string { raw, _ := json.Marshal(value); return string(raw) }
