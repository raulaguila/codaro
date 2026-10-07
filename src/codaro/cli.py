from __future__ import annotations

import json
import os
import shlex
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
from typer.core import TyperGroup

from codaro.agent import Agent, AgentEvent
from codaro.index import CodeIndex, safe_preview
from codaro.policies import Mode
from codaro.provider import ModelError, OpenAICompatible, Settings
from codaro.repository import Repository
from codaro.tasks import TaskStore


class CodaroGroup(TyperGroup):
    def resolve_command(self, ctx, args: list[str]):
        if args:
            target = args[0]
            explicit_path = (
                target in {".", ".."} or "/" in target or "\\" in target or target.startswith("~")
            )
            if target not in self.commands and (
                explicit_path or Path(target).expanduser().is_dir()
            ):
                return super().resolve_command(ctx, ["chat", "--repo", target, *args[1:]])
        return super().resolve_command(ctx, args)


app = typer.Typer(
    cls=CodaroGroup,
    help="Explore repositórios com busca local e um assistente de IA.",
    epilog=(
        "Atalho: codaro . abre o chat no diretório atual. Use codaro CAMINHO para outro projeto."
    ),
    no_args_is_help=True,
)
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


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    resume: Annotated[
        bool, typer.Option("--resume", help="Retoma a conversa do diretório atual.")
    ] = False,
):
    if resume:
        if ctx.invoked_subcommand:
            raise typer.BadParameter("Use --resume após o diretório ou o comando chat.")
        ctx.invoke(chat, repo=Path("."), read_only=False, tls_insecure=None, resume=True)


def approve_command(argv, timeout, cancelled, *, root):
    errors.print(
        safe_preview(f"Diretório: {root}\nComando · timeout {timeout}s\n{shlex.join(argv)}"),
        markup=False,
    )
    try:
        return typer.confirm("Autorizar esta execução?", default=False)
    except (EOFError, typer.Abort):
        return False


def approve_changes(proposals, cancelled):
    if cancelled is not None and cancelled.is_set():
        return False
    for proposal in proposals:
        errors.print(
            Panel(
                Text(safe_preview(proposal.reason)),
                title=Text(proposal.operation + " · " + proposal.path),
            )
        )
        errors.print(Syntax(safe_preview(proposal.diff), "diff", word_wrap=True))
    try:
        return typer.confirm(
            f"Aplicar este conjunto de {len(proposals)} arquivo(s)?", default=False
        )
    except (EOFError, typer.Abort):
        return False


def grant_scope(agent, paths, commands):
    task = agent.tasks.current()
    if task is None:
        raise ValueError("Defina uma tarefa antes de autorizar o escopo.")
    parsed = [json.loads(command) for command in commands]
    from codaro.policies import ApprovalPolicy

    proposed = ApprovalPolicy()
    proposed.grant(task["id"], paths, parsed)
    errors.print(
        safe_preview(
            json.dumps(
                {
                    "task": task["id"],
                    "objective": task["objective"],
                    "paths": paths,
                    "commands": parsed,
                },
                ensure_ascii=False,
                indent=2,
            )
        ),
        markup=False,
    )
    try:
        approved = typer.confirm(
            "Autorizar criar/alterar/remover/renomear nesses caminhos e "
            "executar somente esses comandos nesta sessão/tarefa?",
            default=False,
        )
    except (EOFError, typer.Abort):
        approved = False
    if approved:
        agent.policy = proposed
        agent.tasks.event("scope_approval", {"paths": paths, "commands": parsed})
    return approved


def fail(exc: Exception):
    message = (
        "Falha no índice SQLite. Confira permissões, espaço e integridade do índice."
        if isinstance(exc, sqlite3.Error)
        else safe_preview(str(exc))
    )
    errors.print(f"Erro: {message}", style="red", markup=False)
    raise typer.Exit(1) from exc


@app.command()
def pwd(repo: Root = Path(".")):
    """Mostra a raiz absoluta do projeto sem consultar IA."""
    try:
        typer.echo(str(Repository(repo).root))
    except (ValueError, OSError) as exc:
        fail(exc)


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
def ask(
    question: str,
    repo: Root = Path("."),
    tls_insecure: TLSInsecure = None,
    context_window: ContextWindow = None,
):
    """Investiga uma pergunta usando ferramentas e o modelo configurado (somente leitura)."""
    run_question(
        question, repo, allow_edits=False, tls_insecure=tls_insecure, context_window=context_window
    )


