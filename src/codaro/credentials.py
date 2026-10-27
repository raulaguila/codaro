"""Opt-in native OS vault; plaintext fallback is never implicit."""

import uuid


class CredentialVault:
    service = "codaro.provider"

    def __init__(self, backend=None):
        self.backend = backend

    def native(self):
        if self.backend is None:
            try:
                import keyring

                candidate = keyring.get_keyring()
                options = getattr(candidate, "backends", [candidate])
                candidate = next(
                    (
                        item
                        for item in options
                        if type(item).__module__.startswith(
                            (
                                "keyring.backends.macOS",
                                "keyring.backends.Windows",
                                "keyring.backends.SecretService",
                                "keyring.backends.kwallet",
                            )
                        )
                        and item.priority > 0
                    ),
                    None,
                )
                if candidate is None:
                    raise ValueError(
                        "Nenhum cofre nativo disponível. Desbloqueie/configure o cofre do sistema."
                    )
                self.backend = candidate
            except ImportError as exc:
                raise ValueError(
                    "Instale codaro[credentials] para usar o cofre do sistema."
                ) from exc
        return self.backend

    def create(self, secret):
        reference = uuid.uuid4().hex
        backend = self.native()
        try:
            backend.set_password(self.service, reference, secret)
        except Exception as exc:
            raise ValueError(
                "Não foi possível salvar no cofre do sistema; configuração anterior preservada."
            ) from exc
        return reference

    def get(self, reference):
        backend = self.native()
        try:
            secret = backend.get_password(self.service, reference)
        except Exception as exc:
            raise ValueError(
                "Cofre indisponível ou bloqueado; nenhuma credencial alternativa foi usada."
            ) from exc
        if not isinstance(secret, str):
            raise ValueError("Credencial não encontrada no cofre do sistema.")
        return secret

    def delete(self, reference):
        try:
            self.native().delete_password(self.service, reference)
        except Exception:
            # An orphan vault entry is preferable to corrupting committed metadata.
            return False
        return True
