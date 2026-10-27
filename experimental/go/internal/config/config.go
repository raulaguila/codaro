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
	ModelContext  int    `json:"model_context,omitempty"`
	ContextSource string `json:"context_source,omitempty"`
	TLSInsecure   bool   `json:"tls_insecure,omitempty"`
	APIKey        string `json:"-"`
}

func Default() Settings {
	return Settings{Provider: "ollama", BaseURL: "http://localhost:11434", ContextWindow: 8192, ContextSource: "configuração · 8k"}
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
	for name, target := range map[string]*string{"CODARO_GO_PROVIDER": &s.Provider, "CODARO_GO_BASE_URL": &s.BaseURL, "CODARO_GO_MODEL": &s.Model, "CODARO_GO_API_KEY": &s.APIKey} {
		if v, ok := os.LookupEnv(name); ok {
			*target = v
		}
	}
	if v := os.Getenv("CODARO_GO_CONTEXT_WINDOW"); v != "" {
		n, e := strconv.Atoi(v)
		if e != nil {
			return s, errors.New("CODARO_GO_CONTEXT_WINDOW deve ser inteiro")
		}
		s.ContextWindow = n
		s.ContextSource = "ambiente"
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
	if s.ContextWindow < 4096 || s.ContextWindow > 262144 {
		return errors.New("contexto do protótipo deve ficar entre 4096 e 262144 tokens")
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
