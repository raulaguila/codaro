"""Optional Linux execution boundary. Required mode never falls back to host execution."""

import os
import shutil
import sys
from pathlib import Path


def sandbox_command(root, argv, environ):
    mode = os.environ.get("CODARO_SANDBOX", "off").strip().lower()
    if mode not in {"off", "required"}:
        raise ValueError("CODARO_SANDBOX deve ser off ou required.")
    if mode == "off":
        return argv, "off"
    executable = shutil.which("bwrap") if sys.platform.startswith("linux") else None
    if executable is None:
        raise ValueError("Sandbox obrigatória indisponível: requer Linux e bubblewrap instalado.")
    root = Path(root).resolve()
    home = Path.home().resolve()
    if root == Path(root.anchor) or root == home or root in home.parents:
        raise ValueError(
            "Escolha um projeto específico para a sandbox; não use a raiz ou seu home."
        )
    prefix = [executable, "--die-with-parent", "--unshare-all", "--new-session"]
    runtime = [Path(p) for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64") if Path(p).exists()]
    for prefix_path in (sys.base_prefix, sys.prefix):
        base = Path(prefix_path).resolve()
        if not any(base == path or path in base.parents for path in runtime):
            runtime.append(base)
    for path in runtime:
        prefix.extend(["--ro-bind", str(path), str(path)])
    for path in (
        "/etc/ssl",
        "/etc/ld.so.cache",
        "/etc/hosts",
        "/etc/resolv.conf",
        "/etc/nsswitch.conf",
    ):
        if Path(path).exists():
            prefix.extend(["--ro-bind", path, path])
    prefix.extend(
        [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/tmp/codaro-home",
            "--setenv",
            "HOME",
            "/tmp/codaro-home",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--bind",
            str(root),
            str(root),
            "--chdir",
            str(root),
            "--",
            *argv,
        ]
    )
    return prefix, "bubblewrap"