@app.command()
def edit(
    question: str,
    repo: Root = Path("."),
    tls_insecure: TLSInsecure = None,
    context_window: ContextWindow = None,
):
    """Propõe mudanças e solicita aprovação para diffs e comandos."""
    run_question(
        question, repo, allow_edits=True, tls_insecure=tls_insecure, context_window=context_window
    )


def run_question(
    question: str,
    repo: Path,
    *,
    allow_edits: bool,
    tls_insecure: bool | None = None,
    context_window: int | None = None,
    mode: str | Mode | None = None,
    approval: str = "action",
    scopes: list[str] | None = None,
    commands: list[str] | None = None,
    max_steps: int | None = None,
    max_seconds: int = 1800,
    max_corrections: int = 3,
):
    try:
        agent = Agent(
            Repository(repo),
            OpenAICompatible(
                Settings.from_env(tls_insecure=tls_insecure, context_window=context_window)
            ),
            allow_edits=allow_edits,
            mode=mode or (Mode.EXECUTE if allow_edits else Mode.ASK),
            approve_edit=approve_changes,
            max_steps=max_steps,
            max_seconds=max_seconds,
            max_corrections=max_corrections,
            approve_command=(
                lambda argv, timeout, cancelled: approve_command(
                    argv, timeout, cancelled, root=repo.expanduser().resolve()
                )
            )
            if allow_edits
            else None,
        )
        if approval not in {"action", "task"}:
            raise ValueError("Use approval action ou task.")
        if approval == "task":
            if agent.mode != Mode.EXECUTE:
                raise ValueError("Autorização por tarefa está disponível em Executar.")
            agent.tasks.start(question)
            if not grant_scope(agent, scopes or [], commands or []):
                raise ValueError("Escopo não autorizado; nenhuma ação executada.")
        elif scopes or commands:
            raise ValueError("Use --approval task para autorizar escopo/comandos.")
        parts: list[str] = []

        def detail(event: AgentEvent):
            if event.kind == "model_start":
                parts.clear()
                live.update(Markdown("Consultando modelo…"))
            elif event.kind == "model_end" and event.state != "answer":
                parts.clear()
                live.update(Markdown("Investigando…"))
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
                live.update(Text("Gerando resposta… · texto provisório em .codaro/prompt.json"))

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
                message = f"Edição aplicada. Checkpoint: {proposal.checkpoint_id}. "
                message += "Testes não foram executados. " + proposal.checkpoint_warning
                console.print(message)
                save_review(agent, proposal, message)
            else:
                agent.edits.reject(proposal.id)
                console.print("Edição rejeitada; arquivo preservado.")
                save_review(agent, proposal, "Edição rejeitada; arquivo preservado.")
    except (ModelError, ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)


@app.command()
def chat(
    repo: Root = Path("."),
    read_only: Annotated[
        bool, typer.Option("--read-only", help="Somente leitura: desabilita edições e comandos.")
    ] = False,
    mode: Annotated[Mode, typer.Option("--mode", help="ask, plan ou execute.")] = Mode.EXECUTE,
    max_steps: Annotated[int | None, typer.Option(min=1, max=200)] = None,
    max_seconds: Annotated[int, typer.Option(min=1, max=7200)] = 1800,
    max_corrections: Annotated[int, typer.Option(min=1, max=10)] = 3,
    tls_insecure: TLSInsecure = None,
    context_window: ContextWindow = None,
    resume: Annotated[
        bool, typer.Option("--resume", help="Retoma a última conversa deste projeto.")
    ] = False,
):
    """Abre o agente interativo no projeto, com conversa, ferramentas e revisão."""
    from codaro.tui import CodaroApp

    try:
        CodaroApp(
            Agent(
                Repository(repo),
                OpenAICompatible(
                    Settings.from_env(tls_insecure=tls_insecure, context_window=context_window)
                ),
                allow_edits=not read_only,
                mode=Mode.ASK if read_only else mode,
                max_steps=max_steps,
                max_seconds=max_seconds,
                max_corrections=max_corrections,
            ),
            resume=resume,
        ).run()
    except (ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)


