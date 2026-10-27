package llm

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
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
				models = append(models, Model{ID: id, Context: n, Source: source})
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
	for _, key := range []string{"context_window", "context_length", "max_model_len", "input_token_limit"} {
		if n, ok := m[key].(float64); ok && n >= 4096 && n <= 2000000 {
			return int(n), "API: " + key
		}
	}
	return 0, "não informado pela API"
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
		Info map[string]any `json:"model_info"`
	}
	if err = readJSON(resp.Body, &doc); err != nil {
		return Model{}, err
	}
	keys := []string{}
	for key := range doc.Info {
		if strings.HasSuffix(key, ".context_length") {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	for _, key := range keys {
		if n, ok := doc.Info[key].(float64); ok && n >= 4096 && n <= 2000000 {
			return Model{ID: id, Context: int(n), Source: "API: model_info." + key}, nil
		}
	}
	return Model{ID: id, Source: "API sem contexto; limite configurado"}, nil
}

// ResolveContext records both the provider's capacity and this experiment's active budget.
func ResolveContext(s config.Settings, m Model) config.Settings {
	s.Model = m.ID
	s.ModelContext = m.Context
	if m.Context > 0 && s.ContextWindow > m.Context {
		s.ContextWindow = m.Context
	}
	s.ContextSource = m.Source
	if s.ContextSource == "" {
		s.ContextSource = "limite configurado; API sem metadados"
	}
	return s
}

func (c *Client) Chat(ctx context.Context, messages []Message, tools []Tool, onDelta func(Delta)) (Response, error) {
	if c.Settings.Provider == "ollama" {
		return c.ollama(ctx, messages, tools, onDelta)
	}
	return c.openai(ctx, messages, tools, onDelta)
}
