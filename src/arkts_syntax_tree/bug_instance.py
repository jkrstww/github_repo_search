from __future__ import annotations

import difflib
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import configparser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .parser import CONTROL_WORDS, mask_non_code


FUNCTION_NODE_TYPES = {"function", "method", "callback"}
CONTAINER_NODE_TYPES = {"class", "struct", "interface"}


@dataclass(frozen=True)
class FunctionInfo:
    path: str
    node_type: str
    name: str
    qualified_name: str
    owner_name: str | None
    owner_modifiers: tuple[str, ...]
    start_line: int
    end_line: int
    signature: str
    modifiers: tuple[str, ...]

    @property
    def identity(self) -> str:
        return f"{self.path}::{self.qualified_name}"

    @property
    def is_anonymous(self) -> bool:
        return self.name.startswith("anonymous@")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "node_type": self.node_type,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "owner_name": self.owner_name,
            "owner_modifiers": list(self.owner_modifiers),
            "start_line": self.start_line,
            "end_line": self.end_line,
            "signature": self.signature,
            "modifiers": list(self.modifiers),
        }


@dataclass(frozen=True)
class ReturnMutation:
    line: int
    original_line: str
    mutated_line: str
    original_expression: str
    mutated_expression: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "original_line": self.original_line.rstrip("\r\n"),
            "mutated_line": self.mutated_line.rstrip("\r\n"),
            "original_expression": self.original_expression,
            "mutated_expression": self.mutated_expression,
        }


@dataclass(frozen=True)
class ConsumerInfo:
    path: str
    function_name: str
    function_start_line: int | None
    call_line: int
    call_text: str
    consumption_type: str

    @property
    def identity(self) -> str:
        return f"{self.path}::{self.function_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "function_name": self.function_name,
            "function_start_line": self.function_start_line,
            "call_line": self.call_line,
            "call_text": self.call_text.strip(),
            "consumption_type": self.consumption_type,
        }


@dataclass(frozen=True)
class CandidateAnalysis:
    function: FunctionInfo
    out_degree: int
    callees: tuple[str, ...]
    consumers: tuple[ConsumerInfo, ...]
    mutation: ReturnMutation

    @property
    def downstream_function_count(self) -> int:
        return len({consumer.identity for consumer in self.consumers})

    def to_dict(self) -> dict[str, Any]:
        return {
            "function": self.function.to_dict(),
            "out_degree": self.out_degree,
            "callees": list(self.callees),
            "downstream_function_count": self.downstream_function_count,
            "downstream_consumers": [consumer.to_dict() for consumer in self.consumers],
            "mutation": self.mutation.to_dict(),
        }


@dataclass(frozen=True)
class ImportBinding:
    local_name: str
    imported_name: str
    source_path: str
    kind: str


def load_syntax_tree_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fp:
        for line in fp:
            stripped = line.strip()
            if stripped:
                records.append(json.loads(stripped))
    return records


def find_bug_candidates(
    repo_root: str | Path,
    syntax_tree_path: str | Path,
    *,
    min_out_degree: int = 3,
    min_downstream_consumers: int = 2,
) -> list[CandidateAnalysis]:
    repo = Path(repo_root)
    records = load_syntax_tree_jsonl(syntax_tree_path)
    known_paths = {record["path"] for record in records}
    functions = flatten_functions(records)
    functions_by_path = _group_functions_by_path(functions)
    imports_by_path = _build_import_index(records, known_paths)

    candidates: list[CandidateAnalysis] = []
    for function in functions:
        if function.is_anonymous:
            continue
        if function.node_type not in {"function", "method"}:
            continue

        source = _read_repo_file(repo, function.path)
        mutation = find_return_mutation(function, source)
        if mutation is None:
            continue

        callees = extract_callees(source, function)
        if len(callees) < min_out_degree:
            continue

        consumers = find_return_consumers(
            repo,
            function,
            records,
            imports_by_path,
            functions_by_path,
        )
        unique_consumers = _unique_consumers(consumers)
        if len(unique_consumers) < min_downstream_consumers:
            continue

        candidates.append(
            CandidateAnalysis(
                function=function,
                out_degree=len(callees),
                callees=tuple(sorted(callees)),
                consumers=tuple(unique_consumers),
                mutation=mutation,
            )
        )

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.downstream_function_count,
            -candidate.out_degree,
            candidate.function.path,
            candidate.function.start_line,
        ),
    )


