from __future__ import annotations

from pathlib import Path


def short_path(root: Path, limit: int = 40) -> str:
    try:
        relative = root.relative_to(Path.home())
        display = "~" if relative == Path(".") else "~/" + relative.as_posix()
    except ValueError:
        display = str(root)
    return display if len(display) <= limit else "…" + display[-(limit - 1) :]
