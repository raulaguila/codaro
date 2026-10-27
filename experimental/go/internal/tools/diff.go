package tools

import (
	"context"
	"errors"
	"os/exec"
)

// Diff uses fixed arguments, never a shell, external diff program or text converter.
func (r *Repository) Diff(ctx context.Context) (string, error) {
	cmd := exec.CommandContext(ctx, "git", "--no-pager", "diff", "--no-ext-diff", "--no-textconv", "--no-color", "HEAD", "--")
	cmd.Dir = r.Path
	var out cappedBuffer
	out.limit = 128 * 1024
	cmd.Stdout = &out
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", errors.New("diff indisponível: requer Git, commit inicial e saída menor que 128 KiB")
	}
	if out.Len() == 0 {
		return "Nenhuma alteração rastreada em relação ao HEAD. Arquivos não rastreados não aparecem neste diff.", nil
	}
	return out.String(), nil
}
