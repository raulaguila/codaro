"""Inspect effective configuration and change persistent startup defaults."""

from dataclasses import asdict

import typer
from rich.table import Table

from codaro.cli_commands.common import console, fail
from codaro.configuration import resolve_configuration
from codaro.preferences import PreferencesStore, preference_value

config_app = typer.Typer(help="Preferências de inicialização e origem da configuração.")


@config_app.command("show")
def show():
    """Mostra preferências e configuração efetiva, sem credenciais e sem chamar a API."""
    try:
        config = resolve_configuration()
        table = Table("Configuração", "Valor", "Origem")
        table.add_row(
            "provedor", config.model.provider_id or "não selecionado", config.sources["provider"]
        )
        table.add_row(
            "modelo",
            config.model.model if not config.needs_setup else "não selecionado",
            config.sources["provider"],
        )
        for name, value in asdict(config.preferences).items():
            if name == "timeout":
                value = config.model.timeout
            table.add_row(name, "auto" if value is None else str(value), config.sources[name])
        for name in ("context_window", "max_output_tokens", "tls_insecure"):
            value = getattr(config.model, name)
            table.add_row(name, "auto" if value is None else str(value), config.sources[name])
        console.print(table)
        if config.ignored_environment:
            console.print(
                "Variáveis de conexão ignoradas: " + ", ".join(config.ignored_environment)
            )
        console.print("Preferências: " + str(PreferencesStore().path), markup=False)
    except (ValueError, OSError) as exc:
        fail(exc)


@config_app.command("set")
def set_preference(name: str, value: str):
    """Salva mode, resume, max_steps, max_seconds, max_corrections ou timeout."""
    try:
        PreferencesStore().update({name: preference_value(name, value)})
        console.print("Preferência salva para a próxima inicialização.")
    except (ValueError, OSError, TypeError) as exc:
        fail(exc)


@config_app.command("reset")
def reset(yes: bool = typer.Option(False, "--yes", "-y", help="Dispensa confirmação.")):
    """Restaura preferências; mantém provedores, credenciais e dados dos projetos."""
    if not yes and not typer.confirm("Restaurar as preferências de inicialização?", default=False):
        raise typer.Exit()
    try:
        PreferencesStore().reset()
        console.print("Preferências restauradas.")
    except (ValueError, OSError) as exc:
        fail(exc)


def register(app):
    app.add_typer(config_app, name="config", rich_help_panel="Configuração")