@app.command()
def doctor(
    tls_insecure: TLSInsecure = None,
    context_window: ContextWindow = None,
    check_tools: Annotated[
        bool,
        typer.Option(
            "--check-tools", help="Faz uma chamada ao modelo para verificar tool-calling."
        ),
    ] = False,
):
    """Mostra configuração e disponibilidade do ripgrep sem expor a chave."""
    try:
        settings = Settings.from_env(tls_insecure=tls_insecure, context_window=context_window)
    except ValueError as exc:
        fail(exc)
    sqlite_ready = True
    snapshot_ready = False
    try:
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE VIRTUAL TABLE diagnostic USING fts5(content)")
            if callable(getattr(db, "serialize", None)) and callable(
                getattr(db, "deserialize", None)
            ):
                db.deserialize(db.serialize())
                snapshot_ready = True
    except sqlite3.Error:
        sqlite_ready = False
    rg_ready = bool(shutil.which("rg"))
    console.print(
        Panel(
            Text(
                f"ripgrep: {'disponível' if rg_ready else 'ausente'}\n"
                f"SQLite FTS5: {'disponível' if sqlite_ready else 'ausente'}\n"
                f"SQLite snapshots: {'disponível' if snapshot_ready else 'ausente'}\n"
                f"modelo: {settings.model}\n"
                f"timeout: {settings.timeout:g}s\n"
                f"janela configurada: {settings.context_window} tokens\n"
                f"reserva de saída: {settings.max_output_tokens} tokens; margem: 512\n"
                f"contagem: {settings.token_encoding or 'estimativa UTF-8 / 2'}\n"
                f"TLS: {'sem verificação' if settings.tls_insecure else 'verificação ativa'}\n"
                f"credencial: {'configurada' if settings.api_key else 'não configurada'}\n"
                "Configuração: CODARO_BASE_URL, CODARO_MODEL, CODARO_API_KEY"
            ),
            title="Diagnóstico",
        )
    )
    if not rg_ready or not sqlite_ready or not snapshot_ready:
        raise typer.Exit(1)
    if check_tools:
        try:
            with console.status("Verificando protocolo de ferramentas…"):
                OpenAICompatible(settings).check_tool_calling()
            console.print(
                "Tool-calling: chamada estruturada, resultado e resposta final confirmados."
            )
        except (ModelError, ValueError, OSError) as exc:
            fail(exc)


