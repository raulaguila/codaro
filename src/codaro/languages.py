"""Offline language discovery and conservative structural symbol extraction.

Grammars are bundled in the wheel: parsing never downloads or executes project code.
Unknown/unsupported syntax still has searchable text windows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tree_sitter_language_pack import get_parser

EXTENSIONS = {
    "python": (".py", ".pyi"),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".mts", ".cts"),
    "tsx": (".tsx",),
    "go": (".go",),
    "java": (".java",),
    "c": (".c", ".h"),
    "cpp": (".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
    "csharp": (".cs",),
    "rust": (".rs",),
    "sql": (".sql",),
    "ruby": (
        ".rb",
        ".rake",
    ),
    "php": (".php",),
    "kotlin": (".kt", ".kts"),
    "swift": (".swift",),
    "scala": (".scala", ".sc"),
    "bash": (".sh", ".bash", ".zsh"),
    "lua": (".lua",),
    "dart": (".dart",),
    "elixir": (".ex", ".exs"),
    "erlang": (".erl", ".hrl"),
    "haskell": (".hs",),
    "ocaml": (".ml", ".mli"),
    "r": (".r",),
    "julia": (".jl",),
    "perl": (".pl", ".pm"),
    "powershell": (".ps1", ".psm1"),
    "groovy": (".groovy", ".gradle"),
    "clojure": (".clj", ".cljs", ".cljc", ".edn"),
    "fsharp": (".fs", ".fsi", ".fsx"),
    "fortran": (".f", ".f90", ".f95"),
    "zig": (".zig",),
    "solidity": (".sol",),
    "proto": (".proto",),
    "graphql": (".graphql", ".gql"),
    "hcl": (".tf", ".hcl"),
    "html": (".html", ".htm"),
    "css": (".css",),
    "scss": (".scss",),
    "json": (".json",),
    "yaml": (".yaml", ".yml"),
    "toml": (".toml",),
    "vue": (".vue",),
    "svelte": (".svelte",),
    "markdown": (".md", ".mdx"),
    "objc": (".m", ".mm"),
    "cuda": (".cu", ".cuh"),
    "d": (".d",),
    "nim": (".nim",),
    "elm": (".elm",),
    "gleam": (".gleam",),
    "gdscript": (".gd",),
    "haxe": (".hx",),
    "apex": (".cls", ".trigger"),
    "ada": (".adb", ".ads"),
    "cobol": (".cob", ".cbl"),
    "pascal": (".pas", ".pp"),
    "matlab": (".matlab",),
    "asm": (".asm", ".s"),
    "verilog": (".v", ".sv", ".svh"),
    "vhdl": (".vhd", ".vhdl"),
    "glsl": (".glsl", ".vert", ".frag"),
    "hlsl": (".hlsl",),
    "wgsl": (".wgsl",),
    "nix": (".nix",),
    "bicep": (".bicep",),
    "prisma": (".prisma",),
    "cmake": (".cmake",),
    "xml": (".xml", ".csproj", ".fsproj", ".vbproj", ".props", ".targets"),
    "ini": (".ini", ".cfg"),
    "fish": (".fish",),
    "tcl": (".tcl",),
    "commonlisp": (".lisp", ".lsp"),
    "scheme": (".scm",),
    "racket": (".rkt",),
    "elisp": (".el",),
    "ocaml_interface": (".mli",),
    "rst": (".rst",),
}
BY_EXTENSION = {
    suffix: language for language, suffixes in EXTENSIONS.items() for suffix in suffixes
}
ALIASES = {
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "ts": "typescript",
    "c#": "csharp",
    "c++": "cpp",
    "py": "python",
    "rs": "rust",
    "shell": "bash",
    "protobuf": "proto",
    "golang": "go",
    "c-sharp": "csharp",
    "objective-c": "objc",
}
DECLARATIONS = {
    "function_definition": "function",
    "function_declaration": "function",
    "function_item": "function",
    "function": "function",
    "method_declaration": "method",
    "method_definition": "method",
    "method": "method",
    "singleton_method": "method",
    "constructor_declaration": "constructor",
    "class_definition": "class",
    "class_declaration": "class",
    "class_specifier": "class",
    "class": "class",
    "struct_item": "struct",
    "struct_specifier": "struct",
    "struct_declaration": "struct",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "enum_item": "enum",
    "enum_specifier": "enum",
    "trait_item": "trait",
    "trait_declaration": "trait",
    "type_alias_declaration": "type",
    "type_item": "type",
    "type_spec": "type",
    "namespace_definition": "namespace",
    "namespace_declaration": "namespace",
    "module_definition": "module",
    "module": "module",
    "object_definition": "object",
    "object_declaration": "object",
    "create_table": "table",
    "create_view": "view",
    "create_function": "function",
    "create_procedure": "procedure",
    "create_index": "index",
    "message": "message",
    "service": "service",
    "function_signature": "function",
    "function_statement": "function",
    "local_function": "function",
    "type_declaration": "type",
    "contract_declaration": "class",
    "record_declaration": "class",
    "subroutine": "function",
    "subroutine_declaration_statement": "function",
    "fun_decl": "function",
    "value_definition": "function",
    "function_or_value_defn": "function",
    "object_type_definition": "type",
    "input_object_type_definition": "type",
    "enum_type_definition": "enum",
    "interface_type_definition": "interface",
    "named_module": "module",
    "procedure_declaration": "function",
    "procedure_definition": "function",
}


def language_for(path: str | Path, text: str = "") -> str:
    path = Path(path)
    if path.name.lower() in {"dockerfile", "containerfile"}:
        return "dockerfile"
    if path.name.lower() in {"makefile", "gnumakefile"}:
        return "make"
    if path.name.lower() == "cmakelists.txt":
        return "cmake"
    if path.suffix.lower() == ".h" and re.search(
        r"(?m)^\s*(?:namespace\s+\w|class\s+\w|template\s*<)", text
    ):
        return "cpp"
    return BY_EXTENSION.get(path.suffix.lower(), "text")


@lru_cache(maxsize=64)
def parser_available(language: str) -> bool:
    try:
        get_parser(language)
        return True
    except (LookupError, ValueError, ImportError, OSError):
        return False


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    start: int
    end: int
    signature: str
    language: str


def _text(node):
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _name(node, language):
    name = node.child_by_field_name("name")
    if name is None and node.type == "impl_item":
        name = node.child_by_field_name("type")
    if name is None and node.type in {"function_definition", "function_declaration"}:
        declaration = node.child_by_field_name("declarator")
        while declaration is not None:
            child = declaration.child_by_field_name("declarator")
            if child is None:
                name = declaration
                break
            declaration = child
    if name is None and language == "sql":
        name = next(
            (child for child in node.named_children if child.type == "object_reference"), None
        )
    if name is None and node.type in {
        "message",
        "service",
        "object_type_definition",
        "input_object_type_definition",
        "enum_type_definition",
        "interface_type_definition",
        "function_statement",
    }:
        name = next(
            (
                child
                for child in node.named_children
                if child.type in {"message_name", "service_name", "name", "function_name"}
            ),
            None,
        )
    if name is None and node.type == "subroutine":
        header = next(
            (child for child in node.named_children if child.type == "subroutine_statement"), None
        )
        name = header.child_by_field_name("name") if header else None
    if name is None and node.type == "fun_decl":
        clause = node.child_by_field_name("clause")
        name = clause.child_by_field_name("name") if clause else None
    if name is None and node.type == "value_definition":
        binding = next(
            (child for child in node.named_children if child.type == "let_binding"), None
        )
        name = binding.child_by_field_name("pattern") if binding else None
    if name is None and node.type == "function_or_value_defn":
        left = next(
            (
                child
                for child in node.named_children
                if child.type in {"value_declaration_left", "function_declaration_left"}
            ),
            None,
        )
        name = left.named_children[0] if left and left.named_children else None
    if name is None and language == "julia" and node.type == "function_definition":
        signature = next(
            (child for child in node.named_children if child.type == "signature"), None
        )
        if signature and signature.named_children:
            call = signature.named_children[0]
            name = call.named_children[0] if call.named_children else None
    if name is None and language in {
        "kotlin",
        "scala",
        "dart",
        "lua",
        "julia",
        "haskell",
        "ocaml",
        "fsharp",
        "fortran",
        "zig",
        "protobuf",
    }:
        name = next(
            (
                child
                for child in node.named_children
                if child.type
                in {
                    "identifier",
                    "simple_identifier",
                    "type_identifier",
                    "name",
                    "variable",
                    "long_identifier",
                }
            ),
            None,
        )
    return _text(name).strip()


def symbols_for(path: Path, text: str) -> list[Symbol]:
    language = language_for(path, text)
    if language in {"vue", "svelte"}:
        import re
        from dataclasses import replace

        result = []
        for match in re.finditer(r"<script\b([^>]*)>([\s\S]*?)</script\s*>", text, re.IGNORECASE):
            suffix = ".ts" if re.search(r'lang\s*=\s*["\']ts', match[1]) else ".js"
            line_offset = text.count("\n", 0, match.start(2))
            for symbol in symbols_for(Path("embedded" + suffix), match[2]):
                result.append(
                    replace(symbol, start=symbol.start + line_offset, end=symbol.end + line_offset)
                )
        return result
    if not parser_available(language):
        return []
    grammar = "java" if language == "groovy" else language
    root = get_parser(grammar).parse(text.encode("utf-8")).root_node
    result, seen = [], set()
    pending = [(root, ())]
    while pending:
        node, parents = pending.pop()
        nested = parents
        kind = DECLARATIONS.get(node.type)
        name = _name(node, language) if kind or node.type == "impl_item" else ""
        if language == "hcl" and node.type == "block":
            labels = [
                _text(child).strip('"')
                for child in node.named_children
                if child.type in {"identifier", "string_lit"}
            ]
            if labels:
                kind, name = "resource", ".".join(labels)
        if language in {"css", "scss"} and node.type == "rule_set":
            selector = next(
                (child for child in node.named_children if child.type == "selectors"), None
            )
            kind, name = "selector", _text(selector)
        if language == "r" and node.type == "binary_operator":
            value = node.child_by_field_name("rhs")
            if value and value.type == "function_definition":
                kind, name = "function", _text(node.child_by_field_name("lhs"))
        if language == "elixir" and node.type == "call":
            target = _text(node.child_by_field_name("target"))
            arguments = next(
                (child for child in node.named_children if child.type == "arguments"), None
            )
            if (
                arguments
                and arguments.named_children
                and target in {"defmodule", "def", "defp", "defmacro"}
            ):
                head = arguments.named_children[0]
                kind = "module" if target == "defmodule" else "function"
                name = (
                    _text(head.child_by_field_name("target"))
                    if head.type == "call"
                    else _text(head)
                )
        if language == "clojure" and node.type == "list_lit" and len(node.named_children) >= 2:
            if _text(node.named_children[0]) in {
                "defn",
                "defn-",
                "defmacro",
                "defrecord",
                "deftype",
            }:
                kind, name = "function", _text(node.named_children[1])
        if language == "zig" and node.type == "Decl":
            proto = next((child for child in node.named_children if child.type == "FnProto"), None)
            if proto:
                kind, name = "function", _text(proto.child_by_field_name("function"))
        # Arrow functions and function expressions retain the binding's public name.
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is not None and value.type in {
                "arrow_function",
                "function_expression",
                "function",
            }:
                kind, name = "function", _text(node.child_by_field_name("name"))
        if name and len(name) <= 300 and "\n" not in name:
            if language == "cpp":
                name = name.replace("::", ".")
            if language == "go" and node.type == "method_declaration":
                receiver = node.child_by_field_name("receiver")
                types = [child for child in receiver.named_children] if receiver else []
                if types:
                    target = types[0].child_by_field_name("type")
                    owner = _text(target).lstrip("*")
                    if owner:
                        parents = (*parents, owner)
            qualified = ".".join((*parents, name))
            nested = (*parents, name)
            outer = (
                node.parent if node.parent and node.parent.type == "decorated_definition" else node
            )
            start = outer.start_point.row + 1
            end = max(start, outer.end_point.row + bool(outer.end_point.column))
            if language == "dart" and node.type == "function_signature":
                sibling = node.next_named_sibling
                if sibling is None and node.parent and node.parent.type == "method_signature":
                    sibling = node.parent.next_named_sibling
                if sibling and sibling.type == "function_body":
                    end = sibling.end_point.row + bool(sibling.end_point.column)
            key = (qualified, start, end)
            if kind and key not in seen and not node.is_missing:
                body = node.child_by_field_name("body")
                header = (
                    node.text[: body.start_byte - node.start_byte]
                    if body
                    else node.text.split(b"\n", 1)[0]
                )
                result.append(
                    Symbol(
                        qualified,
                        kind,
                        start,
                        end,
                        " ".join(header.decode("utf-8", errors="replace").split())[:300],
                        language,
                    )
                )
                seen.add(key)
        pending.extend((child, nested) for child in reversed(node.named_children))
    return sorted(result, key=lambda item: (item.start, item.end, item.name))
