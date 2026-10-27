from __future__ import annotations

from pathlib import Path


def short_path(root: Path, limit: int = 40) -> str:
    try:
        relative = root.relative_to(Path.home())
        display = "~" if relative == Path(".") else "~/" + relative.as_posix()
    except ValueError:
        display = str(root)
    return display if len(display) <= limit else "…" + display[-(limit - 1) :]


def transport_label(base_url, tls_insecure=False):
    if base_url.lower().startswith("http://"):
        return "HTTP"
    return "HTTPS · sem verificar certificado" if tls_insecure else "HTTPS · verificação habilitada"
