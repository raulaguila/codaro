package tools

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

func fixture(t *testing.T) *Repository {
	t.Helper()
	dir := t.TempDir()
	for name, body := range map[string]string{"main.go": "package main\nfunc main() {}\n", ".env": "PASSWORD=hidden", ".gitignore": "private.go\n", "private.go": "secret", ".codaroignore": "ignored.go\n", "ignored.go": "hidden"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	r, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.Close() })
	return r
}
func execute(t *testing.T, r *Repository, name string, args map[string]any) map[string]any {
	t.Helper()
	raw, _ := json.Marshal(args)
	out, err := r.Execute(context.Background(), llm.Call{Function: llm.Function{Name: name, Arguments: string(raw)}})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestToolsUseSelectedRootAndRespectIgnores(t *testing.T) {
	r := fixture(t)
	info := execute(t, r, "get_repository_info", map[string]any{})
	if info["root"] != r.Path {
		t.Fatal(info)
	}
	listed := execute(t, r, "list_files", map[string]any{})
	raw := llm.Encode(listed)
	for _, name := range []string{"private.go", "ignored.go", ".env"} {
		if strings.Contains(raw, name) {
			t.Fatal("ignored file listed: ", raw)
		}
	}
	for _, name := range []string{"../outside.go", "/etc/passwd", "private.go", ".env", "ignored.go"} {
		_, err := r.Execute(context.Background(), llm.Call{Function: llm.Function{Name: "read_lines", Arguments: llm.Encode(map[string]any{"path": name, "start": 1, "end": 1})}})
		if err == nil {
			t.Fatalf("read forbidden %q", name)
		}
	}
	result := execute(t, r, "read_lines", map[string]any{"path": "main.go", "start": "1", "end": "2"})
	if !strings.Contains(result["content"].(string), "func main") {
		t.Fatal(result)
	}
}

func TestReadCursorPreservesLongUnicodeLines(t *testing.T) {
	r := fixture(t)
	source := strings.Repeat("ação 🎯 ", 1000) + "\nend\n"
	if err := os.WriteFile(filepath.Join(r.Path, "long.go"), []byte(source), 0600); err != nil {
		t.Fatal(err)
	}
	line, column := 1, 0
	var recovered strings.Builder
	for i := 0; i < 20; i++ {
		page := execute(t, r, "read_lines", map[string]any{"path": "long.go", "start": line, "end": 2, "column": column})
		recovered.WriteString(page["content"].(string))
		if !page["truncated"].(bool) {
			break
		}
		nextLine, nextColumn := page["next_start_line"].(int), page["next_column"].(int)
		if line == nextLine && column == nextColumn {
			t.Fatal("cursor did not progress")
		}
		line, column = nextLine, nextColumn
	}
	if recovered.String() != source {
		t.Fatalf("cursor lost content: %d != %d", recovered.Len(), len(source))
	}
}

func TestLinksAndBinaryFilesCannotBeRead(t *testing.T) {
	r := fixture(t)
	if err := os.WriteFile(filepath.Join(r.Path, "binary.go"), []byte{0, 1, 2}, 0600); err != nil {
		t.Fatal(err)
	}
	_, err := r.read(context.Background(), "binary.go")
	if err == nil {
		t.Fatal("binary accepted")
	}
	if runtime.GOOS != "windows" {
		outside := filepath.Join(t.TempDir(), "outside.go")
		os.WriteFile(outside, []byte("outside"), 0600)
		if err = os.Symlink(outside, filepath.Join(r.Path, "link.go")); err != nil {
			t.Fatal(err)
		}
		if _, err = r.read(context.Background(), "link.go"); err == nil {
			t.Fatal("external symlink read")
		}
	}
}

func TestSearchAndFilePagination(t *testing.T) {
	r := fixture(t)
	result := execute(t, r, "search_code", map[string]any{"query": "main", "limit": 1})
	if result["next_offset"] != 1 {
		t.Fatal(result)
	}
	next := execute(t, r, "search_code", map[string]any{"query": "main", "offset": 1, "limit": 1})
	if next["results"].([]map[string]any)[0]["line"] != 2 {
		t.Fatal(next)
	}
	page := execute(t, r, "list_files", map[string]any{"limit": 1})
	if page["next_offset"] != 1 {
		t.Fatal(page)
	}
	cancel, c := context.WithCancel(context.Background())
	c()
	if _, err := r.Files(cancel); err != context.Canceled {
		t.Fatalf("cancel: %v", err)
	}
}
