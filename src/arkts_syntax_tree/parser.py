from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


SOURCE_EXTENSIONS = (".ets", ".ts")
SKIP_DIRECTORIES = {
    ".git",
    ".hvigor",
    ".idea",
    ".preview",
    ".vscode",
    "build",
    "node_modules",
    "oh_modules",
}
CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "else", "do", "try"}


@dataclass
class SyntaxNode:
    type: str
    name: str
    start_line: int
    end_line: int | None = None
    decorators: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    signature: str = ""
    children: list["SyntaxNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line or self.start_line,
            "decorators": self.decorators,
            "modifiers": self.modifiers,
            "signature": self.signature,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True)
class ParsedFile:
    path: str
    language: str
    imports: list[dict[str, Any]]
    tree: SyntaxNode
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "imports": self.imports,
            "tree": self.tree.to_dict(),
            "metrics": self.metrics,
        }


def parse_repository(
    root: str | Path,
    *,
    extensions: Iterable[str] = SOURCE_EXTENSIONS,
) -> list[ParsedFile]:
    root_path = Path(root)
    source_paths = iter_source_files(root_path, extensions=extensions)
    parsed_files: list[ParsedFile] = []
    for source_path in source_paths:
        relative_path = source_path.relative_to(root_path).as_posix()
        source = source_path.read_text(encoding="utf-8", errors="replace")
        parsed_files.append(parse_source(source, path=relative_path))
    return parsed_files


def iter_source_files(
    root: str | Path,
    *,
    extensions: Iterable[str] = SOURCE_EXTENSIONS,
) -> list[Path]:
    root_path = Path(root)
    extension_set = {extension.lower() for extension in extensions}
    paths: list[Path] = []
    for path in root_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.lower() in extension_set:
            paths.append(path)
    return sorted(paths)


def parse_source(source: str, *, path: str) -> ParsedFile:
    masked = mask_non_code(source)
    original_lines = source.splitlines()
    masked_lines = masked.splitlines()
    imports = extract_imports(source)
    root = SyntaxNode(
        type="file",
        name=path,
        start_line=1,
        end_line=max(1, len(original_lines)),
    )
    stack: list[tuple[SyntaxNode, int]] = [(root, 0)]
    pending_decorators: list[str] = []
    brace_depth = 0

    for line_number, masked_line in enumerate(masked_lines, start=1):
        original_line = original_lines[line_number - 1] if line_number <= len(original_lines) else ""
        decorators, declaration_line = split_decorators(original_line)
        _, masked_declaration_line = split_decorators(masked_line)
        if decorators and not declaration_line.strip():
            pending_decorators.extend(decorators)
            brace_depth += brace_delta(masked_line)
            continue

        if decorators:
            pending_decorators.extend(decorators)

        node = detect_node(masked_declaration_line, original_line, line_number, pending_decorators)
        if node is not None:
            stack[-1][0].children.append(node)
            if "{" in masked_declaration_line:
                stack.append((node, brace_depth + 1))
            else:
                node.end_line = line_number
            pending_decorators = []

        brace_depth += brace_delta(masked_line)
        while len(stack) > 1 and brace_depth < stack[-1][1]:
            completed, _ = stack.pop()
            completed.end_line = line_number

    for node, _ in stack[1:]:
        node.end_line = len(original_lines)

    return ParsedFile(
        path=path,
        language=language_for_path(path),
        imports=imports,
        tree=root,
        metrics=build_metrics(root, imports),
    )


def write_syntax_tree_outputs(
    parsed_files: list[ParsedFile],
    *,
    output_path: str | Path,
    summary_path: str | Path | None = None,
    pretty: bool = False,
) -> dict[str, Any]:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fp:
        for parsed_file in parsed_files:
            fp.write(json.dumps(parsed_file.to_dict(), ensure_ascii=False, indent=2 if pretty else None))
            fp.write("\n")

    summary = build_repository_summary(parsed_files, output_path=output)
    if summary_path is not None:
        summary_output = Path(summary_path)
        summary_output.parent.mkdir(parents=True, exist_ok=True)
        summary_output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return summary


