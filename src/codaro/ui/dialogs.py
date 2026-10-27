"""Small terminal dialogs with fixed actions and human-readable permissions."""

import shlex

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Static

from codaro.ui.screen import DialogScreen as ModalScreen


class NewConversation(ModalScreen[str | None]):
    DEFAULT_CSS = """
    NewConversation { align: center middle; background: #000000 65%; }
    #new-conversation { width: 90%; max-width: 80; height: auto; max-height: 90%;
        padding: 1; border: round $accent; background: $surface; }
    #new-conversation Static { height: auto; }
    #new-conversation Horizontal { height: 3; }
    #new-conversation Button { width: 1fr; min-width: 10; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    def compose(self) -> ComposeResult:
        with Vertical(id="new-conversation"):
            yield Static("Começar outra conversa?", classes="dialog-title", markup=False)
            yield Static(
                "A conversa atual continuará disponível para retomada. "
                "Encerra o plano e descarta propostas pendentes. Arquivos alterados são mantidos.",
                markup=False,
            )
            yield Static("Seu rascunho será preservado ao voltar.", markup=False)
            with Horizontal():
                yield Button("Continuar aqui", id="cancel-conversation")
                yield Button("Nova conversa", id="begin-conversation", variant="primary")

    def on_mount(self):
        self.query_one("#cancel-conversation").focus()

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        self.dismiss("" if event.button.id == "begin-conversation" else None)


class ScopeFields:
    """Translate visible line entries to exact command argument arrays."""

    @staticmethod
    def parse(paths, commands):
        return {
            "paths": [line.strip() for line in paths.splitlines() if line.strip()],
            "commands": [shlex.split(line) for line in commands.splitlines() if line.strip()],
        }


class ProviderManager(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ProviderManager { align: center middle; background: #000000 65%; }
    #provider-manager { width: 90%; max-width: 90; height: 85%; padding: 1;
        border: round $accent; background: $surface; }
    #profile-list { height: 1fr; }
    .profile-row { height: auto; margin-bottom: 1; }
    .profile-row Static { height: auto; }
    .profile-actions { height: 3; }
    .profile-actions Button { width: 1fr; min-width: 8; }
    #manager-error { height: auto; }
    """
    BINDINGS = [Binding("escape", "back", "Voltar")]

    def __init__(self, store):
        super().__init__()
        self.store = store

    def compose(self):
        with Vertical(id="provider-manager"):
            yield Static("Gerenciar provedores · credenciais mascaradas", markup=False)
            with VerticalScroll(id="profile-list"):
                configured = self.store.load(resolve_secrets=False)
                for name, profile in configured["profiles"].items():
                    with Vertical(classes="profile-row"):
                        yield Static(
                            f"{name} {'· ativo' if name == configured['active'] else ''}\n"
                            f"{profile['base_url']}\n"
                            + (
                                "TLS sem verificação"
                                if profile["tls_insecure"]
                                else "TLS verificado"
                            ),
                            markup=False,
                        )
                        with Horizontal(classes="profile-actions"):
                            yield Button("Editar / testar", id=f"profile-edit-{name}")
                            yield Button("Remover", id=f"profile-remove-{name}")
            yield Static("", id="manager-error", markup=False)
            with Horizontal(classes="profile-actions"):
                yield Button("Cadastrar", id="profile-add")
                yield Button("Voltar", id="manager-back")

    def action_back(self):
        self.dismiss(None)

    def on_button_pressed(self, event):
        identifier = event.button.id or ""
        if identifier == "profile-add":
            self.dismiss("add")
        elif identifier.startswith("profile-edit-"):
            self.dismiss("edit:" + identifier.removeprefix("profile-edit-"))
        elif identifier.startswith("profile-remove-"):
            name = identifier.removeprefix("profile-remove-")
            if event.button.label.plain != "Confirmar remoção":
                event.button.label = "Confirmar remoção"
                self.query_one("#manager-error", Static).update(
                    "Confirme a remoção. Se ativo, será necessário selecionar outro modelo."
                )
            else:
                try:
                    self.store.remove(name)
                except (ValueError, OSError) as exc:
                    self.query_one("#manager-error", Static).update(str(exc))
                    return
                self.dismiss("removed:" + name)
        else:
            self.action_back()
