"""Import/include hints extracted without executing project configuration."""

import ast
import re

from codaro.languages import get_parser, language_for, parser_available


def imports_for(path, text):
    language = language_for(path)
    imports = []
    if language == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, RecursionError):
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend({"target": name.name, "line": node.lineno} for name in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(
                    {"target": "." * node.level + (node.module or ""), "line": node.lineno}
                )
    else:
        patterns = {
            "javascript": r"""(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(?\s*)["']([^"']+)["']""",
            "typescript": r"""(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(?\s*)["']([^"']+)["']""",
            "tsx": r"""(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(?\s*)["']([^"']+)["']""",
            "c": r'^\s*#\s*include\s*["<]([^">]+)[">]',
            "cpp": r'^\s*#\s*include\s*["<]([^">]+)[">]',
            "java": r"\bimport\s+(?:static\s+)?([\w.]+)",
            "kotlin": r"\bimport\s+([\w.]+)",
            "csharp": r"\busing\s+([\w.]+)\s*;",
            "rust": r"\b(?:use|mod)\s+([\w:]+)",
            "go": r'"([\w./-]+)"',
            "ruby": r"""\brequire(?:_relative)?\s*["']([^"']+)["']""",
            "php": r"\buse\s+([\w\\]+)",
        }
        pattern = patterns.get(language)
        if pattern and parser_available(language):
            tree = get_parser(language).parse(text.encode("utf-8"))
            pending = [tree.root_node]
            import_nodes = {
                "import_statement",
                "import_declaration",
                "import_spec",
                "preproc_include",
                "using_directive",
                "use_declaration",
                "mod_item",
                "require",
                "use_statement",
            }
            while pending:
                node = pending.pop()
                if node.type in import_nodes:
                    code = node.text.decode("utf-8", errors="replace")
                    for match in re.finditer(pattern, code, re.MULTILINE):
                        imports.append(
                            {
                                "target": match[1],
                                "line": node.start_point.row
                                + 1
                                + code.count("\n", 0, match.start()),
                            }
                        )
                else:
                    pending.extend(reversed(node.named_children))
    # These are static hints, not a complete build-system or runtime resolution graph.
    return [
        {**item, "language": language, "source": "syntactic_import_hint"} for item in imports[:100]
    ]
