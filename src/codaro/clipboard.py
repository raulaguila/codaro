"""Best-effort native desktop clipboard; remote sessions must use their terminal."""

import os
import shutil
import subprocess
import sys


def copy_native(text: str) -> bool:
    """Return true only when a local clipboard helper completed successfully.

    No shell, response text in argv, clipboard reads or runtime installations.
    Call from a worker: desktop helpers can stall when the display is unavailable.
    """
    if any(os.environ.get(key) for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return False
    candidates = []
    if sys.platform == "darwin":
        candidates.append(("pbcopy", [], text.encode("utf-8")))
    elif sys.platform == "win32":
        candidates.append(("clip.exe", [], b"\xff\xfe" + text.encode("utf-16le")))
    elif sys.platform.startswith("linux"):
        if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
            candidates.append(("clip.exe", [], b"\xff\xfe" + text.encode("utf-16le")))
        if os.environ.get("WAYLAND_DISPLAY"):
            candidates.append(
                ("wl-copy", ["--type", "text/plain;charset=utf-8"], text.encode("utf-8"))
            )
        if os.environ.get("DISPLAY"):
            candidates.extend(
                [
                    ("xclip", ["-selection", "clipboard", "-in"], text.encode("utf-8")),
                    ("xsel", ["--clipboard", "--input"], text.encode("utf-8")),
                ]
            )
    for name, arguments, data in candidates:
        executable = shutil.which(name)
        if executable is None:
            continue
        try:
            result = subprocess.run(
                [executable, *arguments],
                input=data,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform == "win32"
                else 0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False
