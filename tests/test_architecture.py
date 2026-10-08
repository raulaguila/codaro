"""Keep orchestration and transport independent of terminal presentation."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "codaro"
LEGACY = {
    "codaro.provider",
    "codaro.providers",
    "codaro.anthropic",
    "codaro.ollama",
    "codaro.tui",
    "codaro.provider_ui",
    "codaro.ux_screens",
    "codaro.extension_screens",
    "codaro.extensions_cli",
}


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


@pytest.mark.parametrize("package", ["agent", "llm", "ui", "cli_commands"])
def test_packages_use_canonical_modules(package):
    violations = [
        f"{path.relative_to(SOURCE)}: {module}"
        for path in (SOURCE / package).rglob("*.py")
        for module in imports(path)
        if module in LEGACY
    ]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("package", "forbidden"),
    [
        ("agent", ("codaro.ui", "codaro.cli", "codaro.cli_commands", "textual", "rich")),
        ("llm", ("codaro.agent", "codaro.ui", "codaro.cli", "codaro.cli_commands", "textual")),
    ],
)
def test_core_packages_do_not_depend_on_interfaces(package, forbidden):
    violations = [
        f"{path.relative_to(SOURCE)}: {module}"
        for path in (SOURCE / package).rglob("*.py")
        for module in imports(path)
        if any(module == name or module.startswith(name + ".") for name in forbidden)
    ]
    assert not violations, "\n".join(violations)
