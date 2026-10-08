"""Explicit trust and lifecycle commands for project-local integrations."""

import hashlib
import json
from pathlib import Path
from typing import Annotated

import typer

from codaro.features import FeatureStore
from codaro.repository import Repository

features_app = typer.Typer(help="Funcionalidades opcionais e orçamento global.")
integrations_app = typer.Typer(help="MCP e plugins explicitamente confiáveis.")
sessions_app = typer.Typer(help="Conversas independentes por projeto.")


@features_app.command("show")
def show_features(repo: Path = Path(".")):
    typer.echo(json.dumps(FeatureStore(Repository(repo).root).load(), ensure_ascii=False, indent=2))


@features_app.command("set")
def set_feature(feature: str, enabled: bool, repo: Path = Path(".")):
    FeatureStore(Repository(repo).root).toggle(feature, enabled)
    typer.echo("Configuração salva. Aplique em uma nova sessão do CLI.")


@integrations_app.command("add")
def add_integration(
    kind: str,
    name: str,
    trust: Annotated[
        bool, typer.Option("--trust", help="Confia no código/serviço e sua execução.")
    ] = False,
    command: str = "",
    url: str = "",
    plugin: Path | None = None,
    token_env: str = "",
    tls_insecure: bool = False,
    read_only: Annotated[list[str] | None, typer.Option("--read-only")] = None,
    env: Annotated[list[str] | None, typer.Option("--env")] = None,
    repo: Path = Path("."),
):
    if not trust:
        raise typer.BadParameter("Confiança explícita necessária: --trust.")
    if kind not in {"mcp", "plugin"}:
        raise typer.BadParameter("Use mcp ou plugin.")
    store = FeatureStore(Repository(repo).root)
    data = store.load()
    config = {
        "trusted": True,
        "enabled": True,
        "read_only_tools": read_only or [],
        "env": env or [],
    }
    if kind == "plugin":
        if plugin is None or command or url:
            raise typer.BadParameter("Plugin requer apenas --plugin CAMINHO.")
        path = plugin.expanduser().absolute()
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
            raise typer.BadParameter("Plugin deve ser arquivo regular de até 1 MB.")
        config.update(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        section = "plugins"
    else:
        if bool(command) == bool(url):
            raise typer.BadParameter("Informe exatamente um: --command JSON ou --url.")
        if command:
            try:
                argv = json.loads(command)
            except ValueError as exc:
                raise typer.BadParameter("--command deve ser uma lista JSON.") from exc
            config.update(transport="stdio", command=argv)
        else:
            config.update(transport="http", url=url, token_env=token_env, tls_insecure=tls_insecure)
        section = "mcp"
    if name in data[section]:
        raise typer.BadParameter("Nome já cadastrado. Remova antes de renovar a confiança.")
    data[section][name] = config
    try:
        store.save(data)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        "Integração cadastrada. Somente ferramentas declaradas --read-only dispensam revisão."
    )


@integrations_app.command("remove")
def remove_integration(kind: str, name: str, repo: Path = Path(".")):
    store = FeatureStore(Repository(repo).root)
    data = store.load()
    section = {"mcp": "mcp", "plugin": "plugins"}.get(kind)
    if section is None or name not in data[section]:
        raise typer.BadParameter("Integração não encontrada.")
    del data[section][name]
    store.save(data)
    typer.echo("Integração removida.")


@integrations_app.command("test")
def test_integrations(repo: Path = Path(".")):
    from codaro.agent import Agent
    from codaro.provider import Settings, create_provider

    agent = Agent(Repository(repo), create_provider(Settings.from_env()), mode="ask")
    try:
        agent.integrations.discover()
        result = {
            "connected": list(agent.integrations.clients),
            "errors": agent.integrations.errors,
            "tools": [
                {"name": tool.name, "source": tool.source, "read_only": tool.read_only}
                for tool in agent.registry.tools.values()
                if tool.source.startswith(("mcp:", "plugin:"))
            ],
        }
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        if agent.integrations.errors:
            raise typer.Exit(1)
    finally:
        agent.integrations.close()


@sessions_app.command("list")
def list_sessions(repo: Path = Path(".")):
    from codaro.session_catalog import SessionCatalog

    typer.echo(
        json.dumps(SessionCatalog(Repository(repo).root).load(), ensure_ascii=False, indent=2)
    )


@sessions_app.command("new")
def new_session(title: Annotated[str, typer.Argument()] = "Nova conversa", repo: Path = Path(".")):
    from codaro.session_catalog import SessionCatalog

    catalog = SessionCatalog(Repository(repo).root)
    identifier = catalog.create(title)
    catalog.activate(identifier)
    typer.echo(identifier)


@sessions_app.command("use")
def use_session(identifier: str, repo: Path = Path(".")):
    from codaro.session_catalog import SessionCatalog

    SessionCatalog(Repository(repo).root).activate(identifier)
    typer.echo("Sessão ativa: " + identifier)


def reverse_interaction(repo: Path, redo: bool, run_id: str | None):
    from codaro.edits import EditManager
    from codaro.session_catalog import SessionCatalog
    from codaro.storage import private_lock
    from codaro.undo_history import UndoHistory

    repository = Repository(repo)
    with private_lock(repository.root / ".codaro/agent.lock"):
        edits = EditManager(repository)
        session_id = SessionCatalog(repository.root).load()["active"]
        edits.checkpoints.session_id = session_id
        history = UndoHistory(edits, session_id)
        record, proposals = history.preview(run_id, redo=redo)
        for proposal in proposals:
            typer.echo(proposal.diff)
        if not typer.confirm("Aplicar a reversão em todos esses arquivos?", default=False):
            raise typer.Exit(0)
        transaction = history.apply(record, proposals, redo=redo)
        # Old assistant assumptions cannot remain live after a reversal.
        catalog = SessionCatalog(repository.root)
        catalog.store(session_id).save([], "")
        catalog.summary_path(session_id).unlink(missing_ok=True)
        typer.echo(json.dumps(transaction, ensure_ascii=False))


def register(app):
    app.add_typer(features_app, name="features")
    app.add_typer(integrations_app, name="integrations")
    app.add_typer(sessions_app, name="sessions")

    @app.command("undo-turn")
    def undo_turn(repo: Path = Path("."), run_id: str | None = None):
        reverse_interaction(repo, False, run_id)

    @app.command("redo")
    def redo(repo: Path = Path(".")):
        reverse_interaction(repo, True, None)

    @app.command("evaluate-workflows")
    def workflows(cases: Path, allow_execution: bool = False, output: Path | None = None):
        from codaro.provider import Settings, create_provider
        from codaro.workflow_evaluation import evaluate_workflows

        try:
            report = evaluate_workflows(
                cases, create_provider(Settings.from_env()), allow_execution=allow_execution
            )
        except (OSError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        raw = json.dumps(report, ensure_ascii=False, indent=2)
        if output:
            output.write_text(raw)
        typer.echo(raw)
        if report["passed"] != report["total"]:
            raise typer.Exit(1)
