"""Localized command palette and callbacks for the terminal application."""

from textual.app import SystemCommand


def system_commands(app, framework_commands):
    yield SystemCommand(
        "Ajuda",
        "/help · Comandos e atalhos",
        lambda: app.run_worker(app.open_provider_menu("/help")),
    )
    if app.busy:
        yield SystemCommand(
            "Cancelar atividade",
            "Solicitar interrupção; rascunho preservado",
            app.action_cancel,
        )
        return
    actions = {
        "Cadastrar provedor": (
            "/providers",
            "Cadastrar API key e listar modelos do provedor (BYOK).",
        ),
        "Gerenciar provedores": (
            "/provider-manage",
            "Editar, testar, renomear e remover perfis.",
        ),
        "Selecionar provedor e modelo": (
            "/models",
            "Escolher entre os provedores cadastrados e seus modelos.",
        ),
        "Perguntar": ("/ask", "Consultar sem modificar arquivos"),
        "Planejar": ("/plan", "Investigar e definir etapas"),
        "Executar": ("/execute", "Implementar e validar com aprovação"),
        "Retomar conversa": ("/resume", "Retomar a última conversa salva"),
        "Revisar alterações": ("/changes", "Ver alterações e checkpoints"),
        "Permissões por tarefa": (
            "/permissions task",
            "Autorizar caminhos e comandos da tarefa ativa",
        ),
        "Revogar escopo": ("/permissions action", "Voltar à aprovação por ação"),
        "Contexto": ("/status", "Orçamento, origem e contagem"),
        "Recalibrar contexto": ("/recalibrate", "Reaprender limites do servidor"),
        "Compactar contexto": ("/compact", "Reduzir histórico enviado ao modelo"),
        "Cadastrar MCP ou plugin": (
            "/integrations",
            "Confiar, testar e cadastrar uma integração",
        ),
        "Desfazer interação": ("/undo-turn", "Revisar reversão de todos os arquivos"),
        "Refazer interação": ("/redo", "Revisar reaplicação da interação desfeita"),
        "Sessões": ("/sessions", "Conversas independentes neste projeto"),
        "Funcionalidades e integrações": (
            "/features",
            "Contexto, exploração, MCP, plugins e LSP",
        ),
        "Histórico": ("/history", "Consultar a memória da conversa"),
        "Tarefas": ("/task list", "Ver tarefas deste projeto"),
        "Desfazer limpeza": ("/restore-clear", "Restaurar mensagens e contexto"),
    }
    for title, (command, description) in actions.items():
        yield SystemCommand(
            title,
            command + " · " + description,
            lambda cmd=command: app.run_worker(app.open_provider_menu(cmd)),
        )
    yield SystemCommand(
        "Nova conversa e tarefa",
        "/new · Revogar escopo e começar outra atividade",
        app.action_new_conversation,
    )
    yield SystemCommand(
        "Limpar mensagens",
        "/clear · Manter tarefa, escopo e propostas; permite desfazer",
        lambda: app.run_worker(app.action_clear_chat()),
    )
    yield SystemCommand(
        "Mostrar/ocultar raciocínio",
        "/reasoning · Alternar prévias do modelo",
        app.toggle_reasoning,
    )
    # Localize the framework actions while retaining theme and keyboard functionality.
    labels = {
        "Theme": ("Tema", "Escolher tema da interface"),
        "Quit": ("Sair", "Encerrar o Codaro"),
        "Keys": ("Atalhos", "Mostrar teclas disponíveis"),
        "Maximize": ("Ampliar painel", "Ampliar elemento atual"),
        "Screenshot": ("Capturar tela", "Salvar captura SVG"),
    }
    for item in framework_commands:
        if item.title in labels:
            title, help_text = labels[item.title]
            yield SystemCommand(title, help_text, item.callback)
