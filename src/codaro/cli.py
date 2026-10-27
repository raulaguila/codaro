from __future__ import annotations

import json
import os
import shlex
import shutil
import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from codaro.agent import Agent, AgentEvent
from codaro.cli_commands.common import ContextWindow, Root, TLSInsecure, console, errors, fail
from codaro.cli_commands.configuration import register as register_configuration
from codaro.cli_commands.extensions import register as register_extensions
from codaro.cli_commands.providers import register as register_provider_commands
from codaro.cli_commands.startup import CodaroGroup, help_all
from codaro.configuration import resolve_configuration, startup_overrides
from codaro.index import CodeIndex, safe_preview
from codaro.llm import ModelError, Settings, create_provider
from codaro.policies import Mode
from codaro.repository import Repository
from codaro.tasks import TaskStore

app = typer.Typer(
    cls=CodaroGroup,
    help="Explore repositórios com busca local e um assistente de IA.",
    epilog=(
        "codaro abre o projeto atual. codaro CAMINHO abre outro projeto. "
        "Use Ctrl+P para configurar; --help-all lista todos os comandos."
    ),
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    resume: Annotated[
        bool | None, typer.Option("--resume/--no-resume", help="Retoma a última conversa.")
    ] = None,
    mode: Annotated[Mode | None, typer.Option(help="Modo inicial: ask, plan ou execute.")] = None,
    provider: Annotated[
        str | None, typer.Option(help="Perfil salvo; env usa variáveis de ambiente.")
    ] = None,
    all_help: Annotated[
        bool,
        typer.Option(
            "--help-all",
            callback=help_all,
            is_eager=True,
            help="Mostra também ferramentas e administração.",
        ),
    ] = False,
):
    overrides = {
        key: value
        for key, value in {"mode": mode, "resume": resume, "provider": provider}.items()
        if value is not None
    }
    if ctx.invoked_subcommand not in {None, "chat"} and (mode is not None or resume is not None):
        raise typer.BadParameter("--mode e --resume se aplicam à abertura do chat.")
    token = startup_overrides.set(overrides)
    ctx.call_on_close(lambda: startup_overrides.reset(token))
    if ctx.invoked_subcommand is None:
        ctx.invoke(chat)


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


