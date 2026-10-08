from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    base_url: str
    model: str
    api_key: str = field(default="", repr=False)
    timeout: float = 90.0
    tls_insecure: bool = False
    context_window: int = 16_384
    max_output_tokens: int | None = None
    token_encoding: str | None = None
    provider_id: str = ""
    context_source: str = "configuração padrão/ambiente"
    api_style: str = "openai"
    model_max_output_tokens: int | None = None
    include_stream_usage: bool = False

    def __post_init__(self):
        if self.api_style not in {"openai", "anthropic", "ollama"}:
            raise ValueError("API do provedor inválida.")
        if len(self.api_key) > 16384:
            raise ValueError("Credencial excede o limite permitido.")
        if (
            type(self.context_window) is not int
            or not 4096 <= self.context_window <= 2_000_000
            or (
                self.max_output_tokens is not None
                and (
                    type(self.max_output_tokens) is not int
                    or not 1 <= self.max_output_tokens <= 32_768
                    or self.max_output_tokens + 512 >= self.context_window
                )
            )
        ):
            raise ValueError("Janela de contexto/limite de saída inválidos; reserve 512 tokens.")
        if self.model_max_output_tokens is not None and (
            type(self.model_max_output_tokens) is not int or self.model_max_output_tokens < 1
        ):
            raise ValueError("Limite de saída do modelo inválido.")
        if self.max_output_tokens is not None and self.model_max_output_tokens is not None:
            object.__setattr__(
                self, "max_output_tokens", min(self.max_output_tokens, self.model_max_output_tokens)
            )
        if self.token_encoding not in (None, "cl100k_base", "o200k_base"):
            raise ValueError("CODARO_TOKEN_ENCODING deve ser cl100k_base ou o200k_base.")
        if type(self.include_stream_usage) is not bool:
            raise ValueError("Stream usage deve ser booleano.")
        if type(self.tls_insecure) is not bool:
            raise ValueError("TLS insecure deve ser booleano.")
        try:
            parsed = urlsplit(self.base_url)
            valid_port = parsed.port is None or 0 < parsed.port < 65536
        except ValueError as exc:
            raise ValueError("CODARO_BASE_URL inválida.") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not valid_port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ord(c) <= 32 or ord(c) == 127 for c in self.base_url)
        ):
            raise ValueError("Use uma URL HTTP(S), sem credenciais, query ou fragmento.")
        if (
            not self.model.strip()
            or len(self.model) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in self.model)
        ):
            raise ValueError("CODARO_MODEL deve conter um nome de modelo válido.")
        if not 1 <= self.timeout <= 300:
            raise ValueError("CODARO_TIMEOUT deve ficar entre 1 e 300 segundos.")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.api_key):
            raise ValueError("Credencial contém caracteres inválidos.")

    @property
    def output_reserve(self) -> int:
        """Planning headroom, separate from the optional wire generation limit."""
        if self.max_output_tokens is not None:
            return min(self.max_output_tokens, self.model_max_output_tokens or 32_768)
        return min(8192, self.context_window // 4, self.model_max_output_tokens or 8192)

    @classmethod
    def from_env(
        cls, *, tls_insecure: bool | None = None, context_window: int | None = None
    ) -> Settings:
        from codaro.llm.profiles import ProviderStore

        store, selected = ProviderStore(), os.getenv("CODARO_PROVIDER", "").strip()
        configured = None
        if selected or not any(
            os.getenv(name) for name in ("CODARO_BASE_URL", "CODARO_MODEL", "CODARO_API_KEY")
        ):
            if selected or store.load()["active"]:
                configured = store.active_settings(selected or None)
        if tls_insecure is None:
            raw = (
                os.getenv(
                    "CODARO_TLS_INSECURE", str(configured.tls_insecure) if configured else "false"
                )
                .strip()
                .lower()
            )
            values = {
                "1": True,
                "true": True,
                "yes": True,
                "on": True,
                "0": False,
                "false": False,
                "no": False,
                "off": False,
            }
            if raw not in values:
                raise ValueError("CODARO_TLS_INSECURE deve ser true/false ou 1/0.")
            tls_insecure = values[raw]
        try:
            timeout = float(os.getenv("CODARO_TIMEOUT", "90"))
        except ValueError as exc:
            raise ValueError("CODARO_TIMEOUT deve ser um número.") from exc
        try:
            window = (
                context_window
                if context_window is not None
                else int(
                    os.getenv(
                        "CODARO_CONTEXT_WINDOW",
                        str(configured.context_window) if configured else "16384",
                    )
                )
            )
            raw_output = os.getenv(
                "CODARO_MAX_OUTPUT_TOKENS",
                str(configured.max_output_tokens)
                if configured and configured.max_output_tokens is not None
                else "auto",
            )
            output = None if raw_output.strip().casefold() in {"", "auto"} else int(raw_output)
        except ValueError as exc:
            raise ValueError(
                "Use inteiros para CODARO_CONTEXT_WINDOW e CODARO_MAX_OUTPUT_TOKENS; "
                "a saída também aceita auto."
            ) from exc
        if output is not None and configured and configured.model_max_output_tokens:
            output = min(output, configured.model_max_output_tokens, 32768)
        return cls(
            base_url=configured.base_url
            if configured
            else os.getenv("CODARO_BASE_URL", "http://localhost:11434/v1").strip().rstrip("/"),
            model=configured.model
            if configured
            else os.getenv("CODARO_MODEL", "qwen2.5:7b").strip(),
            api_key=configured.api_key if configured else os.getenv("CODARO_API_KEY", "").strip(),
            timeout=timeout,
            tls_insecure=tls_insecure,
            context_window=window,
            max_output_tokens=output,
            token_encoding=os.getenv("CODARO_TOKEN_ENCODING", "").strip() or None,
            provider_id=configured.provider_id if configured else "",
            api_style=configured.api_style if configured else "openai",
            model_max_output_tokens=configured.model_max_output_tokens if configured else None,
            include_stream_usage=configured.include_stream_usage if configured else False,
            context_source=(
                "configuração do usuário"
                if context_window is not None or os.getenv("CODARO_CONTEXT_WINDOW")
                else configured.context_source
                if configured
                else "configuração padrão/ambiente"
            ),
        )
