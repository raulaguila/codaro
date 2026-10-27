// Package tools exposes bounded, read-only operations on the selected repository.
package tools

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/raulaguila/codaro/experimental/go/internal/llm"
)

const maxFileBytes = 512000

type Repository struct {
	Path string
	root *os.Root
}

func Open(path string) (*Repository, error) {
	resolved, err := filepath.Abs(path)
	if err != nil {
		return nil, err
	}
	resolved, err = filepath.EvalSymlinks(resolved)
	if err != nil {
		return nil, err
	}
	root, err := os.OpenRoot(resolved)
	if err != nil {
		return nil, err
	}
	return &Repository{Path: resolved, root: root}, nil
}
func (r *Repository) Close() error { return r.root.Close() }

type cappedBuffer struct {
	bytes.Buffer
	limit int
}

func (b *cappedBuffer) Write(p []byte) (int, error) {
	if b.Len()+len(p) > b.limit {
		return 0, errors.New("listagem excede o limite")
	}
	return b.Buffer.Write(p)
}

func (r *Repository) Files(ctx context.Context) ([]string, error) {
	args := []string{"--files", "--null", "--hidden", "--no-require-git", "--glob", "!.git/**", "--glob", "!.codaro/**", "--glob", "!.venv/**", "--glob", "!node_modules/**", "--glob", "!dist/**", "--glob", "!build/**", "--glob", "!**/bin/**", "--glob", "!**/__pycache__/**", "--glob", "!**/.env*", "--glob", "!**/*.pem", "--glob", "!**/*.key", "--glob", "!**/*.p12", "--glob", "!**/.aws/**", "--glob", "!**/.ssh/**", "--glob", "!**/credentials*", "--glob", "!**/secrets*", "--glob", "!**/*.sqlite*"}
	if info, err := r.root.Stat(".codaroignore"); err == nil && info.Mode().IsRegular() {
		args = append(args, "--ignore-file", ".codaroignore")
	}
	cmd := exec.CommandContext(ctx, "rg", args...)
	cmd.Dir = r.Path
	var output cappedBuffer
	output.limit = 4 * 1024 * 1024
	cmd.Stdout = &output
	cmd.Stderr = io.Discard
	err := cmd.Run()
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if err != nil {
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 1 {
			return nil, fmt.Errorf("não foi possível listar o projeto; instale ripgrep (rg): %w", err)
		}
	}
	paths := []string{}
	for _, raw := range bytes.Split(output.Bytes(), []byte{0}) {
		if len(raw) == 0 {
			continue
		}
		name := filepath.ToSlash(string(raw))
		if !safeName(name) {
			continue
		}
		info, e := r.root.Lstat(filepath.FromSlash(name))
		if e == nil && info.Mode().IsRegular() && info.Size() <= maxFileBytes {
			paths = append(paths, name)
		}
		if len(paths) > 20000 {
			return nil, fmt.Errorf("projeto excede 20 mil arquivos; refine .codaroignore")
		}
	}
	sort.Strings(paths)
	return paths, nil
}

func safeName(name string) bool {
	if name == "" || strings.ContainsAny(name, "\x00\r\n\\") || filepath.IsAbs(name) {
		return false
	}
	for _, part := range strings.Split(name, "/") {
		if part == ".." || part == "." || strings.HasPrefix(strings.ToLower(part), ".env") {
			return false
		}
	}
	return true
}

func (r *Repository) read(ctx context.Context, name string) ([]byte, error) {
	if !safeName(name) {
		return nil, errors.New("use um caminho relativo retornado por list_files")
	}
	files, err := r.Files(ctx)
	if err != nil {
		return nil, err
	}
	if !slices.Contains(files, name) {
		return nil, errors.New("arquivo ignorado, indisponível ou não permitido; use list_files")
	}
	return r.readKnown(ctx, name)
}

