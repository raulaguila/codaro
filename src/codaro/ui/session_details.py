"""Human-readable session information, separate from the conversation transcript."""

from codaro.interaction import COMMANDS
from codaro.ui.details import DetailsDialog


def compact_tokens(value):
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def session_dialog(app):
    agent = app.agent
    settings = agent.provider.settings
    provisional = settings.context_source.startswith("fallback")
    rows = [
        ("Projeto", str(agent.repository.root)),
        ("Modelo", settings.model),
        ("Modo", agent.mode.label),
        (
            "Janela total",
            "Não informada pela API" if provisional else f"{agent.context_window:,} tokens",
        ),
        (
            "Orçamento de entrada",
            f"{app.context_limit:,} tokens" + (" · provisório" if provisional else ""),
        ),
        ("Entrada estimada", f"≈ {app.context_tokens:,} tokens"),
        ("Origem", settings.context_source),
        ("Reserva de saída", f"{agent.max_output_tokens:,} tokens · limite de saída automático"),
        ("Margem", "512 tokens"),
        (
            "Edições",
            "Automáticas nesta conversa" if agent.policy.auto_edits else "Pedir autorização",
        ),
        ("Comandos e remoções", "Exigem autorização"),
        ("Diagnóstico local", ".codaro/prompt.json"),
    ]
    advanced = (
        f"Contagem: {agent.counter.method}\n"
        "Tokens informados pelo servidor: "
        f"{app.reported_tokens if app.reported_tokens is not None else 'Não informados'}\n"
        f"Calibração: {agent.counter.scale:.2f} · {len(agent.counter.samples)} amostras\n"
        f"Sessão: {app.session.path}\nTurnos: {len(app.session_turns)}"
    )
    return DetailsDialog("Detalhes da sessão", rows, advanced=advanced)


def help_dialog(app):
    rows = [
        ("Abrir comandos", "/ ou " + app.shortcut_display("p")),
        ("Selecionar opção", "↑↓ e Enter"),
        ("Completar comando", "Tab"),
        ("Fechar / pausar pergunta", "Esc"),
        ("Enviar mensagem", "Enter"),
        ("Nova linha", app.option_display("Enter")),
        ("Interromper execução", app.shortcut_display("x")),
        ("Referenciar arquivo", '@src/main.py ou @"pasta com espaços/main.py"'),
        ("Detalhes da sessão", "/status"),
    ]
    advanced = "\n".join(f"{command} · {description}" for command, description in COMMANDS.items())
    if app.is_macos:
        advanced += "\n⌘ depende do suporte do terminal; use Ctrl como alternativa. ⌥ = Option."
    return DetailsDialog("Comandos e atalhos", rows, advanced=advanced)
