package tools

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestDiffReadsTrackedChangesWithoutExecutingExternalDiff(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git unavailable")
	}
	dir := t.TempDir()
	run := func(args ...string) {
		t.Helper()
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatal(err, string(out))
		}
	}
	run("init", "-q")
	os.WriteFile(filepath.Join(dir, "main.go"), []byte("package main\n"), 0600)
	run("add", "main.go")
	run("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial")
	run("config", "diff.external", "command-that-must-not-run")
	os.WriteFile(filepath.Join(dir, "main.go"), []byte("package changed\n"), 0600)
	r, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	diff, err := r.Diff(context.Background())
	if err != nil || !strings.Contains(diff, "+package changed") {
		t.Fatal(diff, err)
	}
}