// readKnown reads a member of this tool invocation's freshly discovered policy snapshot.
func (r *Repository) readKnown(ctx context.Context, name string) ([]byte, error) {
	parts := strings.Split(name, "/")
	check := func() error {
		for i := range parts {
			info, e := r.root.Lstat(filepath.FromSlash(strings.Join(parts[:i+1], "/")))
			if e != nil {
				return e
			}
			if info.Mode()&os.ModeSymlink != 0 {
				return errors.New("links simbólicos não são lidos pelo protótipo")
			}
		}
		return nil
	}
	if err := check(); err != nil {
		return nil, err
	}
	f, err := r.root.OpenFile(filepath.FromSlash(name), os.O_RDONLY|readFlags, 0)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	before, err := f.Stat()
	if err != nil {
		return nil, err
	}
	if !before.Mode().IsRegular() || before.Size() > maxFileBytes {
		return nil, errors.New("somente arquivos regulares de até 512 KB")
	}
	raw, err := io.ReadAll(io.LimitReader(f, maxFileBytes+1))
	if err != nil {
		return nil, err
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if len(raw) > maxFileBytes {
		return nil, errors.New("arquivo cresceu além do limite")
	}
	after, err := r.root.Lstat(filepath.FromSlash(name))
	if err != nil {
		return nil, err
	}
	if err = check(); err != nil {
		return nil, err
	}
	if !os.SameFile(before, after) || !before.ModTime().Equal(after.ModTime()) || before.Size() != after.Size() {
		return nil, errors.New("arquivo mudou durante a leitura; tente novamente")
	}
	if !utf8.Valid(raw) || bytes.IndexByte(raw, 0) >= 0 {
		return nil, errors.New("arquivo binário ou texto sem UTF-8")
	}
	return raw, nil
}

func definition(name, description string, properties map[string]any, required ...string) llm.Tool {
	if required == nil {
		required = []string{}
	}
	return llm.Tool{Type: "function", Function: llm.Definition{Name: name, Description: description, Parameters: map[string]any{"type": "object", "properties": properties, "required": required, "additionalProperties": false}}}
}
func integer(min, max int) map[string]any {
	return map[string]any{"type": "integer", "minimum": min, "maximum": max}
}
func text() map[string]any { return map[string]any{"type": "string", "maxLength": 1000} }
func Definitions() []llm.Tool {
	return []llm.Tool{
		definition("get_repository_info", "Consulta o diretório real da sessão, sem descrever o código.", map[string]any{}),
		definition("list_files", "Lista arquivos permitidos em páginas. Use next_offset para continuar.", map[string]any{"path": text(), "offset": integer(0, 20000), "limit": integer(1, 60)}),
		definition("get_project_map", "Resume diretórios, extensões e manifestos; não prova comportamento do código.", map[string]any{}),
		definition("read_lines", "Lê linhas reais, com cursor de linha e coluna para páginas truncadas.", map[string]any{"path": text(), "start": integer(1, 1000000), "end": integer(1, 1000000), "column": integer(0, maxFileBytes)}, "path", "start", "end"),
		definition("search_code", "Busca texto literal nos arquivos permitidos. Refine path e use next_offset.", map[string]any{"query": text(), "path": text(), "offset": integer(0, 10000), "limit": integer(1, 20)}, "query"),
	}
}

func validate(name string, args map[string]any) error {
	var selected *llm.Definition
	for _, tool := range Definitions() {
		if tool.Function.Name == name {
			d := tool.Function
			selected = &d
		}
	}
	if selected == nil {
		return errors.New("ferramenta desconhecida; use apenas ferramentas anunciadas")
	}
	properties := selected.Parameters["properties"].(map[string]any)
	for _, key := range selected.Parameters["required"].([]string) {
		if _, ok := args[key]; !ok {
			return fmt.Errorf("argumento obrigatório: %s", key)
		}
	}
	for key, value := range args {
		raw, ok := properties[key]
		if !ok {
			return fmt.Errorf("argumento desconhecido: %s", key)
		}
		spec := raw.(map[string]any)
		if spec["type"] == "integer" {
			if s, ok := value.(string); ok {
				n, e := strconv.Atoi(s)
				if e == nil && strconv.Itoa(n) == s {
					value = float64(n)
					args[key] = value
				}
			}
			n, ok := value.(float64)
			if !ok || n != float64(int(n)) || n < float64(spec["minimum"].(int)) || n > float64(spec["maximum"].(int)) {
				return fmt.Errorf("%s deve ser inteiro no intervalo anunciado", key)
			}
		} else {
			s, ok := value.(string)
			if !ok || s == "" || len(s) > 1000 {
				return fmt.Errorf("%s deve ser texto de até 1000 caracteres", key)
			}
		}
	}
	return nil
}
func number(args map[string]any, key string, fallback int) int {
	if n, ok := args[key].(float64); ok {
		return int(n)
	}
	return fallback
}
func str(args map[string]any, key string) string { s, _ := args[key].(string); return s }
func scoped(name, path string) bool {
	return path == "" || path == "." || name == path || strings.HasPrefix(name, strings.TrimSuffix(path, "/")+"/")
}

func (r *Repository) Execute(ctx context.Context, call llm.Call) (map[string]any, error) {
	var args map[string]any
	if len(call.Function.Arguments) > 64000 {
		return nil, errors.New("argumentos excedem 64 KiB")
	}
	if err := json.Unmarshal([]byte(call.Function.Arguments), &args); err != nil {
		return nil, fmt.Errorf("argumentos JSON inválidos")
	}
	if args == nil {
		return nil, errors.New("argumentos devem ser objeto JSON")
	}
	if err := validate(call.Function.Name, args); err != nil {
		return nil, err
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	switch call.Function.Name {
	case "get_repository_info":
		return map[string]any{"root": r.Path, "mode": "ask", "read_only": true}, nil
	case "read_lines":
		return r.readLines(ctx, args)
	}
	files, err := r.Files(ctx)
	if err != nil {
		return nil, err
	}
	if call.Function.Name == "get_project_map" {
		dirs := map[string]int{}
		ext := map[string]int{}
		manifests := []string{}
		for _, p := range files {
			dir := "."
			if strings.Contains(p, "/") {
				dir = strings.Split(p, "/")[0]
			}
			dirs[dir]++
			ext[filepath.Ext(p)]++
			if slices.Contains([]string{"go.mod", "package.json", "pyproject.toml", "Cargo.toml", "pom.xml", "Makefile", "Dockerfile"}, filepath.Base(p)) && len(manifests) < 30 {
				manifests = append(manifests, p)
			}
		}
		return map[string]any{"root": r.Path, "files": len(files), "extensions": ext, "top_directories": topCounts(dirs, 20), "manifests": manifests}, nil
	}
	path := str(args, "path")
	if path != "" && !safeName(path) && path != "." {
		return nil, errors.New("escopo deve ser relativo ao projeto")
	}
	offset := number(args, "offset", 0)
	limit := number(args, "limit", 20)
	if call.Function.Name == "list_files" {
		filtered := []string{}
		for _, p := range files {
			if scoped(p, path) {
				filtered = append(filtered, p)
			}
		}
		start := min(offset, len(filtered))
		end := min(start+limit, len(filtered))
		var next any
		if end < len(filtered) {
			next = end
		}
		return map[string]any{"files": filtered[start:end], "total": len(filtered), "offset": start, "next_offset": next}, nil
	}
	query := str(args, "query")
	results := []map[string]any{}
	seen := 0
	scanned := 0
	more := false
	// Use bounded pages; callers can restrict the directory rather than loading a whole repository.
	for _, p := range files {
		if !scoped(p, path) {
			continue
		}
		scanned++
		if scanned > 2000 {
			more = true
			break
		}
		raw, e := r.readKnown(ctx, p)
		if e != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			continue
		}
		for i, line := range strings.Split(string(raw), "\n") {
			if !strings.Contains(strings.ToLower(line), strings.ToLower(query)) {
				continue
			}
			seen++
			if seen <= offset {
				continue
			}
			if len(results) >= limit {
				more = true
				break
			}
			results = append(results, map[string]any{"path": p, "line": i + 1, "preview": prefix(line, 180)})
		}
		if more {
			break
		}
	}
	var next any
	if more && len(results) > 0 {
		next = offset + len(results)
	}
	return map[string]any{"results": results, "offset": offset, "next_offset": next, "scanned": min(scanned, 2000), "scan_limited": scanned > 2000, "notice": "Busca textual, sem resolução semântica; refine path em projetos grandes."}, nil
}

func (r *Repository) readLines(ctx context.Context, args map[string]any) (map[string]any, error) {
	name := str(args, "path")
	raw, err := r.read(ctx, name)
	if err != nil {
		return nil, err
	}
	lines := strings.Split(string(raw), "\n")
	if len(lines) > 1 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	start, end, column := number(args, "start", 1), number(args, "end", 80), number(args, "column", 0)
	if end < start || start > len(lines) || end-start > 159 {
		return nil, errors.New("intervalo inválido; leia até 160 linhas existentes")
	}
	end = min(end, len(lines))
	if column > len(lines[start-1]) || !utf8.ValidString(lines[start-1][:column]) {
		return nil, errors.New("coluna inválida para esta linha UTF-8")
	}
	var content strings.Builder
	nextLine, nextColumn := start, column
	truncated := false
	for i := start; i <= end; i++ {
		line := lines[i-1]
		col := 0
		if i == start {
			col = column
		}
		segment := line[col:]
		remaining := 2400 - content.Len()
		if len(segment)+1 > remaining {
			part := prefix(segment, max(0, remaining-1))
			content.WriteString(part)
			nextLine = i
			nextColumn = col + len(part)
			truncated = true
			break
		}
		fmt.Fprintf(&content, "%s\n", segment)
		nextLine = i + 1
		nextColumn = 0
	}
	return map[string]any{"path": name, "start_line": start, "end_line": min(nextLine, end), "content": content.String(), "truncated": truncated, "next_start_line": nextLine, "next_column": nextColumn}, nil
}

func prefix(s string, n int) string {
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}
func topCounts(counts map[string]int, limit int) []map[string]any {
	keys := []string{}
	for k := range counts {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		if counts[keys[i]] == counts[keys[j]] {
			return keys[i] < keys[j]
		}
		return counts[keys[i]] > counts[keys[j]]
	})
	out := []map[string]any{}
	for _, key := range keys[:min(limit, len(keys))] {
		out = append(out, map[string]any{"name": key, "files": counts[key]})
	}
	return out
}
