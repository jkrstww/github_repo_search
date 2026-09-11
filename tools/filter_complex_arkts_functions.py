"""按上游调用复杂度筛选 ArkTS 函数。

用途：
    为函数恢复任务选择复杂度较高的 ArkTS 函数。它不检查函数是否可变异，
    只分析调用图：找出所有直接或间接调用目标函数的上游函数，并累计这些
    上游函数各自直接调用的函数数量。

使用方式：
    作为命令行工具列出候选函数：
        python tools/filter_complex_arkts_functions.py <仓库目录> <语法树.jsonl>
    作为 Python 模块复用：
        find_complex_function_candidates(repo, syntax_tree)

输入：
    - 已 checkout 的仓库目录；
    - 由 ArkTS 解析器生成的语法树 JSONL 文件；
    - 可选阈值 ``--min-upstream-direct-call-count``，默认 5。

输出：
    - 命令行：候选函数及其 ``upstream_direct_call_count`` 的 JSON 数组；
    - Python API：候选列表、调用图和函数目录。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from arkts_syntax_tree.parser import (
    FunctionInfo,
    build_import_index,
    extract_function_callees,
    flatten_functions,
    load_syntax_tree_jsonl,
)


@dataclass(frozen=True)
class ComplexFunctionCandidate:
    """A function and the call-graph metrics used to select it."""

    function: FunctionInfo
    upstream_function_count: int
    upstream_direct_call_count: int

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable candidate metadata."""
        return {
            "function": self.function.to_dict(),
            "upstream_function_count": self.upstream_function_count,
            "upstream_direct_call_count": self.upstream_direct_call_count,
        }


def _function_catalog(syntax_tree: Path) -> dict[str, FunctionInfo]:
    """Load all parsed functions keyed by their stable identity."""
    return {
        function.identity: function
        for function in flatten_functions(load_syntax_tree_jsonl(syntax_tree))
    }


def _resolve_callee(
    caller: FunctionInfo,
    callee: str,
    functions_by_path: dict[str, list[FunctionInfo]],
    imports_by_path: dict[str, list[Any]],
    by_qualified: dict[str, set[str]],
    by_name: dict[str, set[str]],
) -> set[str]:
    """Resolve a call without conflating identical method names in other files."""
    leaf = callee.rsplit(".", 1)[-1]
    same_path = functions_by_path.get(caller.path, [])
    if callee.startswith("this."):
        owned = {item.identity for item in same_path if item.name == leaf and item.owner_name == caller.owner_name}
        if owned:
            return owned
    nested = {
        item.identity for item in same_path
        if item.name == leaf and caller.start_line <= item.start_line <= item.end_line <= caller.end_line
        and item.identity != caller.identity
    }
    if nested:
        return nested
    for binding in imports_by_path.get(caller.path, []):
        imported_name = (
            leaf if binding.kind == "namespace" and callee.startswith(binding.local_name + ".")
            else binding.imported_name if binding.kind == "named" and callee == binding.local_name
            else "default" if binding.kind == "default" and callee == binding.local_name
            else None
        )
        if imported_name is not None:
            imported = {
                item.identity for item in functions_by_path.get(binding.source_path, [])
                if (imported_name == "default" and "default" in item.modifiers)
                or item.name == imported_name or item.qualified_name == imported_name
            }
            if imported:
                return imported
    local = {item.identity for item in same_path if item.name == leaf or item.qualified_name == callee}
    if local:
        return local
    qualified = by_qualified.get(callee, set())
    if len(qualified) == 1:
        return set(qualified)
    named = by_name.get(leaf, set())
    return set(named) if len(named) == 1 else set()


def build_call_graph(repo: Path, syntax_tree: Path) -> tuple[dict[str, set[str]], dict[str, FunctionInfo]]:
    """Build caller-to-callee edges for functions represented in the syntax tree."""
    catalog = _function_catalog(syntax_tree)
    by_qualified: dict[str, set[str]] = {}
    by_name: dict[str, set[str]] = {}
    functions_by_path: dict[str, list[FunctionInfo]] = {}
    for function in catalog.values():
        by_qualified.setdefault(function.qualified_name, set()).add(function.identity)
        by_name.setdefault(function.name, set()).add(function.identity)
        functions_by_path.setdefault(function.path, []).append(function)
    records = load_syntax_tree_jsonl(syntax_tree)
    imports_by_path = build_import_index(records)
    graph = {identity: set() for identity in catalog}
    for function in catalog.values():
        try:
            source = (repo / function.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for callee in extract_function_callees(source, function):
            graph[function.identity].update(_resolve_callee(function, callee, functions_by_path, imports_by_path, by_qualified, by_name))
    return graph, catalog


def _reachable(graph: dict[str, set[str]], root: str) -> set[str]:
    """Return nodes reachable from ``root`` without including the root itself."""
    found: set[str] = set()
    pending = list(graph.get(root, ()))
    while pending:
        identity = pending.pop()
        if identity == root or identity in found:
            continue
        found.add(identity)
        pending.extend(graph.get(identity, ()))
    return found


def find_complex_function_candidates(
    repo: Path,
    syntax_tree: Path,
    *,
    min_upstream_direct_call_count: int = 5,
) -> tuple[list[ComplexFunctionCandidate], dict[str, set[str]], dict[str, FunctionInfo]]:
    """Select named functions whose transitive callers make enough direct calls."""
    graph, catalog = build_call_graph(repo, syntax_tree)
    reverse_graph = {identity: set() for identity in catalog}
    for caller, callees in graph.items():
        for callee in callees:
            if callee in reverse_graph:
                reverse_graph[callee].add(caller)
    candidates: list[ComplexFunctionCandidate] = []
    for function in catalog.values():
        if function.is_anonymous or function.node_type not in {"function", "method"}:
            continue
        upstream = _reachable(reverse_graph, function.identity)
        direct_call_total = sum(len(graph[caller]) for caller in upstream)
        if direct_call_total < min_upstream_direct_call_count:
            continue
        candidates.append(ComplexFunctionCandidate(function, len(upstream), direct_call_total))
    return sorted(candidates, key=lambda item: (-item.upstream_direct_call_count, -item.upstream_function_count, item.function.path, item.function.start_line)), graph, catalog


def _positive_int(value: str) -> int:
    """验证命令行阈值为正整数。"""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def main(argv: list[str] | None = None) -> int:
    """读取命令行输入并将复杂函数候选以 JSON 输出到标准输出。"""
    parser = argparse.ArgumentParser(description="Filter ArkTS functions by upstream call complexity")
    parser.add_argument("repo", type=Path, help="local repository checkout")
    parser.add_argument("syntax_tree", type=Path, help="ArkTS syntax-tree JSONL")
    parser.add_argument("--min-upstream-direct-call-count", type=_positive_int, default=5)
    args = parser.parse_args(argv)
    if not args.repo.is_dir():
        parser.error(f"repository directory does not exist: {args.repo}")
    if not args.syntax_tree.is_file():
        parser.error(f"syntax-tree JSONL does not exist: {args.syntax_tree}")
    candidates, _, _ = find_complex_function_candidates(
        args.repo,
        args.syntax_tree,
        min_upstream_direct_call_count=args.min_upstream_direct_call_count,
    )
    print(json.dumps([candidate.to_dict() for candidate in candidates], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
