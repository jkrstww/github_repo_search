from __future__ import annotations

import configparser
import difflib
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .bug_instance import load_syntax_tree_jsonl
from .parser import ParsedFile, mask_non_code, parse_repository


@dataclass(frozen=True)
class AbstractNodeInfo:
    path: str
    node_type: str
    name: str
    start_line: int
    end_line: int
    signature: str
    modifiers: tuple[str, ...]

    @property
    def identity(self) -> str:
        return f"{self.path}::{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "node_type": self.node_type,
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "signature": self.signature,
            "modifiers": list(self.modifiers),
        }


@dataclass(frozen=True)
class ImplementationFileInfo:
    path: str
    relation: str
    local_name: str
    declarations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relation": self.relation,
            "local_name": self.local_name,
            "declarations": list(self.declarations),
        }


@dataclass(frozen=True)
class FeatureCandidate:
    abstract_node: AbstractNodeInfo
    implementation_files: tuple[ImplementationFileInfo, ...]

    @property
    def implementation_file_count(self) -> int:
        return len(self.implementation_files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "abstract_node": self.abstract_node.to_dict(),
            "implementation_file_count": self.implementation_file_count,
            "implementation_files": [item.to_dict() for item in self.implementation_files],
        }


def find_feature_candidates(
    repo_root: str | Path,
    syntax_tree_path: str | Path | None = None,
    *,
    min_implementation_files: int = 2,
    target_name: str | None = None,
    include_structural_usage: bool = False,
) -> list[FeatureCandidate]:
    repo = Path(repo_root)
    if not repo.is_dir():
        raise NotADirectoryError(f"repository does not exist: {repo}")
    if min_implementation_files < 1:
        raise ValueError("min_implementation_files must be at least 1")
    records = _load_records(repo, syntax_tree_path)
    known_paths = {record["path"] for record in records}
    abstract_nodes = _flatten_abstract_nodes(records)
    declarations_by_path = _declarations_by_path(records)

    candidates: list[FeatureCandidate] = []
    for abstract_node in abstract_nodes:
        implementations = _find_implementation_files(
            repo,
            records,
            known_paths,
            declarations_by_path,
            abstract_node,
            include_structural_usage=include_structural_usage,
        )
        if len(implementations) >= min_implementation_files:
            candidates.append(
                FeatureCandidate(
                    abstract_node=abstract_node,
                    implementation_files=tuple(implementations),
                )
            )

    if target_name is not None:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.abstract_node.name == target_name
            or candidate.abstract_node.path == target_name
        ]

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.implementation_file_count,
            candidate.abstract_node.path,
            candidate.abstract_node.start_line,
        ),
    )


