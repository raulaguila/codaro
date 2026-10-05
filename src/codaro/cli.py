from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from codaro.agent import Agent, AgentEvent
from codaro.index import CodeIndex, safe_preview
from codaro.provider import ModelError, OpenAICompatible, Settings
from codaro.repository import Repository

app = typer.Typer(
    help="Explore repositórios com busca local e um assistente de IA.", no_args_is_help=True
)
console = Console()
errors = Console(stderr=True)
Root = Annotated[Path, typer.Option("--repo", "-r", help="Diretório do repositório.")]


def fail(exc: Exception):
    message = (
        "Falha no índice SQLite. Confira permissões, espaço e integridade do índice."
        if isinstance(exc, sqlite3.Error)
        else safe_preview(str(exc))
    )
    errors.print(f"Erro: {message}", style="red", markup=False)
    raise typer.Exit(1) from exc


@app.command("index")
def build_index(repo: Root = Path(".")):
    """Atualiza o índice, reutilizando arquivos que não mudaram."""
    index = None
    try:
        index = CodeIndex(Repository(repo))
        with console.status("Indexando código…"):
            stats = index.update()
        table = Table(title="Índice local")
        table.add_column("Métrica")
        table.add_column("Quantidade", justify="right")
        for key, value in stats.items():
            table.add_row(key, str(value))
        console.print(table)
    except (ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)
    finally:
        if index:
            index.close()


@app.command()
def search(
    query: str,
    repo: Root = Path("."),
    limit: Annotated[int, typer.Option(min=1, max=12)] = 6,
    as_json: Annotated[bool, typer.Option("--json", help="Saída estruturada.")] = False,
):
    """Busca textual e por símbolos, sem chamar uma API de IA."""
    index = None
    try:
        index = CodeIndex(Repository(repo))
        results = index.search(query, limit)
        if as_json:
            typer.echo(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        else:
            for item in results:
                console.print(
                    Panel(
                        Text(f"{item['symbol']}\n{item['signature']}\n\n{item['preview']}"),
                        title=Text(f"{item['path']}:{item['start_line']}–{item['end_line']}"),
                        border_style="cyan",
                    )
                )
            if not results:
                console.print(
                    "Nenhum resultado. Tente um nome de função ou termos presentes no código."
                )
    except (ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)
    finally:
        if index:
            index.close()


@app.command()
def read(
    path: str,
    repo: Root = Path("."),
    start: Annotated[int, typer.Option(min=1)] = 1,
    end: Annotated[int, typer.Option(min=1)] = 80,
):
    """Exibe um intervalo de código atual, com número de linha."""
    try:
        repository = Repository(repo)
        result = repository.read_lines(path, start, end)
        console.print(Panel(Text(result["content"]), title=Text(result["path"])))
        if result["truncated"]:
            console.print("Saída truncada. Solicite um intervalo menor.")
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command()
def ask(question: str, repo: Root = Path(".")):
    """Investiga uma pergunta usando ferramentas e o modelo configurado (somente leitura)."""
    run_question(question, repo, allow_edits=False)


@app.command()
def edit(question: str, repo: Root = Path(".")):
    """Propõe mudanças e solicita aprovação para cada diff antes de aplicar."""
    run_question(question, repo, allow_edits=True)


def run_question(question: str, repo: Path, *, allow_edits: bool):
    try:
        agent = Agent(
            Repository(repo), OpenAICompatible(Settings.from_env()), allow_edits=allow_edits
        )
        parts: list[str] = []

        def detail(event: AgentEvent):
            if event.kind in {"status", "tool_end"}:
                duration = f" · {event.elapsed_ms:.0f} ms" if event.elapsed_ms is not None else ""
                errors.print(
                    safe_preview(f"{event.title}{duration}\n{event.detail}"),
                    style="dim",
                    markup=False,
                )

        with Live(Markdown("Investigando…"), console=console, refresh_per_second=10) as live:

            def delta(fragment: str):
                parts.append(fragment)
                live.update(Markdown(safe_preview("".join(parts))))

            try:
                answer = agent.ask(question, on_delta=delta, on_detail=detail)
                live.update(Markdown(safe_preview(answer)))
            except (ModelError, ValueError, OSError, sqlite3.Error):
                live.update(Markdown("Resposta interrompida. Consulte o erro abaixo."))
                raise
        for proposal in agent.edits.pending:
            console.print(Panel(Text(safe_preview(proposal.reason)), title=Text(proposal.path)))
            console.print(Syntax(safe_preview(proposal.diff), "diff", word_wrap=True))
            try:
                approved = typer.confirm(f"Aplicar a edição em {proposal.path}?", default=False)
            except (EOFError, typer.Abort):
                agent.edits.reject(proposal.id)
                errors.print("Revisão encerrada; edições pendentes não foram aplicadas.")
                raise typer.Exit(1) from None
            if approved:
                agent.edits.apply(proposal.id)
                console.print("Edição aplicada. Testes não foram executados.")
            else:
                agent.edits.reject(proposal.id)
                console.print("Edição rejeitada; arquivo preservado.")
    except (ModelError, ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)


@app.command()
def chat(
    repo: Root = Path("."),
    read_only: Annotated[
        bool, typer.Option("--read-only", help="Desabilita propostas de edição.")
    ] = False,
):
    """Abre o chat interativo com painéis de conversa e atividade."""
    from codaro.tui import CodaroApp

    try:
        CodaroApp(
            Agent(
                Repository(repo), OpenAICompatible(Settings.from_env()), allow_edits=not read_only
            )
        ).run()
    except (ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)


@app.command()
def doctor():
    """Mostra configuração e disponibilidade do ripgrep sem expor a chave."""
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        fail(exc)
    sqlite_ready = True
    try:
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE VIRTUAL TABLE diagnostic USING fts5(content)")
    except sqlite3.Error:
        sqlite_ready = False
    rg_ready = bool(shutil.which("rg"))
    console.print(
        Panel(
            Text(
                f"ripgrep: {'disponível' if rg_ready else 'ausente'}\n"
                f"SQLite FTS5: {'disponível' if sqlite_ready else 'ausente'}\n"
                f"modelo: {settings.model}\n"
                f"timeout: {settings.timeout:g}s\n"
                f"credencial: {'configurada' if settings.api_key else 'não configurada'}\n"
                "Configuração: CODARO_BASE_URL, CODARO_MODEL, CODARO_API_KEY"
            ),
            title="Diagnóstico",
        )
    )
    if not rg_ready or not sqlite_ready:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
