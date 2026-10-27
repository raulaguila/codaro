"""Regenerate the documented catalog from the registry; --check is read-only."""

import argparse
from pathlib import Path

from codaro.languages import ALIASES, EXTENSIONS, FILENAMES, SUPPORTED_LANGUAGES, language_members

START = "<!-- language-catalog:start -->"
END = "<!-- language-catalog:end -->"


def render_catalog():
    rows = [
        START,
        "| Filtro | Extensões | Nomes de arquivo | Aliases |",
        "| --- | --- | --- | --- |",
    ]
    for language in SUPPORTED_LANGUAGES:
        suffixes = dict.fromkeys(
            suffix for member in language_members(language) for suffix in EXTENSIONS.get(member, ())
        )
        aliases = [alias for alias, target in ALIASES.items() if target == language]
        cells = [
            f"`{language}`",
            ", ".join(f"`{value}`" for value in suffixes) or "—",
            ", ".join(f"`{value}`" for value in FILENAMES.get(language, ())) or "—",
            ", ".join(f"`{value}`" for value in aliases) or "—",
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([*rows, END])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "docs/TOOLS.md"
    original = target.read_text(encoding="utf-8")
    before, remainder = original.split(START, 1)
    _, after = remainder.split(END, 1)
    updated = before + render_catalog() + after
    if args.check:
        if original != updated:
            parser.exit(
                1, "Catálogo desatualizado; execute python scripts/update_language_docs.py\n"
            )
    else:
        target.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