@app.command("history")
def conversation_history(query: str, repo: Root = Path(".")):
    """Busca mensagens antigas do projeto, sem consultar IA."""
    from codaro.memory import ConversationMemory

    try:
        result = ConversationMemory(Repository(repo).root, os.getenv("CODARO_API_KEY", "")).search(
            query
        )
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("memory")
def task_memory(
    action: Annotated[str, typer.Argument()] = "show",
    text: Annotated[str, typer.Argument()] = "",
    repo: Root = Path("."),
):
    """Ver memória da tarefa ou registrar decision, constraint, pending, clear."""
    from codaro.memory import ConversationMemory

    try:
        memory = ConversationMemory(Repository(repo).root, os.getenv("CODARO_API_KEY", ""))
        if action == "clear":
            memory.clear_task()
        elif action != "show":
            memory.remember(action, text)
        typer.echo(json.dumps(memory.task(), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("map")
def repository_map(repo: Root = Path(".")):
    """Mostra módulos, manifestos e candidatos a pontos de entrada atuais."""
    from codaro.project_map import ProjectMap

    try:
        with CodeIndex(Repository(repo)) as index:
            typer.echo(json.dumps(ProjectMap().build(index), ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error) as exc:
        fail(exc)


@app.command("changes")
def changes(repo: Root = Path(".")):
    """Lista checkpoints com status e possibilidade de desfazer."""
    from codaro.checkpoints import Checkpoints

    try:
        typer.echo(json.dumps(Checkpoints(Repository(repo)).list(), ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("undo")
def undo(checkpoint: Annotated[str | None, typer.Argument()] = None, repo: Root = Path(".")):
    """Revisa e desfaz uma edição se o arquivo não mudou; aprovação obrigatória."""
    from codaro.edits import EditManager

    try:
        manager = EditManager(Repository(repo))
        proposal = manager.propose_undo(checkpoint)
        console.print(Syntax(safe_preview(proposal.diff), "diff", word_wrap=True))
        if typer.confirm(f"Desfazer a edição em {proposal.path}?", default=False):
            from codaro.storage import private_lock

            with private_lock(manager.repository.root / ".codaro/agent.lock"):
                manager.apply(proposal.id)
            message = "Edição desfeita. " + proposal.checkpoint_warning
            console.print(message)
            from codaro.memory import ConversationMemory

            try:
                memory = ConversationMemory(
                    manager.repository.root, os.getenv("CODARO_API_KEY", "")
                )
                memory.record_change(proposal, message)
            except (ValueError, OSError) as exc:
                errors.print(f"Alteração concluída; memória não salva: {exc}", markup=False)
        else:
            manager.reject(proposal.id)
            console.print("Desfazer rejeitado; arquivo preservado.")
    except (ValueError, OSError) as exc:
        fail(exc)


@app.command("evaluate")
def evaluate_command(
    cases: Annotated[Path, typer.Argument()] = Path("evaluations/cases.json"),
    agent_mode: Annotated[
        bool,
        typer.Option(
            "--agent", help="Avalia respostas com o modelo real; envia código ao endpoint."
        ),
    ] = False,
    output: Annotated[Path, typer.Option("--output", "-o", help="Relatório JSON.")] = Path(
        ".codaro/evaluation.json"
    ),
):
    """Avalia recuperação local; --agent também mede respostas e consumo de contexto."""
    from codaro.evaluation import evaluate
    from codaro.trace import atomic_write

    try:
        provider = OpenAICompatible(Settings.from_env()) if agent_mode else None
        report = evaluate(
            cases.resolve(), provider=provider, mode="agent" if agent_mode else "retrieval"
        )
        atomic_write(output.resolve(), json.dumps(report, ensure_ascii=False, indent=2).encode())
        console.print(
            f"Avaliação {report['mode']}: {report['passed']}/{report['total']} · {output}"
        )
        if report["passed"] != report["total"]:
            raise typer.Exit(1)
    except (ValueError, OSError, ModelError) as exc:
        fail(exc)


def save_review(agent, proposal, message):
    try:
        agent.memory.review(f"{proposal.path}: {message}")
        agent.memory.record_change(proposal, message)
    except (ValueError, OSError) as exc:
        errors.print(f"Revisão concluída; memória não salva: {exc}", markup=False)


@app.command()
def plan(
    question: str,
    repo: Root = Path("."),
    tls_insecure: TLSInsecure = None,
    context_window: ContextWindow = None,
):
    """Investiga e registra um plano persistente, sem alterações ou comandos."""
    run_question(
        question,
        repo,
        allow_edits=False,
        mode=Mode.PLAN,
        tls_insecure=tls_insecure,
        context_window=context_window,
    )


@app.command(name="execute")
def execute_task(
    question: str,
    repo: Root = Path("."),
    tls_insecure: TLSInsecure = None,
    context_window: ContextWindow = None,
    approval: Annotated[str, typer.Option(help="action ou task.")] = "action",
    scope: Annotated[
        list[str] | None, typer.Option(help="Caminho relativo autorizado; repetível.")
    ] = None,
    allow_command: Annotated[
        list[str] | None,
        typer.Option(help='Comando exato como array JSON, por exemplo ["pytest","-q"]. Repetível.'),
    ] = None,
    max_steps: Annotated[int | None, typer.Option(min=1, max=200)] = None,
    max_seconds: Annotated[int, typer.Option(min=1, max=7200)] = 1800,
    max_corrections: Annotated[int, typer.Option(min=1, max=10)] = 3,
):
    """Implementa uma atividade e valida o resultado, com autorização humana."""
    run_question(
        question,
        repo,
        allow_edits=True,
        mode=Mode.EXECUTE,
        approval=approval,
        scopes=scope,
        commands=allow_command,
        max_steps=max_steps,
        max_seconds=max_seconds,
        max_corrections=max_corrections,
        tls_insecure=tls_insecure,
        context_window=context_window,
    )


@app.command()
def task(
    action: Annotated[str, typer.Argument()] = "show",
    value: Annotated[str, typer.Argument()] = "",
    repo: Root = Path("."),
):
    """Consulta tarefas, inicia uma nova ou seleciona uma para retomada."""
    try:
        from codaro.sessions import SessionStore

        repository = Repository(repo)
        store = TaskStore(
            repository.root,
            redact=SessionStore(repository.root, os.getenv("CODARO_API_KEY", "")).redact,
        )
        from contextlib import nullcontext

        from codaro.storage import private_lock

        guard = (
            private_lock(repository.root / ".codaro/agent.lock")
            if action in {"new", "resume"}
            else nullcontext()
        )
        with guard:
            if action == "new":
                if not value.strip() or len(value) > 8000:
                    raise ValueError("Use task new OBJETIVO.")
                result = store.start(value, new=True)
            elif action == "resume":
                result = store.select(value)
            elif action == "list":
                result = [
                    {key: item[key] for key in ("id", "objective", "state")}
                    for item in store.load()["tasks"]
                ]
            elif action == "show":
                result = store.current()
            else:
                raise ValueError("Use show, list, new ou resume.")
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        fail(exc)


if __name__ == "__main__":
    app()
