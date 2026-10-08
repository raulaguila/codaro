from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from codaro.index import safe_preview

console = Console()

errors = Console(stderr=True)

Root = Annotated[Path, typer.Option("--repo", "-r", help="Diretório do repositório.")]

TLSInsecure = Annotated[
    bool | None,
    typer.Option(
        "--tls-insecure/--tls-verify",
        help="Desativa/ativa a verificação de certificados TLS do modelo.",
    ),
]

ContextWindow = Annotated[
    int | None,
    typer.Option(
        "--context-window",
        min=4096,
        max=2_000_000,
        help="Janela real do servidor em tokens; reserva saída e margem de segurança.",
    ),
]


def fail(exc: Exception):
    message = (
        "Falha no índice SQLite. Confira permissões, espaço e integridade do índice."
        if isinstance(exc, sqlite3.Error)
        else safe_preview(str(exc))
    )
    errors.print(f"Erro: {message}", style="red", markup=False)
    raise typer.Exit(1) from exc