def create_bug_instance(
    repo_root: str | Path,
    syntax_tree_path: str | Path,
    *,
    output_dir: str | Path,
    min_out_degree: int = 3,
    min_downstream_consumers: int = 2,
    instance_id: str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    syntax_tree = Path(syntax_tree_path).resolve()
    output = Path(output_dir).resolve()
    if _is_relative_to(output, repo):
        raise ValueError("output directory must be outside the source repository")
    candidates = find_bug_candidates(
        repo,
        syntax_tree,
        min_out_degree=min_out_degree,
        min_downstream_consumers=min_downstream_consumers,
    )
    if not candidates:
        raise ValueError(
            "no function matches out_degree >= "
            f"{min_out_degree} and downstream consumers >= {min_downstream_consumers}"
        )

    candidate = candidates[0]
    instance_name = instance_id or _default_instance_id(repo.name, candidate.function)
    instance_dir = output / instance_name
    if instance_dir.exists():
        raise FileExistsError(f"instance already exists: {instance_dir}")

    instance_dir.mkdir(parents=True)
    shutil.copyfile(syntax_tree, instance_dir / "syntax_tree.jsonl")

    relative_target = Path(candidate.function.path)
    original_file = repo / relative_target
    original_lines = _read_text_preserve_newlines(original_file).splitlines(keepends=True)
    mutated_lines = list(original_lines)
    mutation_index = candidate.mutation.line - 1
    mutated_lines[mutation_index] = candidate.mutation.mutated_line

    bug_patch = _make_patch(
        original_lines,
        mutated_lines,
        relative_target.as_posix(),
    )
    fix_patch = _make_patch(
        mutated_lines,
        original_lines,
        relative_target.as_posix(),
    )
    bug_patch_path = instance_dir / "bug.patch"
    fix_patch_path = instance_dir / "fix.patch"
    bug_patch_path.write_text(bug_patch, encoding="utf-8")
    fix_patch_path.write_text(fix_patch, encoding="utf-8")

    metadata = {
        "schema_version": 1,
        "instance_id": instance_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_repo": {
            "github_url": _github_url(repo),
            "commit": _git_head(repo),
        },
        "target": candidate.to_dict(),
        "description": render_description(candidate),
        "file_hashes": {
            "original_sha256": _sha256_text("".join(original_lines)),
            "mutated_sha256": _sha256_text("".join(mutated_lines)),
        },
    }
    (instance_dir / "instance.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def flatten_functions(records: list[dict[str, Any]]) -> list[FunctionInfo]:
    functions: list[FunctionInfo] = []

    def visit(
        path: str,
        node: dict[str, Any],
        name_stack: list[str],
        owner_name: str | None,
        owner_modifiers: tuple[str, ...],
    ) -> None:
        node_type = node.get("type", "")
        next_stack = list(name_stack)
        next_owner = owner_name
        next_owner_modifiers = owner_modifiers

        if node_type in CONTAINER_NODE_TYPES:
            next_owner = node.get("name") or None
            next_owner_modifiers = tuple(node.get("modifiers", []))
            next_stack.append(node.get("name", "<anonymous>"))

        if node_type in FUNCTION_NODE_TYPES:
            raw_name = node.get("name", "")
            display_name = f"anonymous@{node.get('start_line')}" if raw_name == "anonymous" else raw_name
            qualified_name = ".".join(next_stack + [display_name]) if next_stack else display_name
            functions.append(
                FunctionInfo(
                    path=path,
                    node_type=node_type,
                    name=display_name,
                    qualified_name=qualified_name,
                    owner_name=next_owner,
                    owner_modifiers=next_owner_modifiers,
                    start_line=int(node.get("start_line", 1)),
                    end_line=int(node.get("end_line", node.get("start_line", 1))),
                    signature=node.get("signature", ""),
                    modifiers=tuple(node.get("modifiers", [])),
                )
            )
            next_stack = next_stack + [display_name]

        for child in node.get("children", []):
            visit(path, child, next_stack, next_owner, next_owner_modifiers)

    for record in records:
        visit(record["path"], record["tree"], [], None, ())
    return functions


def extract_callees(source: str, function: FunctionInfo) -> set[str]:
    lines = source.splitlines(keepends=True)
    snippet = "".join(lines[function.start_line - 1 : function.end_line])
    masked = mask_non_code(snippet)
    callees: set[str] = set()
    for match in re.finditer(r"(?<![\w$])(?P<call>[A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*)*)\s*\(", masked):
        call = match.group("call").replace("?.", ".")
        name = call.split(".")[-1]
        if name in CONTROL_WORDS:
            continue
        if name == function.name:
            continue
        callees.add(call)
    return callees


def find_return_mutation(function: FunctionInfo, source: str) -> ReturnMutation | None:
    original_lines = source.splitlines(keepends=True)
    masked_lines = mask_non_code(source).splitlines()
    for line_number in range(function.end_line, function.start_line - 1, -1):
        if line_number > len(masked_lines):
            continue
        masked = masked_lines[line_number - 1].strip()
        if not re.match(r"return\s+.+;?\s*$", masked):
            continue

        original_line = original_lines[line_number - 1]
        line_body, line_ending = _split_line_ending(original_line)
        match = re.match(r"(?P<indent>\s*)return\s+(?P<expr>.*?)(?P<semi>;)?\s*$", line_body)
        if not match:
            continue

        expression = match.group("expr").strip()
        mutated_expression = infer_mutated_expression(function, expression)
        if expression == mutated_expression:
            continue

        semi = match.group("semi") or ""
        mutated_line = f"{match.group('indent')}return {mutated_expression}{semi}{line_ending}"
        return ReturnMutation(
            line=line_number,
            original_line=original_line,
            mutated_line=mutated_line,
            original_expression=expression,
            mutated_expression=mutated_expression,
        )
    return None


def infer_mutated_expression(function: FunctionInfo, expression: str) -> str:
    return_type = _return_type(function.signature).lower()
    normalized = expression.strip().rstrip(";")
    if "boolean" in return_type or _looks_boolean_expression(normalized):
        return "true" if normalized == "false" else "false"
    if "string" in return_type or _looks_string_expression(normalized):
        return '""'
    if "number" in return_type or _looks_number_expression(normalized):
        return "0"
    if "[]" in return_type or "array<" in return_type:
        return "[]"
    return "null"


def find_return_consumers(
    repo: Path,
    candidate: FunctionInfo,
    records: list[dict[str, Any]],
    imports_by_path: dict[str, list[ImportBinding]],
    functions_by_path: dict[str, list[FunctionInfo]],
) -> list[ConsumerInfo]:
    consumers: list[ConsumerInfo] = []
    for record in records:
        path = record["path"]
        if path == candidate.path:
            continue

        patterns = _call_patterns(candidate, imports_by_path.get(path, []))
        if not patterns:
            continue

        source = _read_repo_file(repo, path)
        original_lines = source.splitlines()
        masked_lines = mask_non_code(source).splitlines()
        file_functions = functions_by_path.get(path, [])
        for line_index, masked_line in enumerate(masked_lines):
            for pattern in patterns:
                for match in pattern.finditer(masked_line):
                    original_line = original_lines[line_index] if line_index < len(original_lines) else ""
                    consumer_function = _find_containing_function(file_functions, line_index + 1)
                    consumption_type = _classify_consumption(
                        original_line,
                        match.start(),
                        line_index + 1,
                        consumer_function,
                        original_lines,
                    )
                    if consumption_type is None:
                        continue
                    consumers.append(
                        ConsumerInfo(
                            path=path,
                            function_name=consumer_function.qualified_name if consumer_function else "<top-level>",
                            function_start_line=consumer_function.start_line if consumer_function else None,
                            call_line=line_index + 1,
                            call_text=original_line,
                            consumption_type=consumption_type,
                        )
                    )
    return consumers


def render_description(candidate: CandidateAnalysis) -> str:
    functions = "、".join(consumer.function_name for consumer in candidate.consumers)
    return f"以下函数{functions}调用失败，找出错误"


def _build_import_index(
    records: list[dict[str, Any]],
    known_paths: set[str],
) -> dict[str, list[ImportBinding]]:
    imports_by_path: dict[str, list[ImportBinding]] = {}
    for record in records:
        path = record["path"]
        bindings: list[ImportBinding] = []
        for import_record in record.get("imports", []):
            resolved = _resolve_import_source(path, import_record.get("source", ""), known_paths)
            if resolved is None:
                continue
            bindings.extend(_parse_import_bindings(import_record.get("clause", ""), resolved))
        imports_by_path[path] = bindings
    return imports_by_path


def _parse_import_bindings(clause: str, source_path: str) -> list[ImportBinding]:
    value = clause.strip()
    if not value:
        return []
    if value.startswith("type "):
        value = value[5:].strip()

    bindings: list[ImportBinding] = []
    named_match = re.search(r"\{(?P<body>[^}]*)\}", value)
    if named_match:
        for part in named_match.group("body").split(","):
            item = part.strip()
            if not item:
                continue
            alias_match = re.match(
                r"(?P<imported>[A-Za-z_$][\w$]*)\s+as\s+(?P<local>[A-Za-z_$][\w$]*)$",
                item,
            )
            if alias_match:
                imported = alias_match.group("imported")
                local = alias_match.group("local")
            else:
                imported = item
                local = item
            bindings.append(ImportBinding(local, imported, source_path, "named"))

    default_part = value.split("{", 1)[0].strip().rstrip(",").strip()
    if default_part.startswith("* as "):
        local = default_part[5:].strip()
        bindings.append(ImportBinding(local, "*", source_path, "namespace"))
    elif default_part and re.match(r"^[A-Za-z_$][\w$]*$", default_part):
        bindings.append(ImportBinding(default_part, "default", source_path, "default"))
    return bindings


def _resolve_import_source(importing_path: str, source: str, known_paths: set[str]) -> str | None:
    if not source.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importing_path), source))
    candidates = [
        base,
        f"{base}.ets",
        f"{base}.ts",
        posixpath.join(base, "index.ets"),
        posixpath.join(base, "index.ts"),
    ]
    for candidate in candidates:
        if candidate in known_paths:
            return candidate
    return None


