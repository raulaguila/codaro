from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from codaro.llm.protocol import MAX_STREAM_BYTES


def stream_limit():
    try:
        value = int(os.getenv("CODARO_MAX_STREAM_MB", str(MAX_STREAM_BYTES // (1024 * 1024))))
    except ValueError as exc:
        raise ValueError("CODARO_MAX_STREAM_MB deve ser inteiro entre 1 e 128.") from exc
    if not 1 <= value <= 128:
        raise ValueError("CODARO_MAX_STREAM_MB deve ficar entre 1 e 128.")
    return value * 1024 * 1024


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
    max_stream_bytes: int = field(default_factory=stream_limit)

    @property
    def max_stream_events(self):
        return min(200_000, self.max_stream_bytes // 64)

    def __post_init__(self):
        if (
            type(self.max_stream_bytes) is not int
            or not 1024 * 1024 <= self.max_stream_bytes <= 128 * 1024 * 1024
        ):
            raise ValueError("Limite do stream deve ficar entre 1 e 128 MiB.")
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
        from codaro.configuration import resolve_configuration

        return resolve_configuration(tls_insecure=tls_insecure, context_window=context_window).model