def create_feature_instance(
    repo_root: str | Path,
    *,
    output_dir: str | Path,
    syntax_tree_path: str | Path | None = None,
    min_implementation_files: int = 2,
    target_name: str | None = None,
    include_structural_usage: bool = False,
    instance_id: str | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    output = Path(output_dir).resolve()
    if not repo.is_dir():
        raise NotADirectoryError(f"repository does not exist: {repo}")
    if _is_relative_to(output, repo):
        raise ValueError("output directory must be outside the source repository")

    candidates = find_feature_candidates(
        repo,
        syntax_tree_path,
        min_implementation_files=min_implementation_files,
        target_name=target_name,
        include_structural_usage=include_structural_usage,
    )
    if not candidates:
        raise ValueError(
            "no abstract interface/base class has at least "
            f"{min_implementation_files} implementation files"
        )

    candidate = candidates[0]
    masked_implementation = _select_mask_target(candidate)
    instance_name = instance_id or _default_instance_id(repo.name)
    _validate_instance_id(instance_name)
    instance_dir = output / instance_name
    if instance_dir.exists():
        raise FileExistsError(f"instance already exists: {instance_dir}")

    instance_dir.mkdir(parents=True)
    records = _load_records(repo, syntax_tree_path)
    if syntax_tree_path is not None:
        shutil.copyfile(Path(syntax_tree_path).resolve(), instance_dir / "syntax_tree.jsonl")
    else:
        _write_records_jsonl(records, instance_dir / "syntax_tree.jsonl")

    target_path = Path(masked_implementation.path)
    original_source = _read_text_preserve_newlines(repo / target_path)
    masked_source = _render_masked_source(candidate, masked_implementation, original_source)
    mask_patch = _make_patch(original_source, masked_source, masked_implementation.path)
    gold_patch = _make_patch(masked_source, original_source, masked_implementation.path)
    (instance_dir / "mask.patch").write_text(mask_patch, encoding="utf-8")
    (instance_dir / "gold.patch").write_text(gold_patch, encoding="utf-8")

    reference_files = [
        item.path
        for item in candidate.implementation_files
        if item.path != masked_implementation.path
    ]
    metadata = {
        "schema_version": 2,
        "task_type": "feature_implementation",
        "instance_id": instance_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_repo": {
            "name": repo.name,
            "github_url": _github_url(repo),
            "commit": _git_head(repo),
        },
        "target": candidate.to_dict(),
        "mask": {
            **masked_implementation.to_dict(),
            "kind": "full_file",
            "original_sha256": _sha256_text(original_source),
            "masked_sha256": _sha256_text(masked_source),
        },
        "description": render_feature_description(candidate, masked_implementation),
        "reference_implementation_files": reference_files,
        "patches": {
            "mask": "mask.patch",
            "gold": "gold.patch",
        },
        "gold_label": {
            "type": "patch",
            "path": "gold.patch",
            "applies_to": "working_repo",
        },
        "affected_modules": [
            candidate.abstract_node.path,
            masked_implementation.path,
            *reference_files,
        ],
        "acceptance": {
            "verification": "static_structure",
            "checks": [
                {
                    "type": "abstract_node_exists",
                    "path": candidate.abstract_node.path,
                    "name": candidate.abstract_node.name,
                },
                {
                    "type": "masked_file_changed",
                    "path": masked_implementation.path,
                    "masked_sha256": _sha256_text(masked_source),
                },
                {
                    "type": "implementation_relation_restored",
                    "path": masked_implementation.path,
                    "relation": masked_implementation.relation,
                },
                {
                    "type": "expected_declarations_restored",
                    "path": masked_implementation.path,
                    "declarations": list(masked_implementation.declarations),
                },
            ],
        },
    }
    (instance_dir / "instance.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


def verify_feature_instance(
    instance_dir: str | Path,
    repo_root: str | Path,
) -> dict[str, Any]:
    instance = Path(instance_dir).resolve()
    metadata = json.loads((instance / "instance.json").read_text(encoding="utf-8"))
    repo = Path(repo_root).resolve()
    if not repo.is_dir():
        raise NotADirectoryError(f"repository does not exist: {repo}")
    target = metadata["target"]
    abstract = target["abstract_node"]
    mask = metadata["mask"]
    masked_path = mask["path"]
    masked_file = repo / masked_path

    records = _load_records(repo, None)
    abstract_exists = any(
        node.path == abstract["path"]
        and node.node_type == abstract["node_type"]
        and node.name == abstract["name"]
        for node in _flatten_abstract_nodes(records)
    )
    candidates = find_feature_candidates(repo, min_implementation_files=1)
    matched = next(
        (
            candidate
            for candidate in candidates
            if candidate.abstract_node.path == abstract["path"]
            and candidate.abstract_node.name == abstract["name"]
        ),
        None,
    )
    current_paths = {item.path for item in matched.implementation_files} if matched else set()
    declarations_by_path = _declarations_by_path(records)
    current_declarations = set(declarations_by_path.get(masked_path, ()))
    expected_declarations = set(mask.get("declarations", []))
    current_source = (
        _read_text_preserve_newlines(masked_file) if masked_file.is_file() else None
    )
    checks = {
        "abstract_node_exists": abstract_exists,
        "masked_file_exists": current_source is not None,
        "masked_file_changed": (
            current_source is not None
            and _sha256_text(current_source) != mask["masked_sha256"]
        ),
            "implementation_relation_restored": masked_path in current_paths,
        "expected_declarations_restored": expected_declarations.issubset(current_declarations),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "masked_file": masked_path,
        "current_declarations": sorted(current_declarations),
        "matches_gold": (
            current_source is not None
            and _sha256_text(current_source) == mask["original_sha256"]
        ),
    }


def render_feature_description(
    candidate: FeatureCandidate,
    masked_implementation: ImplementationFileInfo,
) -> str:
    node = candidate.abstract_node
    masked_label = (
        "实现/派生文件"
        if masked_implementation.relation == "explicit_inheritance"
        else "类型使用文件"
    )
    reference_label = (
        "其他实现/派生文件"
        if masked_implementation.relation == "explicit_inheritance"
        else "其他类型使用文件"
    )
    references = "、".join(
        item.path
        for item in candidate.implementation_files
        if item.path != masked_implementation.path
    )
    declarations = "、".join(masked_implementation.declarations) or "原有声明"
    return (
        f"仓库中的 {node.node_type} `{node.name}` 定义于 {node.path}:{node.start_line}，"
        f"{masked_label} `{masked_implementation.path}` 已被 mask，原文件包含声明 {declarations}。"
        f"请参考抽象定义和{reference_label}（{references}），在原路径补全该文件。"
    )


def _load_records(repo: Path, syntax_tree_path: str | Path | None) -> list[dict[str, Any]]:
    if syntax_tree_path is not None:
        return load_syntax_tree_jsonl(syntax_tree_path)
    return [_parsed_file_to_dict(parsed) for parsed in parse_repository(repo)]


def _parsed_file_to_dict(parsed: ParsedFile) -> dict[str, Any]:
    return parsed.to_dict()


def _flatten_abstract_nodes(records: list[dict[str, Any]]) -> list[AbstractNodeInfo]:
    result: list[AbstractNodeInfo] = []

    def visit(path: str, node: dict[str, Any]) -> None:
        node_type = node.get("type")
        modifiers = tuple(node.get("modifiers", []))
        if node_type in {"interface", "class"}:
            result.append(
                AbstractNodeInfo(
                    path=path,
                    node_type=node_type,
                    name=node.get("name", ""),
                    start_line=int(node.get("start_line", 1)),
                    end_line=int(node.get("end_line", node.get("start_line", 1))),
                    signature=node.get("signature", ""),
                    modifiers=modifiers,
                )
            )
        for child in node.get("children", []):
            visit(path, child)

    for record in records:
        visit(record["path"], record["tree"])
    return result


def _declarations_by_path(records: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for record in records:
        names: list[str] = []

        def visit(node: dict[str, Any]) -> None:
            if node.get("type") in {"class", "struct"}:
                names.append(node.get("name", ""))
            for child in node.get("children", []):
                visit(child)

        visit(record["tree"])
        result[record["path"]] = tuple(name for name in names if name)
    return result


def _find_implementation_files(
    repo: Path,
    records: list[dict[str, Any]],
    known_paths: set[str],
    declarations_by_path: dict[str, tuple[str, ...]],
    abstract_node: AbstractNodeInfo,
    *,
    include_structural_usage: bool,
) -> list[ImplementationFileInfo]:
    found: dict[str, ImplementationFileInfo] = {}
    for record in records:
        path = record["path"]
        if path == abstract_node.path:
            continue
        source = (repo / path).read_text(encoding="utf-8", errors="replace")
        masked_source = mask_non_code(source)
        local_name = _imported_local_name(record, abstract_node, known_paths)
        explicit = local_name and re.search(
            rf"\b(?:class|struct)\s+[A-Za-z_$][\w$]*[^{{\n]*"
            rf"\b(?:implements|extends)\s+[^{{\n]*\b{re.escape(local_name)}\b",
            masked_source,
        )
        if explicit:
            found[path] = ImplementationFileInfo(
                path=path,
                relation="explicit_inheritance",
                local_name=local_name,
                declarations=declarations_by_path.get(path, ()),
            )
            continue

        if not include_structural_usage or abstract_node.node_type != "interface":
            continue
        if local_name and len(re.findall(rf"\b{re.escape(local_name)}\b", masked_source)) >= 2:
            found[path] = ImplementationFileInfo(
                path=path,
                relation="structural_usage",
                local_name=local_name,
                declarations=declarations_by_path.get(path, ()),
            )
    return [found[path] for path in sorted(found)]


def _imported_local_name(
    record: dict[str, Any],
    abstract_node: AbstractNodeInfo,
    known_paths: set[str],
) -> str | None:
    for imported in record.get("imports", []):
        source_path = _resolve_import_source(record["path"], imported.get("source", ""), known_paths)
        if source_path != abstract_node.path:
            continue
        clause = imported.get("clause", "")
        if "{" in clause and "}" in clause:
            body = clause[clause.find("{") + 1 : clause.rfind("}")]
            for part in body.split(","):
                pieces = re.split(r"\s+as\s+", part.strip())
                if pieces and pieces[0].strip() == abstract_node.name:
                    return pieces[-1].strip()
        default_name = clause.split(",", 1)[0].strip()
        if "default" in abstract_node.modifiers and re.fullmatch(r"[A-Za-z_$][\w$]*", default_name):
            return default_name
    return None


def _resolve_import_source(importing_path: str, source: str, known_paths: set[str]) -> str | None:
    if not source.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importing_path), source))
    candidates = [base, f"{base}.ets", f"{base}.ts", f"{base}/index.ets", f"{base}/index.ts"]
    return next((candidate for candidate in candidates if candidate in known_paths), None)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _select_mask_target(candidate: FeatureCandidate) -> ImplementationFileInfo:
    return sorted(
        candidate.implementation_files,
        key=lambda item: (
            not bool(item.declarations),
            item.relation != "explicit_inheritance",
            item.path,
        ),
    )[0]


