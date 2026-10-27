"""Native clipboard routing must never target a server's desktop over SSH."""

import subprocess
from types import SimpleNamespace

import pytest

from codaro import clipboard

TEXT = "Resposta com acentuação: ação 🎯\n```python\nprint('olá')\n```\n"


@pytest.fixture(autouse=True)
def clipboard_environment(monkeypatch):
    for key in (
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SSH_TTY",
        "WSL_INTEROP",
        "WSL_DISTRO_NAME",
        "WAYLAND_DISPLAY",
        "DISPLAY",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    "platform,env,command,arguments,encoding",
    [
        ("darwin", {}, "pbcopy", [], "utf-8"),
        ("win32", {}, "clip.exe", [], "utf-16"),
        ("linux", {"WSL_DISTRO_NAME": "Ubuntu"}, "clip.exe", [], "utf-16"),
        (
            "linux",
            {"WAYLAND_DISPLAY": "wayland-0"},
            "wl-copy",
            ["--type", "text/plain;charset=utf-8"],
            "utf-8",
        ),
        ("linux", {"DISPLAY": ":0"}, "xclip", ["-selection", "clipboard", "-in"], "utf-8"),
        ("linux", {"DISPLAY": ":0"}, "xsel", ["--clipboard", "--input"], "utf-8"),
    ],
)
def test_desktop_helper_receives_exact_unicode_text_via_stdin(
    monkeypatch, platform, env, command, arguments, encoding
):
    monkeypatch.setattr(clipboard.sys, "platform", platform)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        clipboard.shutil, "which", lambda name: f"/helpers/{name}" if name == command else None
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.copy_native(TEXT)
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv == [f"/helpers/{command}", *arguments]
    assert options["input"].decode(encoding) == TEXT
    assert TEXT not in argv and not options.get("shell")
    assert options["timeout"] == 1
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL


@pytest.mark.parametrize("key", ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
def test_ssh_never_uses_a_remote_desktop_clipboard(monkeypatch, key):
    monkeypatch.setenv(key, "connected")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(clipboard.shutil, "which", lambda _: pytest.fail("Remote helper lookup"))
    assert not clipboard.copy_native(TEXT)


def test_headless_linux_does_not_attempt_desktop_commands(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", lambda _: pytest.fail("No local display"))
    assert not clipboard.copy_native(TEXT)


@pytest.mark.parametrize("failure", ["missing", "exit", "timeout", "oserror"])
def test_unavailable_native_copy_never_reports_success(monkeypatch, failure):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    monkeypatch.setattr(
        clipboard.shutil, "which", lambda _: None if failure == "missing" else "pbcopy"
    )

    def run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if failure == "oserror":
            raise OSError("unavailable")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert not clipboard.copy_native(TEXT)


def test_failed_wayland_falls_back_to_x11_clipboard_selection(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name)
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=0 if argv[0] == "xsel" else 1)

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.copy_native(TEXT)
    assert commands == [
        ["wl-copy", "--type", "text/plain;charset=utf-8"],
        ["xclip", "-selection", "clipboard", "-in"],
        ["xsel", "--clipboard", "--input"],
    ]
