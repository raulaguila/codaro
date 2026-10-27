package llm

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/raulaguila/codaro/experimental/go/internal/config"
)

type Client struct {
	Settings config.Settings
	HTTP     *http.Client
}

func New(s config.Settings) *Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: s.TLSInsecure} // Explicit per-provider opt-in.
	transport.ResponseHeaderTimeout = 120 * time.Second
	return &Client{Settings: s, HTTP: &http.Client{Transport: transport, Timeout: 5 * time.Minute,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return fmt.Errorf("redirecionamento recusado; configure a URL final do provedor")
		}}}
}

func (c *Client) endpoint(path string) string {
	base := strings.TrimRight(c.Settings.BaseURL, "/")
	if c.Settings.Provider == "ollama" {
		base = strings.TrimSuffix(base, "/v1")
		base = strings.TrimSuffix(base, "/api")
		return base + "/api/" + path
	}
	if !strings.HasSuffix(base, "/v1") && !strings.HasSuffix(base, "/openai") {
		base += "/v1"
	}
	return base + "/" + path
}

func (c *Client) request(ctx context.Context, path string, body any) (*http.Response, error) {
	method := http.MethodGet
	var reader io.Reader
	if body != nil {
		raw, e := json.Marshal(body)
		if e != nil {
			return nil, e
		}
		reader = bytes.NewReader(raw)
		method = http.MethodPost
	}
	req, err := http.NewRequestWithContext(ctx, method, c.endpoint(path), reader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if c.Settings.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.Settings.APIKey)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("conexão com o provedor: %s", config.Redact(err.Error(), c.Settings.APIKey))
	}
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return resp, nil
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 16000))
	detail := strings.ToLower(string(raw))
	if resp.StatusCode == 413 || (resp.StatusCode == 400 && (strings.Contains(detail, "context") || strings.Contains(detail, "token limit"))) {
		return nil, ErrContext
	}
	switch resp.StatusCode {
	case 401, 403:
		return nil, fmt.Errorf("HTTP %d: confira a chave e as permissões do provedor", resp.StatusCode)
	case 404:
		return nil, fmt.Errorf("HTTP 404: confira URL, protocolo do provedor e nome do modelo")
	case 429:
		return nil, fmt.Errorf("HTTP 429: limite de uso do provedor; aguarde e tente novamente")
	}
	return nil, fmt.Errorf("provedor retornou HTTP %d", resp.StatusCode)
}

func readJSON(r io.Reader, out any) error {
	raw, err := io.ReadAll(io.LimitReader(r, 4*1024*1024+1))
	if err != nil {
		return err
	}
	if len(raw) > 4*1024*1024 {
		return fmt.Errorf("resposta de catálogo excede 4 MiB")
	}
	return json.Unmarshal(raw, out)
}

