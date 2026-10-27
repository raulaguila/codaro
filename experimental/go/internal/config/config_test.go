package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestSettingsStaySeparateAndNeverSaveAPIKey(t *testing.T) {
	dir := t.TempDir()
	s := Default()
	s.APIKey = "test-secret"
	s.Model = "example"
	if err := Save(dir, s); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "config.json"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), s.APIKey) || strings.Contains(string(raw), "api_key") {
		t.Fatal("credential persisted")
	}
	info, _ := os.Stat(filepath.Join(dir, "config.json"))
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0600 {
		t.Fatal(info.Mode())
	}
	t.Setenv("CODARO_GO_API_KEY", "from-env")
	got, err := Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	if got.APIKey != "from-env" || got.Model != "example" {
		t.Fatalf("unexpected config: %#v", got)
	}
}

func TestURLAndContextValidation(t *testing.T) {
	for _, url := range []string{"file:///tmp/model", "http://user:secret@example.com", "http://example.com?api_key=secret", "https://example.com#key"} {
		s := Default()
		s.BaseURL = url
		if s.Validate() == nil {
			t.Errorf("accepted %s", url)
		}
	}
	s := Default()
	s.ContextWindow = 0
	if s.Validate() == nil {
		t.Fatal("zero context accepted")
	}
}

func TestRedactionKeepsJSONValidForQuotedSecret(t *testing.T) {
	secret := "key\"with\\characters"
	raw, _ := json.Marshal(map[string]string{"text": "Do not expose " + secret})
	redacted := Redact(string(raw), secret)
	var doc map[string]string
	if err := json.Unmarshal([]byte(redacted), &doc); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(doc["text"], secret) {
		t.Fatal("secret leaked")
	}
}