def approve_external(source, name, arguments, cancelled):
    if cancelled is not None and cancelled.is_set():
        return False
    errors.print(
        safe_preview(
            source + " / " + name + "\n" + json.dumps(arguments, ensure_ascii=False, indent=2)
        ),
        markup=False,
    )
    try:
        return typer.confirm("Autorizar esta ação externa?", default=False)
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
    path: Annotated[str | None, typer.Option(help="Diretório ou arquivo relativo.")] = None,
    glob: Annotated[str | None, typer.Option(help="Padrão de caminhos.")] = None,
    language: Annotated[str | None, typer.Option(help="Linguagem ou alias.")] = None,
    extension: Annotated[str | None, typer.Option(help="Extensão do arquivo.")] = None,
    kind: Annotated[str | None, typer.Option(help="Tipo de símbolo.")] = None,
    semantic: Annotated[
        bool, typer.Option(help="Embeddings locais opcionais; pode baixar pesos.")
    ] = False,
):
    """Busca textual e por símbolos, sem chamar uma API de IA."""
    index = None
    try:
        index = CodeIndex(Repository(repo))
        if semantic:
            from codaro.semantic import SemanticSearch

            index.semantic = SemanticSearch()
        results = index.search(
            query, limit, path=path, glob=glob, language=language, extension=extension, kind=kind
        )
        if as_json:
            typer.echo(
                json.dumps(
                    {"results": results, "retrieval": index.last_search},
                    ensure_ascii=False,
                    indent=2,
                )
            )
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
    max_seconds: int | None = None,
    max_corrections: int | None = None,
):
    try:
        config = resolve_configuration(
            tls_insecure=tls_insecure,
            context_window=context_window,
            max_steps=max_steps,
            max_seconds=max_seconds,
            max_corrections=max_corrections,
        )
        if config.needs_setup and config.setup_profile:
            raise ValueError(
                "Selecione um modelo para o perfil. Abra codaro ou use codaro models use."
            )
        agent = Agent(
            Repository(repo),
            create_provider(config.model),
            allow_edits=allow_edits,
            mode=mode or (Mode.EXECUTE if allow_edits else Mode.ASK),
            approve_edit=approve_changes,
            approve_external=approve_external,
            max_steps=config.preferences.max_steps,
            max_seconds=config.preferences.max_seconds,
            max_corrections=config.preferences.max_corrections,
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
    mode: Annotated[Mode | None, typer.Option("--mode", help="ask, plan ou execute.")] = None,
    max_steps: Annotated[
        int | None, typer.Option(min=1, max=200, rich_help_panel="Avançado")
    ] = None,
    max_seconds: Annotated[
        int | None, typer.Option(min=1, max=7200, rich_help_panel="Avançado")
    ] = None,
    max_corrections: Annotated[
        int | None, typer.Option(min=1, max=10, rich_help_panel="Avançado")
    ] = None,
    tls_insecure: TLSInsecure = None,
    context_window: ContextWindow = None,
    resume: Annotated[
        bool | None,
        typer.Option("--resume/--no-resume", help="Retoma a última conversa deste projeto."),
    ] = None,
    provider: Annotated[
        str | None, typer.Option(help="Perfil salvo; env usa variáveis de ambiente.")
    ] = None,
):
    """Abre o agente interativo no projeto, com conversa, ferramentas e revisão."""
    from codaro.ui.app import CodaroApp

    try:
        config = resolve_configuration(
            mode=mode,
            resume=resume,
            provider=provider,
            max_steps=max_steps,
            max_seconds=max_seconds,
            max_corrections=max_corrections,
            tls_insecure=tls_insecure,
            context_window=context_window,
        )
        CodaroApp(
            Agent(
                Repository(repo),
                create_provider(config.model),
                allow_edits=not read_only,
                mode=Mode.ASK if read_only else config.preferences.mode,
                max_steps=config.preferences.max_steps,
                max_seconds=config.preferences.max_seconds,
                max_corrections=config.preferences.max_corrections,
            ),
            resume=config.preferences.resume,
            offer_resume=config.preferences.resume and resume is None,
            show_summaries=config.preferences.show_summaries,
            setup_required=config.needs_setup,
            setup_profile=config.setup_profile,
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
        config = resolve_configuration(tls_insecure=tls_insecure, context_window=context_window)
        settings = config.model
    except (ValueError, OSError) as exc:
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
    model_label = settings.model if not config.needs_setup else "não selecionado · abra codaro"
    console.print(
        Panel(
            Text(
                f"ripgrep: {'disponível' if rg_ready else 'ausente'}\n"
                f"SQLite FTS5: {'disponível' if sqlite_ready else 'ausente'}\n"
                f"SQLite snapshots: {'disponível' if snapshot_ready else 'ausente'}\n"
                f"modelo: {model_label}\n"
                f"provedor: {settings.provider_id or 'ambiente/local'} · API {settings.api_style}\n"
                f"timeout: {settings.timeout:g}s\n"
                f"janela configurada: {settings.context_window} tokens\n"
                f"origem do limite: {settings.context_source}\n"
                f"saída: {settings.max_output_tokens or 'Automático'}; "
                f"reserva de contexto: {settings.output_reserve} tokens; margem: 512\n"
                f"contagem: {settings.token_encoding or 'estimativa UTF-8 / 2'}\n"
                f"TLS: {'sem verificação' if settings.tls_insecure else 'verificação ativa'}\n"
                f"credencial: {'configurada' if settings.api_key else 'não configurada'}\n"
                "origem da configuração: " + config.sources["provider"] + "\n"
                "origem do timeout: " + config.sources["timeout"] + "\n"
                "Configuração: Ctrl+P → Preferências; codaro config show\n"
                + (
                    "Variáveis de conexão ignoradas: " + ", ".join(config.ignored_environment)
                    if config.ignored_environment
                    else ""
                )
            ),
            title="Diagnóstico",
        )
    )
    if not rg_ready or not sqlite_ready or not snapshot_ready:
        raise typer.Exit(1)
    if check_tools:
        try:
            with console.status("Verificando protocolo de ferramentas…"):
                create_provider(settings).check_tool_calling()
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
def repository_map(
    repo: Root = Path("."),
    path: Annotated[str | None, typer.Option(help="Escopo relativo ao projeto.")] = None,
):
    """Mostra módulos, manifestos e candidatos a pontos de entrada atuais."""
    from codaro.project_map import ProjectMap

    try:
        with CodeIndex(Repository(repo)) as index:
            typer.echo(
                json.dumps(ProjectMap().build(index, path=path), ensure_ascii=False, indent=2)
            )
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
        provider = create_provider(Settings.from_env()) if agent_mode else None
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
    max_steps: Annotated[
        int | None, typer.Option(min=1, max=200, rich_help_panel="Avançado")
    ] = None,
    max_seconds: Annotated[
        int | None, typer.Option(min=1, max=7200, rich_help_panel="Avançado")
    ] = None,
    max_corrections: Annotated[
        int | None, typer.Option(min=1, max=10, rich_help_panel="Avançado")
    ] = None,
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


register_provider_commands(app)
register_extensions(app)
register_configuration(app)

for command in app.registered_commands:
    name = command.name or command.callback.__name__.replace("_", "-")
    command.rich_help_panel = (
        "Uso diário"
        if name in {"chat", "ask", "plan", "execute"}
        else "Configuração"
        if name == "doctor"
        else "Ferramentas e administração"
    )

if __name__ == "__main__":
    app()
