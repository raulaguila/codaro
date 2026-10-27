package llm

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/raulaguila/codaro/experimental/go/internal/config"
)

type transportFunc func(*http.Request) (*http.Response, error)

func (f transportFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func response(body, mime string) *http.Response {
	return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{mime}}, Body: io.NopCloser(strings.NewReader(body))}
}
func event(value any) string { return "data: " + Encode(value) + "\n\n" }

func TestOpenAIStreamingReassemblesToolsAndSeparatesReasoning(t *testing.T) {
	s := config.Default()
	s.Provider = "openai"
	s.BaseURL = "https://example.test/v1"
	s.Model = "model"
	s.APIKey = "secret"
	c := New(s)
	c.HTTP.Transport = transportFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.Path != "/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer secret" {
			t.Fatal("incorrect endpoint/auth")
		}
		var payload map[string]any
		json.NewDecoder(r.Body).Decode(&payload)
		tools := payload["tools"].([]any)
		if tools[0].(map[string]any)["type"] != "function" {
			t.Fatal("wrong OpenAI tool envelope")
		}
		if _, ok := payload["max_tokens"]; ok {
			t.Fatal("output cap injected")
		}
		body := event(map[string]any{"choices": []any{map[string]any{"index": 0, "delta": map[string]any{"reasoning_content": "Vou ler.", "tool_calls": []any{map[string]any{"index": 0, "id": "a", "function": map[string]any{"name": "read_", "arguments": "{\"path\":"}}}}}}})
		body += event(map[string]any{"choices": []any{map[string]any{"index": 0, "delta": map[string]any{"tool_calls": []any{map[string]any{"index": 0, "function": map[string]any{"name": "lines", "arguments": "\"main.go\",\"start\":1,\"end\":2}"}}}}, "finish_reason": "tool_calls"}}}) + "data: [DONE]\n\n"
		return response(body, "text/event-stream"), nil
	})
	var reasoning string
	got, err := c.Chat(context.Background(), []Message{{Role: "user", Content: "Leia"}}, []Tool{{Type: "function", Function: Definition{Name: "read_lines"}}}, func(d Delta) { reasoning += d.Reasoning })
	if err != nil {
		t.Fatal(err)
	}
	if got.Content != "" || reasoning != "Vou ler." || len(got.Calls) != 1 || got.Calls[0].Function.Name != "read_lines" {
		t.Fatalf("bad response: %#v", got)
	}
	var arguments map[string]any
	if err = json.Unmarshal([]byte(got.Calls[0].Function.Arguments), &arguments); err != nil {
		t.Fatal(err)
	}
}

func TestOllamaCatalogMetadataAndNativeProtocol(t *testing.T) {
	s := config.Default()
	s.BaseURL = "http://example.test/v1"
	s.Model = "llama"
	c := New(s)
	c.HTTP.Transport = transportFunc(func(r *http.Request) (*http.Response, error) {
		switch r.URL.Path {
		case "/api/tags":
			return response(`{"models":[{"name":"llama"}]}`, "application/json"), nil
		case "/api/show":
			return response(`{"model_info":{"llama.context_length":131072}}`, "application/json"), nil
		case "/api/chat":
			var payload map[string]any
			json.NewDecoder(r.Body).Decode(&payload)
			if payload["options"].(map[string]any)["num_ctx"] != float64(131072) {
				t.Fatal("active context not sent")
			}
			tool := payload["tools"].([]any)[0].(map[string]any)
			if tool["function"].(map[string]any)["name"] != "list_files" {
				t.Fatal(tool)
			}
			return response("{\"message\":{\"thinking\":\"Pensando\",\"tool_calls\":[{\"function\":{\"name\":\"list_files\",\"arguments\":{\"limit\":2}}}]},\"done\":false}\n{\"message\":{\"content\":\"\"},\"done\":true,\"done_reason\":\"stop\",\"prompt_eval_count\":32,\"eval_count\":4}\n", "application/x-ndjson"), nil
		default:
			t.Fatalf("unexpected endpoint %s", r.URL.Path)
			return nil, nil
		}
	})
	models, err := c.Catalog(context.Background())
	if err != nil || len(models) != 1 {
		t.Fatal(models, err)
	}
	info, err := c.Describe(context.Background(), "llama")
	if err != nil || info.Context != 131072 {
		t.Fatal(info, err)
	}
	resolved := ResolveContext(s, info)
	if resolved.ContextWindow != 131072 || resolved.ModelContext != 131072 {
		t.Fatal(resolved)
	}
	c.Settings = resolved
	got, err := c.Chat(context.Background(), []Message{{Role: "user", Content: "Liste"}}, []Tool{{Type: "function", Function: Definition{Name: "list_files"}}}, func(Delta) {})
	if err != nil || len(got.Calls) != 1 || got.Calls[0].ID == "" || got.Reasoning != "Pensando" || got.Usage.Input != 32 {
		t.Fatal(got, err)
	}
}

func TestErrorStatusAndRedirectAreSafe(t *testing.T) {
	s := config.Default()
	s.Provider = "openai"
	s.BaseURL = "https://example.test/v1"
	c := New(s)
	c.HTTP.Transport = transportFunc(func(*http.Request) (*http.Response, error) {
		r := response(`{"error":"context length exceeded"}`, "application/json")
		r.StatusCode = 400
		return r, nil
	})
	_, err := c.Catalog(context.Background())
	if !errors.Is(err, ErrContext) {
		t.Fatal(err)
	}
	calls := 0
	c.HTTP.Transport = transportFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		out := response("", "application/json")
		out.StatusCode = 302
		out.Header.Set("Location", "https://another.test/models")
		return out, nil
	})
	if _, err = c.Catalog(context.Background()); err == nil || calls != 1 {
		t.Fatal("redirect followed", err, calls)
	}
}

func TestIncompleteAndOversizedStreamsAreRejected(t *testing.T) {
	c := New(config.Default())
	c.HTTP.Transport = transportFunc(func(*http.Request) (*http.Response, error) {
		return response("{\"message\":{\"content\":\"partial\"},\"done\":false}\n", "application/x-ndjson"), nil
	})
	if _, err := c.Chat(context.Background(), nil, nil, func(Delta) {}); err == nil {
		t.Fatal("incomplete stream accepted")
	}
	large := strings.Repeat(": keepalive\n", MaxStreamBytes/12+100)
	if err := scanStream(strings.NewReader(large), true, func(string) error { return nil }); err == nil {
		t.Fatal("oversized stream accepted")
	}
}

func TestTerminalMarkerStopsWithoutWaitingForConnectionClose(t *testing.T) {
	read, write := io.Pipe()
	defer read.Close()
	defer write.Close()
	go func() { write.Write([]byte("data: [DONE]\n\n")) }()
	if err := scanStream(read, true, func(s string) error {
		if s == "[DONE]" {
			return io.EOF
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
