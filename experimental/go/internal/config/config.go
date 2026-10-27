// Package config keeps the experiment independent from the Python installation.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type Settings struct {
	Provider      string `json:"provider"`
	BaseURL       string `json:"base_url"`
	Model         string `json:"model"`
	ContextWindow int    `json:"context_window"`
	ContextLimit  int    `json:"context_limit,omitempty"`
	ModelContext  int    `json:"model_context,omitempty"`
	ContextSource string `json:"context_source,omitempty"`
	TLSInsecure   bool   `json:"tls_insecure,omitempty"`
	APIKey        string `json:"-"`
	Mode          string `json:"mode,omitempty"`
}

func Default() Settings {
	return Settings{Provider: "ollama", BaseURL: "http://localhost:11434", ContextWindow: 8192, ContextSource: "fallback: API sem contexto", Mode: "ask"}
}

func Directory() (string, error) {
	if value := os.Getenv("CODARO_GO_CONFIG_DIR"); value != "" {
		return filepath.Abs(value)
	}
	base, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "codaro-go"), nil
}

func Load(dir string) (Settings, error) {
	s := Default()
	raw, err := os.ReadFile(filepath.Join(dir, "config.json"))
	if err == nil {
		if len(raw) > 64*1024 {
			return s, errors.New("configuração excede 64 KiB")
		}
		if err = json.Unmarshal(raw, &s); err != nil {
			return s, fmt.Errorf("configuração inválida: %w", err)
		}
	} else if !os.IsNotExist(err) {
		return s, err
	}
	previousProvider, previousURL, previousModel := s.Provider, s.BaseURL, s.Model
	for name, target := range map[string]*string{"CODARO_GO_PROVIDER": &s.Provider, "CODARO_GO_BASE_URL": &s.BaseURL, "CODARO_GO_MODEL": &s.Model, "CODARO_GO_API_KEY": &s.APIKey, "CODARO_GO_MODE": &s.Mode} {
		if v, ok := os.LookupEnv(name); ok {
			*target = v
		}
	}
	if s.Provider != previousProvider || s.BaseURL != previousURL || s.Model != previousModel {
		s.ModelContext = 0
		s.ContextWindow = 8192
		s.ContextSource = "fallback: modelo alterado; aguardando API"
	}
	if v := os.Getenv("CODARO_GO_CONTEXT_WINDOW"); v != "" {
		n, e := strconv.Atoi(v)
		if e != nil {
			return s, errors.New("CODARO_GO_CONTEXT_WINDOW deve ser inteiro")
		}
		s.ContextWindow = n
		if n == 0 {
			s.ContextWindow = 8192
		}
		s.ContextLimit = n
		s.ContextSource = "override: CODARO_GO_CONTEXT_WINDOW"
	}
	if v := os.Getenv("CODARO_GO_TLS_INSECURE"); v != "" {
		b, e := strconv.ParseBool(v)
		if e != nil {
			return s, e
		}
		s.TLSInsecure = b
	}
	return s, s.Validate()
}

func (s Settings) Validate() error {
	if s.Provider != "ollama" && s.Provider != "openai" && s.Provider != "demo" {
		return errors.New("provedor deve usar protocolo ollama ou openai")
	}
	if s.ContextWindow < 1024 || s.ContextWindow > 2000000 || (s.ContextLimit != 0 && (s.ContextLimit < 1024 || s.ContextLimit > 2000000)) {
		return errors.New("contexto deve ficar entre 1024 e 2000000 tokens; context_limit=0 usa a API")
	}
	if s.Mode != "" && s.Mode != "ask" && s.Mode != "plan" {
		return errors.New("modo deve ser ask ou plan; execução ainda não está habilitada")
	}
	if len(s.APIKey) > 16384 || len(s.Model) > 300 {
		return errors.New("chave ou modelo excede o limite")
	}
	u, err := url.Parse(s.BaseURL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("URL deve ser HTTP/HTTPS, sem credenciais ou query string")
	}
	return nil
}

func Save(dir string, s Settings) error {
	if err := s.Validate(); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	return WritePrivate(filepath.Join(dir, "config.json"), raw)
}

func WritePrivate(path string, raw []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".codaro-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if err = f.Chmod(0600); err == nil {
		_, err = f.Write(raw)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(name, path)
}

func Redact(value string, secret string) string {
	if secret != "" {
		encoded, _ := json.Marshal(secret)
		if len(encoded) > 2 {
			value = strings.ReplaceAll(value, string(encoded[1:len(encoded)-1]), "[credencial omitida]")
		}
		value = strings.ReplaceAll(value, secret, "[credencial omitida]")
	}
	return value
}