def _call_patterns(candidate: FunctionInfo, bindings: list[ImportBinding]) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for binding in bindings:
        if binding.source_path != candidate.path:
            continue
        if candidate.owner_name:
            is_named_owner = binding.kind == "named" and binding.imported_name == candidate.owner_name
            is_default_owner = (
                binding.kind == "default"
                and binding.imported_name == "default"
                and "default" in candidate.owner_modifiers
            )
            if is_named_owner or is_default_owner:
                patterns.append(
                    re.compile(
                        rf"(?<![\w$]){re.escape(binding.local_name)}\s*\.\s*{re.escape(candidate.name)}\s*\("
                    )
                )
            elif binding.kind == "namespace":
                patterns.append(
                    re.compile(
                        rf"(?<![\w$]){re.escape(binding.local_name)}\s*\.\s*"
                        rf"{re.escape(candidate.owner_name)}\s*\.\s*{re.escape(candidate.name)}\s*\("
                    )
                )
        else:
            if binding.kind == "named" and binding.imported_name == candidate.name:
                patterns.append(re.compile(rf"(?<![\w$.]){re.escape(binding.local_name)}\s*\("))
            elif binding.kind == "default" and "default" in candidate.modifiers:
                patterns.append(re.compile(rf"(?<![\w$.]){re.escape(binding.local_name)}\s*\("))
            elif binding.kind == "namespace":
                patterns.append(
                    re.compile(
                        rf"(?<![\w$]){re.escape(binding.local_name)}\s*\.\s*{re.escape(candidate.name)}\s*\("
                    )
                )
    return patterns


