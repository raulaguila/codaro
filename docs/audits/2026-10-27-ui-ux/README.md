# Evidências de UI/UX — 27/10/2026

Veja [o relatório](report.md) para achados, prioridades e critérios de aceite.
As capturas ficam em `artifacts/ui-audit-2026-10-27/` (não versionadas).

## Reproduzir

Com o ambiente de desenvolvimento instalado, execute na raiz do checkout:

```bash
env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor python scripts/capture_ui.py --output artifacts/ui-audit-2026-10-27
env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor python docs/audits/2026-10-27-ui-ux/reproduce.py
env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor python docs/audits/2026-10-27-ui-ux/capture_extra_states.py
python docs/audits/2026-10-27-ui-ux/reproduce_locked_vault.py
```

Os scripts usam configurações e repositórios temporários. `reproduce.py` inicia
um processo Python descartável e o encerra na limpeza; não chama modelos. O
último script reproduz deliberadamente a exceção do cofre, registrando o erro
em JSON. Configuração de terminal é apenas para a execução dos scripts.

As 184 capturas em cores abrangem 51 estados. Uma passagem adicional anterior
com `NO_COLOR` ficou em `artifacts/ui-audit-2026-10-27-monochrome/`.
Os scripts foram preservados como reproduções da auditoria, sem corrigir o
comportamento da aplicação.
