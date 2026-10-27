"""A compact, current map built from the incrementally updated source index."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tomllib
from collections import Counter
from pathlib import PurePosixPath

from codaro.index import CodeIndex
from codaro.languages import language_for, parser_available
from codaro.retrieval import matches_scope, scope_options

MANIFESTS = {
    "pyproject.toml",
    "package.json",
    "go.mod",
    "cargo.toml",
    "pom.xml",
    "setup.py",
    "makefile",
    "dockerfile",
    "compose.yml",
    "compose.yaml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "composer.json",
    "gemfile",
    "mix.exs",
    "pubspec.yaml",
    "meson.build",
    "cmakelists.txt",
    "justfile",
}
ENTRIES = {
    "main.py",
    "__main__.py",
    "cli.py",
    "main.go",
    "main.rs",
    "main.ts",
    "main.js",
    "index.ts",
    "index.js",
    "app.py",
    "server.ts",
    "server.js",
    "manage.py",
}


class ProjectMap:
    def __init__(self):
        self.digest = ""
        self.cached: dict = {}

    def build(self, index: CodeIndex, *, refresh=True, path=None) -> dict:
        if refresh:
            index.update()
        filters = scope_options(path=path)
        files = [
            row
            for row in index.db.execute("SELECT path,digest FROM files ORDER BY path")
            if matches_scope(row[0], filters)
        ]
        digest = hashlib.sha256(repr([(row[0], row[1]) for row in files]).encode()).hexdigest()
        if digest != self.digest:
            modules = Counter()
            languages = Counter()
            manifests, entries = [], []
            file_languages = {
                row[0]: row[1]
                for row in index.db.execute("SELECT path,MAX(language) FROM chunks GROUP BY path")
            }
            for row in files:
                path = PurePosixPath(row[0])
                modules[path.parts[0] if len(path.parts) > 1 else "."] += 1
                languages[file_languages.get(str(path), language_for(path))] += 1
                if path.name.lower() in MANIFESTS or path.suffix.lower() in {
                    ".csproj",
                    ".fsproj",
                    ".sln",
                }:
                    manifests.append(str(path))
                if path.name.lower() in ENTRIES:
                    entries.append({"path": str(path), "kind": "filename_candidate"})
            # Structural candidates do not prove runtime execution.
            definitions = list(
                index.db.execute(
                    "SELECT DISTINCT path,symbol,declaration_start FROM chunks "
                    "WHERE (lower(symbol) IN ('main','cli','app','bootstrap') "
                    "OR lower(symbol) LIKE '%.main' OR lower(symbol) LIKE '%.bootstrap') "
                    "AND declaration_start IS NOT NULL ORDER BY path,declaration_start LIMIT 12"
                )
            )
            self.cached = {
                "digest": digest,
                "files": len(files),
                "modules": [
                    {"path": name, "files": count} for name, count in modules.most_common(12)
                ],
                "languages": dict(languages.most_common()),
                "structural_languages": [name for name in languages if parser_available(name)],
                "manifests": sorted(manifests, key=lambda p: (p.count("/"), p))[:12],
                "entrypoint_candidates": sorted(
                    entries, key=lambda p: (p["path"].count("/"), p["path"])
                )[:12],
                "definitions": [
                    {"path": row[0], "symbol": row[1], "line": row[2]} for row in definitions
                ],
                "source": "index_metadata_not_implementation_evidence",
                "notice": "Leia os arquivos candidatos antes de afirmar pontos de entrada.",
            }
            allowed = {row[0] for row in files}
            self.cached["definitions"] = [
                item for item in self.cached["definitions"] if item["path"] in allowed
            ]
            directories = Counter()
            for name in allowed:
                parent = PurePosixPath(name).parent
                while str(parent) != ".":
                    directories[str(parent)] += 1
                    parent = parent.parent
            self.cached["directories"] = [
                {"path": name, "files": count}
                for name, count in sorted(
                    directories.items(), key=lambda item: (item[0].count("/"), item[0])
                )[:160]
            ]
            ordered_paths = sorted(
                allowed,
                key=lambda name: (
                    PurePosixPath(name).name.lower() not in MANIFESTS,
                    name.startswith(("docs/", "tests/", "artifacts/")),
                    name.count("/"),
                    name,
                ),
            )
            self.cached["file_paths"] = ordered_paths[:160]
            self.cached["directory_tree"] = "\n".join(
                "  " * min(name.count("/"), 6)
                + name.rsplit("/", 1)[-1]
                + ("/" if name in directories else "")
                for name in sorted(set(self.cached["file_paths"]) | set(directories))[:240]
            )
            self.cached["packages"] = [
                inspect_manifest(index.repository, name) for name in self.cached["manifests"]
            ]
            package_roots = {
                item["name"]: item["directory"]
                for item in self.cached["packages"]
                if isinstance(item.get("name"), str)
            }
            links, external = [], set()
            for row in index.db.execute(
                "SELECT path,target,line,language FROM imports ORDER BY path,line LIMIT 10000"
            ):
                if row["path"] not in allowed:
                    continue
                target = resolve_import(
                    row["path"], row["target"], row["language"], allowed, package_roots
                )
                if target:
                    links.append(
                        {
                            "from": row["path"],
                            "to": target,
                            "line": row["line"],
                            "source": "static_path_candidate",
                        }
                    )
                else:
                    external.add(row["target"])
            self.cached["dependency_candidates"] = links[:160]
            self.cached["unresolved_imports"] = sorted(external)[:30]
            self.cached["truncated"] = (
                len(directories) > 160
                or len(links) > 160
                or len(manifests) > 12
                or len(allowed) > 160
            )
            self.digest = digest
        return self.cached

    def page(self, index, *, path=None, offset=0, limit=3000):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 200 <= limit <= 6000
        ):
            raise ValueError("Página do mapa inválida.")
        mapping = self.build(index, path=path)
        text = json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))
        return {
            "text": text[offset : offset + limit],
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(text) else None,
            "truncated": offset + limit < len(text),
            "digest": mapping["digest"],
            "source": "project_map_not_implementation_evidence",
        }


def inspect_manifest(repository, name):
    result = {
        "path": name,
        "directory": str(PurePosixPath(name).parent),
        "source": "manifest_declaration_not_execution",
    }
    try:
        text = repository.read_text(name)
        basename = PurePosixPath(name).name.lower()
        if basename == "package.json":
            data = json.loads(text)
            result.update(
                ecosystem="node",
                name=data.get("name"),
                scripts=data.get("scripts", {}),
                dependencies=list(data.get("dependencies", {}))[:30],
                workspaces=data.get("workspaces", []),
            )
        elif basename in {"pyproject.toml", "cargo.toml"}:
            data = tomllib.loads(text)
            if basename == "pyproject.toml":
                project = data.get("project", {})
                result.update(
                    ecosystem="python",
                    name=project.get("name"),
                    scripts=project.get("scripts", {}),
                    dependencies=project.get("dependencies", [])[:30],
                )
            else:
                result.update(
                    ecosystem="rust",
                    name=data.get("package", {}).get("name"),
                    dependencies=list(data.get("dependencies", {}))[:30],
                    workspaces=data.get("workspace", {}).get("members", []),
                )
        elif basename == "go.mod":
            match = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
            result.update(ecosystem="go", name=match[1] if match else None)
        elif basename == "pom.xml" or basename.startswith("build.gradle"):
            result["ecosystem"] = "java"
        elif name.lower().endswith((".csproj", ".fsproj", ".sln")):
            result["ecosystem"] = "dotnet"
        # Keep hostile/huge manifest values out of the automatically injected map.
        if len(json.dumps(result, ensure_ascii=False)) > 4000:
            result = {
                "path": name,
                "directory": result["directory"],
                "truncated": True,
                "notice": "Consulte o manifesto para detalhes.",
            }
    except (OSError, ValueError, TypeError, AttributeError):
        result["notice"] = "Manifesto não interpretado; consulte o arquivo."
    return result


def resolve_import(source, target, language, paths, package_roots=None):
    parent = posixpath.dirname(source)
    candidates = []
    for package, directory in sorted((package_roots or {}).items(), key=lambda item: -len(item[0])):
        if target == package or target.startswith(package + "/"):
            relative = posixpath.normpath(
                posixpath.join(directory, target[len(package) :].lstrip("/"))
            )
            if language == "go" and any(path.startswith(relative + "/") for path in paths):
                return relative + "/"
            if language in {"javascript", "typescript", "tsx"}:
                candidates.extend(
                    [
                        relative + ext
                        for ext in (".ts", ".tsx", ".js", "/index.ts", "/index.js", "/package.json")
                    ]
                )
    if language == "python":
        level = len(target) - len(target.lstrip("."))
        module = target.lstrip(".").replace(".", "/")
        if level:
            base = parent
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            candidates.append(posixpath.join(base, module))
        else:
            candidates.extend([module, "src/" + module])
        candidates = [item + suffix for item in candidates for suffix in (".py", "/__init__.py")]
    elif target.startswith(".") or language in {"c", "cpp"}:
        base = posixpath.normpath(posixpath.join(parent, target))
        candidates.extend(
            [
                base,
                *[
                    base + ext
                    for ext in (
                        ".ts",
                        ".tsx",
                        ".js",
                        ".jsx",
                        ".mjs",
                        ".cjs",
                        "/index.ts",
                        "/index.js",
                    )
                ],
                target,
            ]
        )
    elif language == "rust":
        module = target.replace("crate::", "").replace("::", "/")
        candidates.extend(
            [
                posixpath.join(parent, module + ".rs"),
                posixpath.join(parent, module, "mod.rs"),
                "src/" + module + ".rs",
            ]
        )
    elif language in {"java", "kotlin"}:
        suffix = target.replace(".", "/") + (".java" if language == "java" else ".kt")
        candidates.extend(sorted(path for path in paths if path.endswith(suffix)))
    return next((candidate for candidate in candidates if candidate in paths), None)