def _classify_consumption(
    original_line: str,
    call_start: int,
    call_line: int,
    consumer_function: FunctionInfo | None,
    original_lines: list[str],
) -> str | None:
    prefix = original_line[:call_start]
    stripped_prefix = prefix.strip()
    assignment = re.search(
        r"\b(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)(?:\s*:\s*[^=]+)?\s*=\s*(?:await\s+)?$",
        prefix,
    )
    if assignment:
        variable = assignment.group("var")
        if consumer_function is None or _variable_used_after(
            variable,
            consumer_function,
            original_lines,
            call_line,
        ):
            return "assignment_used"
        return None
    if re.search(r"\b(?:if|while|for)\s*\([^)]*(?:await\s+)?$", prefix):
        return "condition"
    if stripped_prefix in {"return", "return await"}:
        return "return"
    if stripped_prefix.endswith(("(", ",")):
        return "argument"
    if ".then" in original_line[call_start:]:
        return "promise_chain"
    return None


def _variable_used_after(
    variable: str,
    consumer_function: FunctionInfo,
    original_lines: list[str],
    call_line: int,
) -> bool:
    pattern = re.compile(rf"\b{re.escape(variable)}\b")
    for line_index in range(call_line, consumer_function.end_line):
        line = original_lines[line_index] if line_index < len(original_lines) else ""
        if pattern.search(line):
            return True
    return False


