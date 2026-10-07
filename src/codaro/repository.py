from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

MAX_FILE_BYTES = 512_000
MAX_FILES = 20_000
SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".swift",
    ".scala",
    ".sh",
    ".sql",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".css",
    ".html",
    ".vue",
    ".svelte",
}
# Common manifests, build files and ignore rules do not have a source extension.
SOURCE_NAMES = {
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".codaroignore",
    ".editorconfig",
    "dockerfile",
    "containerfile",
    "makefile",
    "gnumakefile",
    "justfile",
    "procfile",
    "go.mod",
    "go.sum",
    "cargo.lock",
    "gemfile",
    "rakefile",
    "jenkinsfile",
    "cmakelists.txt",
    "readme",
    "license",
}
IGNORE_RULE_FILES = {".gitignore", ".dockerignore", ".codaroignore", ".gitattributes"}
EXCLUDED_DIRS = {
    ".git",
    ".codaro",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".aws",
    ".ssh",
    ".codex",
}


class RepositoryError(ValueError):
    """A readable error for filesystem or repository policy failures."""


class Repository:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        if not self.root.is_dir():
            raise RepositoryError(f"Diretório inexistente: {self.root}")
        if not shutil.which("rg"):
            raise RepositoryError("Instale ripgrep (rg) para explorar o repositório.")

    def _relative(self, path: Path) -> Path:
        # Do not resolve here: symlinks should be rejected, not silently followed.
        absolute = Path(os.path.abspath(path))
        try:
            relative = absolute.relative_to(self.root)
        except ValueError as exc:
            raise RepositoryError("Arquivo fora do projeto.") from exc
        if not relative.parts or any(ord(char) < 32 or ord(char) == 127 for char in str(relative)):
            raise RepositoryError("Caminho inválido ou com caracteres de controle.")
        return relative

    def allowed(self, path: Path) -> bool:
        try:
            relative = self._relative(path)
            current = self.root
            for part in relative.parts:
                current = current / part
                if current.is_symlink() or part in EXCLUDED_DIRS:
                    return False
        except (OSError, ValueError):
            return False
        name = relative.name.lower()
        if name == ".env" or name.startswith(".env."):
            return False
        if any(word in name for word in ("credential", "secret", "id_rsa", "id_ed25519")):
            return False
        return relative.suffix.lower() in SOURCE_EXTENSIONS or name in SOURCE_NAMES

    def files(self) -> list[Path]:
        args = ["rg", "--files", "--null", "--hidden", "--no-require-git"]
        for directory in sorted(EXCLUDED_DIRS):
            args.extend(["--glob", f"!**/{directory}/**"])
        ignore = self.root / ".codaroignore"
        if ignore.is_symlink():
            raise RepositoryError(".codaroignore não pode ser um link simbólico.")
        if ignore.is_file():
            args.extend(["--ignore-file", str(ignore)])
        try:
            result = subprocess.run(
                args,
                cwd=self.root,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RepositoryError(
                "Listagem excedeu 20 segundos. Reduza o escopo do projeto."
            ) from exc
        except OSError as exc:
            raise RepositoryError("Não foi possível iniciar ripgrep.") from exc
        if result.returncode not in (0, 1):
            raise RepositoryError("Não foi possível listar arquivos. Confira permissões e ignores.")
        paths = []
        for raw_name in result.stdout.split(b"\0"):
            if not raw_name:
                continue
            path = self.root / os.fsdecode(raw_name)
            try:
                info = path.lstat()
                if (
                    self.allowed(path)
                    and stat.S_ISREG(info.st_mode)
                    and info.st_size <= MAX_FILE_BYTES
                ):
                    paths.append(path)
                    if len(paths) > MAX_FILES:
                        raise RepositoryError("Projeto excede 20.000 arquivos. Use .codaroignore.")
            except OSError:
                continue
        return sorted(paths)

    def resolve_file(self, name: str) -> Path:
        path = self.root / self._relative(self.root / name)
        if not self.allowed(path):
            raise RepositoryError(
                "Arquivo bloqueado pela política de tipos, diretórios ou links. "
                "Use list_files para escolher um caminho permitido."
            )
        if path not in set(self.files()):
            raise RepositoryError(
                "Arquivo inexistente, grande demais ou excluído pelas regras de ignore. "
                "Use list_files para descobrir arquivos disponíveis; não adivinhe caminhos."
            )
        return path

    def resolve_destination(self, name: str) -> Path:
        """Evaluate a future path with ripgrep's ignore engine in a temporary mirror."""
        from codaro.storage import private_read

        if not isinstance(name, str) or not name or len(name) > 2000:
            raise RepositoryError("Destino inválido.")
        relative = self._relative(self.root / name)
        path = self.root / relative
        if not self.allowed(path):
            raise RepositoryError("Destino bloqueado pela política de arquivos.")
        if path.exists():
            return self.resolve_file(name)
        with tempfile.TemporaryDirectory(prefix="codaro-ignore-") as temporary:
            mirror = Path(temporary)
            for ancestor in (Path("."), *relative.parents):
                for ignore_name in (".gitignore", ".ignore", ".rgignore"):
                    source = self.root / ancestor / ignore_name
                    try:
                        data = private_read(source, 64_000)
                    except FileNotFoundError:
                        continue
                    except ValueError as exc:
                        raise RepositoryError("Arquivo de ignore inválido.") from exc
                    destination = mirror / ancestor / ignore_name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
            future = mirror / relative
            future.parent.mkdir(parents=True, exist_ok=True)
            future.touch()
            args = ["rg", "--files", "--null", "--hidden", "--no-require-git"]
            try:
                rules = private_read(self.root / ".codaroignore", 64_000)
            except FileNotFoundError:
                rules = None
            if rules is not None:
                (mirror / ".codaroignore").write_bytes(rules)
                args.extend(["--ignore-file", str(mirror / ".codaroignore")])
            result = subprocess.run(args, cwd=mirror, capture_output=True, timeout=20)
            if result.returncode not in (0, 1):
                raise RepositoryError("Não foi possível verificar os ignores do destino.")
            names = {os.fsdecode(item) for item in result.stdout.split(b"\0") if item}
            if relative.as_posix() not in names:
                raise RepositoryError("Destino excluído pelas regras de ignore.")
        return path

    def read_bytes(self, path: Path) -> bytes:
        """Bounded read; POSIX directory descriptors prevent symlink replacement escapes."""
        relative = self._relative(path)
        if not self.allowed(path):
            raise RepositoryError("Arquivo não permitido.")
        directory_fd = file_fd = None
        try:
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                directory_fd = os.open(self.root, flags)
                for component in relative.parts[:-1]:
                    next_fd = os.open(component, flags, dir_fd=directory_fd)
                    os.close(directory_fd)
                    directory_fd = next_fd
                file_fd = os.open(
                    relative.name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory_fd,
                )
            else:
                file_fd = os.open(self.root / relative, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise RepositoryError("Arquivo inválido ou maior que 512 KB.")
            with os.fdopen(file_fd, "rb") as stream:
                file_fd = None
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise RepositoryError("Arquivo cresceu além do limite de 512 KB.")
            if b"\0" in data:
                raise RepositoryError("Conteúdo binário não é suportado.")
            return data
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def read_text(self, name: str) -> str:
        path = self.resolve_file(name)
        try:
            return self.read_bytes(path).decode("utf-8-sig")
        except UnicodeError as exc:
            raise RepositoryError("O arquivo deve conter texto UTF-8.") from exc

    def read_lines(self, name: str, start: int = 1, end: int = 80) -> dict:
        return self.render_lines(name, self.read_text(name), start, end)

    @staticmethod
    def render_lines(name: str, text: str, start: int, end: int) -> dict:
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise RepositoryError("Intervalo de linhas inválido.")
        if end - start >= 160:
            raise RepositoryError("Solicite de 1 a 160 linhas.")
        # split on LF to agree with Tree-sitter row numbering (including CRLF files).
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not text:
            lines = []
        if start > len(lines) and lines:
            raise RepositoryError(f"Arquivo tem apenas {len(lines)} linhas.")
        if not lines and start != 1:
            raise RepositoryError("Arquivo vazio.")
        output = []
        size = 0
        truncated = False
        last_line = start - 1
        next_line = None
        partial_line = None
        for number, line in enumerate(lines[start - 1 : end], start):
            line = line.removesuffix("\r")
            # Keep tabs but neutralize terminal control sequences in code previews.
            line = "".join(c if c == "\t" or ord(c) >= 32 and ord(c) != 127 else "�" for c in line)
            rendered = f"{number}: {line}"
            available = 6000 - size - (1 if output else 0)
            if available <= 0:
                truncated = True
                next_line = number
                break
            if len(rendered) > available:
                output.append(rendered[:available])
                truncated = True
                last_line = number
                partial_line = number
                next_line = number
                break
            output.append(rendered)
            last_line = number
            size += len(rendered) + (1 if len(output) > 1 else 0)
        if next_line is None and last_line < len(lines):
            next_line = last_line + 1
        return {
            "path": name,
            "start_line": start,
            "end_line": last_line if lines else 0,
            "content": "\n".join(output),
            "truncated": truncated,
            "total_lines": len(lines),
            "next_start_line": next_line,
            "partial_line": partial_line,
        }
