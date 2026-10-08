from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.table import Table
from rich.text import Text

from codaro.cli_commands.common import ContextWindow, TLSInsecure, console, fail
from codaro.llm import ModelError

providers_app = typer.Typer(help="Cadastre seus provedores e credenciais BYOK.")

models_app = typer.Typer(help="Consulte modelos da API e selecione o modelo ativo.")


def model_table(models):
    table = Table("Modelo", "Contexto", "Origem", "Saída máxima", "Ferramentas")
    for model in models:
        table.add_row(
            model["id"],
            str(model["context_window"] or "não informado"),
            model["context_source"],
            str(model["max_output_tokens"] or "não informado"),
            {True: "sim", False: "não", None: "não informado"}[model["tools"]],
        )
    console.print(table)


@providers_app.command("add")
def provider_add(
    kind: Annotated[
        str, typer.Argument(help="openai-compatible, openai, ollama, anthropic, gemini ou groq.")
    ],
    name: Annotated[str | None, typer.Option(help="Nome opcional do perfil.")] = None,
    base_url: Annotated[
        str | None, typer.Option(help="URL para OpenAI Compatible ou servidor Ollama.")
    ] = None,
    key_env: Annotated[
        str | None, typer.Option(help="Ler a chave desta variável de ambiente.")
    ] = None,
    tls_insecure: TLSInsecure = None,
):
    """Consulta o catálogo com a credencial; entrada da chave é oculta."""
    from codaro.llm.endpoints import PRESETS
    from codaro.llm.profiles import ProviderStore

    try:
        if kind not in PRESETS:
            raise ValueError("Provedor inválido: " + ", ".join(PRESETS))
        if not (base_url or PRESETS[kind]):
            base_url = typer.prompt("URL base da API (incluindo /v1 quando necessário)")
        if key_env:
            key = os.getenv(key_env, "")
            if not key:
                raise ValueError("A variável informada não contém uma chave.")
        elif kind == "ollama":
            key = ""
        else:
            key = typer.prompt(
                "API key",
                hide_input=True,
                default="" if kind in {"custom", "openai-compatible"} else None,
                show_default=False,
            )
        store = ProviderStore()
        with console.status("Consultando modelos do provedor…"):
            models = store.register(
                kind, key.strip(), name=name, base_url=base_url, tls_insecure=bool(tls_insecure)
            )
        model_table(models)
        identifier = typer.prompt(
            "Selecione o ID do modelo (Enter para selecionar depois)",
            default="",
            show_default=False,
        )
        if identifier:
            settings = store.select(name or kind, identifier)
            console.print(
                Text(
                    f"Ativo: {settings.provider_id} / {settings.model} · "
                    f"{settings.context_window} tokens · {settings.context_source}"
                )
            )
        else:
            console.print(
                Text(f"Perfil cadastrado. Use codaro models use ID --provider {name or kind}.")
            )
    except (ValueError, OSError, ModelError) as exc:
        fail(exc)


@providers_app.command("list")
def provider_list():
    """Lista perfis sem exibir suas chaves."""
    from codaro.llm.profiles import ProviderStore

    try:
        value = ProviderStore().load()
        table = Table("Perfil", "Provedor", "Modelo", "Ativo", "TLS")
        for name, profile in value["profiles"].items():
            table.add_row(
                name,
                profile["kind"],
                profile["model"] or "não selecionado",
                "sim" if value["active"] == name else "",
                "insecure" if profile["tls_insecure"] else "verificado",
            )
        console.print(table)
    except (ValueError, OSError) as exc:
        fail(exc)


@providers_app.command("remove")
def provider_remove(name: str):
    """Remove o perfil e sua credencial local."""
    from codaro.llm.profiles import ProviderStore

    try:
        ProviderStore().remove(name)
        typer.echo("Perfil removido.")
    except (ValueError, OSError) as exc:
        fail(exc)


@models_app.command("list")
def model_list(
    provider: Annotated[str | None, typer.Option(help="Nome do perfil; padrão é o ativo.")] = None,
    refresh: Annotated[bool, typer.Option(help="Atualiza o catálogo diretamente na API.")] = False,
):
    """Mostra o catálogo salvo ou atualiza pela API, sem executar o modelo."""
    from codaro.llm.profiles import ProviderStore

    try:
        model_table(ProviderStore().models(provider, refresh=refresh))
    except (ValueError, OSError, ModelError) as exc:
        fail(exc)


@models_app.command("use")
def model_use(
    model: str,
    provider: Annotated[str | None, typer.Option(help="Nome do perfil; padrão é o ativo.")] = None,
    context_window: ContextWindow = None,
):
    """Seleciona um modelo do catálogo e usa seus limites informados pela API."""
    from codaro.llm.profiles import ProviderStore

    try:
        store = ProviderStore()
        name, _ = store.profile(provider)
        settings = store.select(name, model, context_window=context_window)
        typer.echo(
            f"Ativo: {settings.provider_id} / {settings.model}\n"
            f"Contexto: {settings.context_window} · {settings.context_source}"
        )
    except (ValueError, OSError, ModelError) as exc:
        fail(exc)


def register(app):
    app.add_typer(providers_app, name="providers")
    app.add_typer(models_app, name="models")