def _find_containing_function(functions: list[FunctionInfo], line_number: int) -> FunctionInfo | None:
    matches = [
        function
        for function in functions
        if function.start_line <= line_number <= function.end_line
    ]
    if not matches:
        return None
    return min(matches, key=lambda function: (function.end_line - function.start_line, -function.start_line))


def _group_functions_by_path(functions: list[FunctionInfo]) -> dict[str, list[FunctionInfo]]:
    grouped: dict[str, list[FunctionInfo]] = {}
    for function in functions:
        grouped.setdefault(function.path, []).append(function)
    return grouped


def _unique_consumers(consumers: list[ConsumerInfo]) -> list[ConsumerInfo]:
    unique: dict[str, ConsumerInfo] = {}
    for consumer in consumers:
        unique.setdefault(consumer.identity, consumer)
    return list(unique.values())


def _return_type(signature: str) -> str:
    match = re.search(r"\)\s*:\s*(?P<type>[^={]+)", signature)
    return match.group("type").strip() if match else ""


def _looks_boolean_expression(expression: str) -> bool:
    return (
        expression in {"true", "false"}
        or expression.startswith("!!")
        or ".every(" in expression
        or ".some(" in expression
        or any(operator in expression for operator in ("===", "!==", "==", "!=", ">=", "<=", ">", "<"))
        or expression.lower().startswith(("is", "has", "can", "should"))
    )


def _looks_string_expression(expression: str) -> bool:
    return (
        expression.startswith(("'", '"', "`"))
        or expression.endswith((".toString()", ".message"))
        or "Path" in expression
        or "uri" in expression.lower()
    )


def _looks_number_expression(expression: str) -> bool:
    return bool(re.match(r"^-?\d+(?:\.\d+)?$", expression)) or expression.endswith(".length")


def _read_repo_file(repo: Path, path: str) -> str:
    return _read_text_preserve_newlines(repo / path)


def _read_text_preserve_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fp:
        return fp.read()


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _make_patch(before: list[str], after: list[str], relative_path: str) -> str:
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            n=0,
        )
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _default_instance_id(repo_name: str, function: FunctionInfo) -> str:
    target = re.sub(r"[^A-Za-z0-9_.-]+", "_", function.qualified_name).strip("_")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{repo_name}_{target}_{timestamp}"


def _git_head(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _github_url(repo: Path) -> str | None:
    remote = ""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        remote = result.stdout.strip()

    if not remote:
        config_path = repo / ".git" / "config"
        parser = configparser.ConfigParser()
        try:
            parser.read(config_path, encoding="utf-8")
            remote = parser.get('remote "origin"', "url", fallback="").strip()
        except (OSError, configparser.Error):
            return None

    match = re.match(
        r"^(?:https?://github\.com/|ssh://git@github\.com/|git@github\.com:)(?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?/?$",
        remote,
    )
    if not match:
        return None
    return f"https://github.com/{match.group('repo')}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
