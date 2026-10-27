package llm

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/raulaguila/codaro/experimental/go/internal/config"
)

func TestContextUsesAPIUnlessExplicitlyLimited(t *testing.T) {
	for _, test := range []struct {
		name             string
		api, limit, want int
	}{
		{"automatic", 131072, 0, 131072}, {"override", 131072, 16384, 16384},
		{"server_cap", 8192, 32768, 8192}, {"fallback", 0, 0, 8192}, {"explicit_without_metadata", 0, 65536, 65536},
		{"small", 2048, 0, 2048}, {"large", 1048576, 0, 1048576},
	} {
		t.Run(test.name, func(t *testing.T) {
			s := config.Default()
			s.ContextLimit = test.limit
			got := ResolveContext(s, Model{ID: "test", Context: test.api, Source: "API: context_length"})
			if got.ContextWindow != test.want || got.ModelContext != test.api {
				t.Fatal(got)
			}
			if test.api == 0 && test.limit == 0 && !strings.Contains(got.ContextSource, "fallback") {
				t.Fatal("unlabeled fallback")
			}
		})
	}
}

func TestOllamaMetadataPrefersLanguageArchitectureAndModelParameters(t *testing.T) {
	for _, test := range []struct {
		name, body string
		want       int
		source     string
	}{
		{"vision", `{"model_info":{"general.architecture":"qwen3vl","qwen3vl.context_length":131072,"clip.context_length":8192}}`, 131072, "qwen3vl"},
		{"parameters", `{"parameters":"temperature 0.7\nnum_ctx 32768\n","model_info":{"llama.context_length":131072}}`, 32768, "parameters.num_ctx"},
		{"object_parameters", `{"parameters":{"num_ctx":65536}}`, 65536, "parameters.num_ctx"},
		{"missing", `{"model_info":{"clip.context_length":512}}`, 0, "fallback"},
	} {
		t.Run(test.name, func(t *testing.T) {
			c := New(config.Default())
			c.HTTP.Transport = transportFunc(func(*http.Request) (*http.Response, error) { return response(test.body, "application/json"), nil })
			got, err := c.Describe(context.Background(), "test")
			if err != nil || got.Context != test.want || !strings.Contains(got.Source, test.source) {
				t.Fatal(got, err)
			}
		})
	}
}

func TestCompatibleMetadataFieldsAndServerLimits(t *testing.T) {
	for _, key := range []string{"context_length", "context_window", "max_context_length", "max_model_len", "max_input_tokens", "inputTokenLimit", "input_token_limit"} {
		t.Run(key, func(t *testing.T) {
			s := config.Default()
			s.Provider = "openai"
			c := New(s)
			c.HTTP.Transport = transportFunc(func(*http.Request) (*http.Response, error) {
				return response(Encode(map[string]any{"data": []any{map[string]any{"id": "test", key: 131072}}}), "application/json"), nil
			})
			got, err := c.Describe(context.Background(), "test")
			if err != nil || got.Context != 131072 || !strings.Contains(got.Source, key) {
				t.Fatal(got, err)
			}
		})
	}
	n, source := contextMetadata(map[string]any{"context_length": float64(131072), "top_provider": map[string]any{"context_length": float64(65536)}})
	if n != 65536 || source != "API: top_provider.context_length" {
		t.Fatal(n, source)
	}
	for _, bad := range []any{true, -1, 3.5, 0, "not-a-number", float64(3000000)} {
		if contextNumber(bad) != 0 {
			t.Fatal("invalid context accepted", bad)
		}
	}
}