def build_repository_summary(parsed_files: list[ParsedFile], *, output_path: Path | None = None) -> dict[str, Any]:
    node_types: Counter[str] = Counter()
    total_imports = 0
    total_nodes = 0
    max_depth = 0
    for parsed_file in parsed_files:
        total_imports += len(parsed_file.imports)
        total_nodes += parsed_file.metrics["nodes"]
        max_depth = max(max_depth, parsed_file.metrics["max_depth"])
        node_types.update(parsed_file.metrics["node_types"])

    summary: dict[str, Any] = {
        "files": len(parsed_files),
        "imports": total_imports,
        "nodes": total_nodes,
        "max_depth": max_depth,
        "node_types": dict(sorted(node_types.items())),
    }
    if output_path is not None:
        summary["output"] = str(output_path)
    return summary


def extract_imports(source: str) -> list[dict[str, Any]]:
    imports: list[dict[str, Any]] = []
    for match in re.finditer(r"^\s*import\s+(?P<body>.+?)(?:;)?\s*$", source, flags=re.MULTILINE):
        body = match.group("body")
        source_match = re.search(r"\bfrom\s+['\"](?P<source>[^'\"]+)['\"]", body)
        side_effect_match = re.match(r"['\"](?P<source>[^'\"]+)['\"]", body.strip())
        imports.append(
            {
                "line": source[: match.start()].count("\n") + 1,
                "source": (source_match or side_effect_match).group("source") if (source_match or side_effect_match) else "",
                "clause": body[: source_match.start()].strip() if source_match else body.strip(),
            }
        )
    return imports


def detect_node(
    masked_line: str,
    original_line: str,
    line_number: int,
    decorators: list[str],
) -> SyntaxNode | None:
    stripped = masked_line.strip()
    if not stripped or stripped.startswith("import "):
        return None

    for pattern, node_type in [
        (r"\b(?P<mods>(?:export\s+|default\s+)*)struct\s+(?P<name>[A-Za-z_$][\w$]*)", "struct"),
        (r"\b(?P<mods>(?:export\s+|default\s+|abstract\s+)*)class\s+(?P<name>[A-Za-z_$][\w$]*)", "class"),
        (r"\b(?P<mods>(?:export\s+|default\s+)*)interface\s+(?P<name>[A-Za-z_$][\w$]*)", "interface"),
        (r"\b(?P<mods>(?:export\s+|default\s+)*)enum\s+(?P<name>[A-Za-z_$][\w$]*)", "enum"),
        (r"\b(?P<mods>(?:export\s+|default\s+|async\s+)*)function\s+(?P<name>[A-Za-z_$][\w$]*)", "function"),
    ]:
        match = re.search(pattern, stripped)
        if match:
            return SyntaxNode(
                type=node_type,
                name=match.group("name"),
                start_line=line_number,
                decorators=list(decorators),
                modifiers=_modifiers(match.groupdict().get("mods", "")),
                signature=original_line.strip(),
            )

    property_node = detect_property(stripped, original_line, line_number, decorators)
    if property_node is not None:
        return property_node

    ui_node = detect_ui_component(stripped, original_line, line_number)
    if ui_node is not None:
        return ui_node

    method_node = detect_method(stripped, original_line, line_number, decorators)
    if method_node is not None:
        return method_node

    callback_node = detect_callback(stripped, original_line, line_number)
    if callback_node is not None:
        return callback_node

    return None


