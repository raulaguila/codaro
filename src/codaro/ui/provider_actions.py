"""Provider screen transitions and activation, preserving the session draft/state."""

from dataclasses import replace

from textual.widgets import Static

from codaro.index import safe_preview
from codaro.llm import Settings, create_provider


def manage_provider_decision(app, result):
    if not result:
        return
    from codaro.llm.profiles import ProviderStore
    from codaro.ui.providers import ProviderSetup

    store = ProviderStore()
    if result.startswith("removed:"):
        if app.agent.provider.settings.provider_id == result.removeprefix("removed:"):
            app.provider_available = False
            app.mount_message(
                Static("Provedor ativo removido · selecione outro modelo.", classes="notice")
            )
        return
    profile = result.removeprefix("edit:") if result.startswith("edit:") else None
    app.push_screen(
        ProviderSetup(store, profile=profile),
        lambda name: app.provider_edited(profile, name, store),
    )


def provider_edited(app, previous, name, store):
    if name and previous == app.agent.provider.settings.provider_id:
        try:
            app.activate_provider(store.active_settings(name), preserve_session=False)
        except (ValueError, OSError) as exc:
            app.provider_available = False
            app.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
    app.provider_registered(name, store)


def provider_registered(app, name, store=None):
    if name is not None:
        from codaro.ui.providers import ModelPicker

        app.push_screen(
            ModelPicker(store, profile=name, onboarding=app.setup_required),
            app.activate_provider,
        )
    elif app.setup_required:
        app.query_one("#status", Static).update("Configuração pendente · Ctrl+P para começar")


def activate_provider(app, settings: Settings | None, *, preserve_session=True):
    if settings is None:
        if app.setup_required:
            app.query_one("#status", Static).update("Selecione um modelo para começar")
        return
    try:
        current = app.agent.provider.settings
        if (
            preserve_session
            and current.provider_id == settings.provider_id
            and current.base_url == settings.base_url
        ):
            settings = replace(
                settings,
                tls_insecure=current.tls_insecure,
                timeout=current.timeout,
                token_encoding=current.token_encoding,
            )
        app.agent.set_provider(create_provider(settings))
        app.provider_available = True
        app.setup_required = False
        app.hide_welcome()
        app.session.secret = settings.api_key
        app.session.redact = app.agent.memory.redact
        app.session_turns = app.session.redact(app.session_turns)
        app.context_limit = app.agent.adaptive_input_limit
        app.context_chars = app.context_tokens = 0
        app.reported_tokens = None
        app.update_session_header()
        app.query_one("#context-meter", Static).update("")
        app.query_one(
            "#context-meter", Static
        ).tooltip = f"Janela: {settings.context_window:,} tokens · {settings.context_source}"
        app.query_one("#status", Static).update(
            safe_preview("Modelo selecionado · " + settings.model)
        )
    except (ValueError, OSError) as exc:
        app.mount_message(Static(safe_preview(str(exc)), classes="notice", markup=False))
