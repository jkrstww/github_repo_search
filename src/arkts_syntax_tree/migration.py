from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCOPE_NODE_TYPES = {"class", "struct", "interface", "function", "method", "callback"}


def detect_android_calls(syntax_tree_path: str | Path) -> dict[str, Any]:
    """Find direct ``android.*(...)`` calls in a parsed syntax-tree JSONL file."""
    path = Path(syntax_tree_path)
    records = _load_records(path)
    missing_call_data = [
        record.get("path", "<unknown>") for record in records if "calls" not in record
    ]
    if missing_call_data:
        raise ValueError(
            "syntax tree does not contain call-expression data; regenerate it with "
            "tools/parse_arkts_syntax_tree.py"
        )

    matches: list[dict[str, Any]] = []
    calls_scanned = 0
    for record in records:
        calls = record.get("calls", [])
        calls_scanned += len(calls)
        for call in calls:
            callee = call.get("callee", "")
            if not callee.startswith("android."):
                continue
            line = int(call.get("line", 1))
            matches.append(
                {
                    "path": record.get("path", ""),
                    "line": line,
                    "column": int(call.get("column", 1)),
                    "callee": callee,
                    "scope": _find_scope(record.get("tree", {}), line),
                }
            )

    files_with_matches = sorted({match["path"] for match in matches})
    return {
        "schema_version": 1,
        "syntax_tree": str(path),
        "files_scanned": len(records),
        "calls_scanned": calls_scanned,
        "android_call_count": len(matches),
        "files_with_android_calls": files_with_matches,
        "android_calls": matches,
    }


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fp:
        for line_number, line in enumerate(fp, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid syntax-tree JSON at line {line_number}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"invalid syntax-tree record at line {line_number}: expected object")
            records.append(record)
    return records


def _find_scope(tree: dict[str, Any], line: int) -> dict[str, Any] | None:
    best: tuple[int, dict[str, Any], list[str]] | None = None

    def visit(node: dict[str, Any], names: list[str]) -> None:
        nonlocal best
        start = int(node.get("start_line", 1))
        end = int(node.get("end_line", start))
        if not start <= line <= end:
            return

        node_type = node.get("type", "")
        node_name = node.get("name", "")
        next_names = names
        if node_type in SCOPE_NODE_TYPES and node_name:
            next_names = names + [node_name]
            span = end - start
            if best is None or span <= best[0]:
                best = (span, node, next_names)
        for child in node.get("children", []):
            visit(child, next_names)

    visit(tree, [])
    if best is None:
        return None
    _, node, names = best
    return {
        "type": node.get("type", ""),
        "name": node.get("name", ""),
        "qualified_name": ".".join(names),
        "start_line": int(node.get("start_line", 1)),
        "end_line": int(node.get("end_line", node.get("start_line", 1))),
    }