def detect_property(
    stripped: str,
    original_line: str,
    line_number: int,
    decorators: list[str],
) -> SyntaxNode | None:
    if "{" in stripped or "(" in stripped:
        return None
    match = re.match(
        r"(?P<mods>(?:public\s+|private\s+|protected\s+|static\s+|readonly\s+)*)"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*(?::|=)",
        stripped,
    )
    if not match:
        return None
    return SyntaxNode(
        type="property",
        name=match.group("name"),
        start_line=line_number,
        end_line=line_number,
        decorators=list(decorators),
        modifiers=_modifiers(match.group("mods")),
        signature=original_line.strip(),
    )


def detect_method(
    stripped: str,
    original_line: str,
    line_number: int,
    decorators: list[str],
) -> SyntaxNode | None:
    if "{" not in stripped:
        return None
    match = re.match(
        r"(?P<mods>(?:public\s+|private\s+|protected\s+|static\s+|async\s+|override\s+)*)"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*\(",
        stripped,
    )
    if not match or match.group("name") in CONTROL_WORDS:
        return None
    return SyntaxNode(
        type="method",
        name=match.group("name"),
        start_line=line_number,
        decorators=list(decorators),
        modifiers=_modifiers(match.group("mods")),
        signature=original_line.strip(),
    )


def detect_ui_component(stripped: str, original_line: str, line_number: int) -> SyntaxNode | None:
    if "{" not in stripped:
        return None
    match = re.match(r"(?P<name>[A-Z][A-Za-z0-9_$]*)\s*\(", stripped)
    if not match:
        return None
    return SyntaxNode(
        type="ui_component",
        name=match.group("name"),
        start_line=line_number,
        signature=original_line.strip(),
    )


def detect_callback(stripped: str, original_line: str, line_number: int) -> SyntaxNode | None:
    if "=>" not in stripped or "{" not in stripped:
        return None
    return SyntaxNode(
        type="callback",
        name="anonymous",
        start_line=line_number,
        signature=original_line.strip(),
    )


def split_decorators(line: str) -> tuple[list[str], str]:
    rest = line.lstrip()
    decorators: list[str] = []
    while rest.startswith("@"):
        match = re.match(r"@(?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)(?:\([^)]*\))?", rest)
        if not match:
            break
        decorators.append(match.group(0))
        rest = rest[match.end() :].lstrip()
    return decorators, rest


def brace_delta(line: str) -> int:
    return line.count("{") - line.count("}")


def mask_non_code(source: str) -> str:
    result: list[str] = []
    index = 0
    state = "code"
    quote = ""
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if char == "/" and next_char == "/":
                result.extend([" ", " "])
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                result.extend([" ", " "])
                index += 2
                state = "block_comment"
                continue
            if char in {"'", '"', "`"}:
                result.append(" ")
                quote = char
                state = "string"
                index += 1
                continue
            result.append(char)
            index += 1
            continue

        if state == "line_comment":
            result.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "code"
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and next_char == "/":
                result.extend([" ", " "])
                index += 2
                state = "code"
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if state == "string":
            if char == "\\" and next_char:
                result.extend([" ", "\n" if next_char == "\n" else " "])
                index += 2
                continue
            result.append("\n" if char == "\n" else " ")
            if char == quote:
                state = "code"
            index += 1
            continue

    return "".join(result)


def build_metrics(root: SyntaxNode, imports: list[dict[str, Any]]) -> dict[str, Any]:
    node_types: Counter[str] = Counter()
    max_depth = 0

    def visit(node: SyntaxNode, depth: int) -> int:
        nonlocal max_depth
        count = 0 if node.type == "file" else 1
        if node.type != "file":
            node_types[node.type] += 1
            max_depth = max(max_depth, depth)
        for child in node.children:
            count += visit(child, depth + 1)
        return count

    return {
        "imports": len(imports),
        "nodes": visit(root, 0),
        "max_depth": max_depth,
        "node_types": dict(sorted(node_types.items())),
    }


def language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".ets":
        return "ArkTS"
    if suffix == ".ts":
        return "TypeScript"
    return suffix.lstrip(".")


def _modifiers(value: str) -> list[str]:
    return [part for part in value.split() if part]