def _render_masked_source(
    candidate: FeatureCandidate,
    implementation: ImplementationFileInfo,
    original_source: str,
) -> str:
    newline = "\r\n" if "\r\n" in original_source else "\n"
    declarations = ", ".join(implementation.declarations) or "unknown"
    masked_source = newline.join(
        [
            "// CODE BENCHMARK MASK",
            f"// Restore the implementation for: {candidate.abstract_node.name}",
            f"// Expected declarations: {declarations}",
            "",
        ]
    )
    return masked_source if masked_source.endswith(newline) else masked_source + newline


def _read_text_preserve_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as file:
        return file.read()


def _write_records_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False))
            file.write("\n")


def _make_patch(before: str, after: str, relative_path: str) -> str:
    lines: list[str] = []
    for line in difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
        n=3,
    ):
        if line.endswith(("\n", "\r")):
            lines.append(line)
        else:
            lines.append(f"{line}\n")
            lines.append("\\ No newline at end of file\n")
    patch_text = "".join(lines)
    return patch_text if patch_text.endswith("\n") else patch_text + "\n"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _default_instance_id(repo_name: str) -> str:
    safe_repo_name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name).strip(".-") or "repo"
    return f"{safe_repo_name}-{uuid.uuid4()}"


def _validate_instance_id(instance_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", instance_id):
        raise ValueError(
            "instance_id must contain only letters, digits, dots, underscores, and hyphens"
        )


def _git_head(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
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
        r"^(?:https?://github\.com/|ssh://git@github\.com/|git@github\.com:)"
        r"(?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?/?$",
        remote,
    )
    if not match:
        return None
    return f"https://github.com/{match.group('repo')}"
