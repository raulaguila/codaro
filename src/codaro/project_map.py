"""A compact, current map built from the incrementally updated source index."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import PurePosixPath

from codaro.index import CodeIndex

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

    def build(self, index: CodeIndex, *, refresh=True) -> dict:
        if refresh:
            index.update()
        files = list(index.db.execute("SELECT path,digest FROM files ORDER BY path"))
        digest = hashlib.sha256(repr([(row[0], row[1]) for row in files]).encode()).hexdigest()
        if digest != self.digest:
            modules = Counter()
            languages = Counter()
            manifests, entries = [], []
            for row in files:
                path = PurePosixPath(row[0])
                modules[path.parts[0] if len(path.parts) > 1 else "."] += 1
                languages[path.suffix or "extensionless"] += 1
                if path.name.lower() in MANIFESTS:
                    manifests.append(str(path))
                if path.name.lower() in ENTRIES:
                    entries.append({"path": str(path), "kind": "filename_candidate"})
            # Python definitions are structural candidates, not runtime execution proof.
            definitions = list(
                index.db.execute(
                    "SELECT DISTINCT path,symbol,declaration_start FROM chunks "
                    "WHERE symbol IN ('main','cli','app','bootstrap') "
                    "AND declaration_start IS NOT NULL ORDER BY path,declaration_start LIMIT 12"
                )
            )
            self.cached = {
                "digest": digest,
                "files": len(files),
                "modules": [
                    {"path": name, "files": count} for name, count in modules.most_common(12)
                ],
                "languages": dict(languages.most_common(10)),
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
            self.digest = digest
        return self.cached
