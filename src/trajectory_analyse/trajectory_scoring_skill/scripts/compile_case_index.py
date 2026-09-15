#!/usr/bin/env python3
"""Create a reviewable task manifest from verified ``analyse.md`` reports.

This first compiler is intentionally conservative: it records report availability and
headings, but does not invent oracle facts. Human-reviewed criterion cases live in the
checked-in ``cases/index.yaml`` and can be added incrementally.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def compile_reports(root: Path) -> dict[str, object]:
    tasks: list[dict[str, object]] = []
    for task_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        report = task_dir / "analyse.md"
        headings: list[str] = []
        report_size = 0
        if report.is_file():
            text = report.read_text(encoding="utf-8", errors="replace")
            report_size = len(text)
            headings = [m.group(1).strip() for m in re.finditer(r"^#{1,3}\s+(.+)$", text, re.M)]
        tasks.append({
            "task": task_dir.name,
            "report_available": report.is_file(),
            "report": str(report) if report.is_file() else None,
            "report_size": report_size,
            "headings": headings,
        })
    return {
        "version": "1.1",
        "source": str(root),
        "report_count": sum(bool(task["report_available"]) for task in tasks),
        "missing_count": sum(not bool(task["report_available"]) for task in tasks),
        "tasks": tasks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = compile_reports(args.reports_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    available = sum(bool(task["report_available"]) for task in manifest["tasks"])
    print(f"indexed {len(manifest['tasks'])} tasks; reports={available}; missing={len(manifest['tasks']) - available}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