func (c *Client) Catalog(ctx context.Context) ([]Model, error) {
	path := "models"
	if c.Settings.Provider == "ollama" {
		path = "tags"
	}
	resp, err := c.request(ctx, path, nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var doc struct {
		Data   []map[string]any `json:"data"`
		Models []struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	if err = readJSON(resp.Body, &doc); err != nil {
		return nil, fmt.Errorf("catálogo inválido: %w", err)
	}
	models := []Model{}
	seen := map[string]bool{}
	if c.Settings.Provider == "ollama" {
		for _, m := range doc.Models {
			models = append(models, Model{ID: m.Name})
		}
	} else {
		for _, m := range doc.Data {
			if id, ok := m["id"].(string); ok {
				n, source := contextMetadata(m)
				models = append(models, Model{ID: id, Context: n, Source: source, Capabilities: compatibleCapabilities(m)})
			}
		}
	}
	valid := models[:0]
	for _, m := range models {
		if m.ID != "" && len(m.ID) < 300 && !seen[m.ID] {
			valid = append(valid, m)
			seen[m.ID] = true
		}
	}
	sort.Slice(valid, func(i, j int) bool { return valid[i].ID < valid[j].ID })
	if len(valid) == 0 {
		return nil, fmt.Errorf("o catálogo não retornou modelos; confira URL e protocolo")
	}
	return valid, nil
}

func contextMetadata(m map[string]any) (int, string) {
	n := 0
	source := "não informado pela API"
	for _, key := range []string{"context_window", "context_length", "max_context_length", "max_model_len", "max_input_tokens", "inputTokenLimit", "input_token_limit"} {
		if value := contextNumber(m[key]); value > 0 {
			n = value
			source = "API: " + key
			break
		}
	}
	if top, ok := m["top_provider"].(map[string]any); ok {
		if limit := contextNumber(top["context_length"]); limit > 0 && (n == 0 || limit < n) {
			n = limit
			source = "API: top_provider.context_length"
		}
	}
	if n == 0 {
		for _, container := range []string{"limits", "metadata"} {
			if nested, ok := m[container].(map[string]any); ok {
				for _, key := range []string{"context_window", "context_length", "max_model_len", "max_input_tokens"} {
					if value := contextNumber(nested[key]); value > 0 {
						return value, "API: " + container + "." + key
					}
				}
			}
		}
	}
	return n, source
}

func contextNumber(value any) int {
	var n float64
	switch v := value.(type) {
	case float64:
		n = v
	case int:
		n = float64(v)
	case string:
		i, err := strconv.Atoi(v)
		if err != nil {
			return 0
		}
		n = float64(i)
	default:
		return 0
	}
	if n < 1024 || n > 2000000 || math.Trunc(n) != n {
		return 0
	}
	return int(n)
}

func (c *Client) Describe(ctx context.Context, id string) (Model, error) {
	if c.Settings.Provider != "ollama" {
		models, err := c.Catalog(ctx)
		if err != nil {
			return Model{}, err
		}
		for _, m := range models {
			if m.ID == id {
				return m, nil
			}
		}
		return Model{}, fmt.Errorf("modelo não encontrado no catálogo")
	}
	resp, err := c.request(ctx, "show", map[string]any{"model": id})
	if err != nil {
		return Model{}, err
	}
	defer resp.Body.Close()
	var doc struct {
		Info         map[string]any `json:"model_info"`
		Capabilities []string       `json:"capabilities"`
		Parameters   any            `json:"parameters"`
	}
	if err = readJSON(resp.Body, &doc); err != nil {
		return Model{}, err
	}
	if value, ok := doc.Parameters.(string); ok {
		match := regexp.MustCompile(`(?m)^\s*num_ctx\s+(\d+)\s*$`).FindStringSubmatch(value)
		if len(match) > 1 {
			if n := contextNumber(match[1]); n > 0 {
				return Model{ID: id, Capabilities: doc.Capabilities, Context: n, Source: "API: parameters.num_ctx → options.num_ctx"}, nil
			}
		}
	}
	if values, ok := doc.Parameters.(map[string]any); ok {
		if n := contextNumber(values["num_ctx"]); n > 0 {
			return Model{ID: id, Capabilities: doc.Capabilities, Context: n, Source: "API: parameters.num_ctx → options.num_ctx"}, nil
		}
	}
	if architecture, ok := doc.Info["general.architecture"].(string); ok {
		if n := contextNumber(doc.Info[architecture+".context_length"]); n > 0 {
			return Model{ID: id, Capabilities: doc.Capabilities, Context: n, Source: "API: model_info." + architecture + ".context_length → options.num_ctx"}, nil
		}
	}
	keys := []string{}
	for key := range doc.Info {
		if strings.HasSuffix(key, ".context_length") {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	limit := 0
	for _, key := range keys {
		if n := contextNumber(doc.Info[key]); n >= 4096 && (limit == 0 || n < limit) {
			limit = n
		}
	}
	if limit > 0 {
		return Model{ID: id, Capabilities: doc.Capabilities, Context: limit, Source: "API: model_info.context_length → options.num_ctx"}, nil
	}
	return Model{ID: id, Capabilities: doc.Capabilities, Source: "fallback: API não informou contexto"}, nil
}

// ResolveContext records both the provider's capacity and this experiment's active budget.
func ResolveContext(s config.Settings, m Model) config.Settings {
	s.Model = m.ID
	s.ModelContext = m.Context
	s.ContextWindow = 8192
	s.ContextSource = "fallback: API não informou contexto"
	if m.Context >= 1024 && m.Context <= 2000000 {
		s.ContextWindow = m.Context
		s.ContextSource = m.Source
		if s.ContextSource == "" {
			s.ContextSource = "API: contexto do modelo"
		}
	}
	if s.ContextLimit > 0 {
		if m.Context == 0 || s.ContextLimit < s.ContextWindow {
			s.ContextWindow = s.ContextLimit
		}
		s.ContextSource = "override explícito · " + s.ContextSource
	}
	return s
}

func (c *Client) Chat(ctx context.Context, messages []Message, tools []Tool, onDelta func(Delta)) (Response, error) {
	if c.Settings.Provider == "ollama" {
		return c.ollama(ctx, messages, tools, onDelta)
	}
	return c.openai(ctx, messages, tools, onDelta)
}

func compatibleCapabilities(metadata map[string]any) []string {
	out := []string{}
	if items, ok := metadata["capabilities"].([]any); ok {
		for _, v := range items {
			if s, ok := v.(string); ok && len(s) < 80 && len(out) < 8 {
				out = append(out, s)
			}
		}
	}
	if params, ok := metadata["supported_parameters"].([]any); ok {
		for _, p := range params {
			if p == "tools" {
				out = append(out, "tools")
				break
			}
		}
	}
	return out
}
