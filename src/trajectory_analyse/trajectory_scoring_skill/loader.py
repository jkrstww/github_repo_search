"""Load the scoring skill without making the existing scorer depend on reports.

The loader intentionally has a small API.  The current scorer can opt into it with
``--skill-dir``; legacy callers continue to use the original prompt unchanged.
PyYAML is used when available, while JSON documents are accepted as a dependency-free
fallback (JSON is valid YAML and can be used for locally generated manifests).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on caller environment
        raise RuntimeError(
            f"Cannot read {path}: install PyYAML or provide a JSON manifest"
        ) from exc
    obj = yaml.safe_load(text)
    if not isinstance(obj, dict):
        raise ValueError(f"Expected an object in {path}")
    return obj


def load_rubric(skill_dir: Path) -> dict[str, Any]:
    """Load and lightly validate the criterion rubric."""
    rubric = _load_document(skill_dir / "rubric" / "base.yaml")
    dimensions = rubric.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set("ABCDEF"):
        raise ValueError("rubric must define dimensions A-F")
    return rubric


def task_report_path(reports_root: Path | None, instance: str) -> Path | None:
    """Resolve an optional task report without making it a required dependency."""
    if reports_root is None:
        return None
    path = reports_root / instance / "analyse.md"
    return path if path.is_file() else None


def task_cases(skill_dir: Path, instance: str) -> list[dict[str, Any]]:
    """Return only cases authored for *instance* (excluding global anchors)."""
    return [c for c in load_cases(skill_dir) if c.get("task") == instance]


def load_cases(skill_dir: Path) -> list[dict[str, Any]]:
    """Return registered reusable cases, tolerating an absent optional index.

    Case records are deliberately treated as data, not instructions.  Malformed
    records are skipped so that adding a draft example cannot disable scoring.
    """
    index = skill_dir / "cases" / "index.yaml"
    if not index.is_file():
        return []
    obj = _load_document(index)
    cases = obj.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError(f"cases must be a list in {index}")
    return [case for case in cases if isinstance(case, dict) and case.get("id")]


def _case_block(
    cases: list[dict[str, Any]], instance: str, criterion: str, limit: int = 3
) -> str:
    """Render task-local examples first, then cross-task anchors.

    A task case is useful for semantics, while a cross-task case only explains a
    scoring pattern.  Keeping both labels explicit prevents the judge from treating
    another task's behavior as this task's oracle.
    """
    selected = []
    ordered = sorted(
        cases,
        key=lambda c: (0 if c.get("task") == instance else 1 if c.get("task") == "cross-task" else 2),
    )
    for case in ordered:
        criteria = {part.strip() for part in str(case.get("criterion", "")).split(",")}
        if criterion in criteria:
            selected.append(case)
        if len(selected) >= limit:
            break
    if not selected:
        return f"### {criterion}\n暂无可复用案例；仅依据当前轨迹证据评分。"
    lines = [f"### {criterion}"]
    for case in selected:
        lines.append(
            f"- [{case.get('type', 'case')}] {case.get('id', 'unknown')}: "
            f"{case.get('summary', '')} (task={case.get('task', 'unknown')}; "
            f"source: {case.get('source', 'unknown')})"
        )
    return "\n".join(lines)


def _task_report_context(reports_root: Path | None, instance: str) -> str:
    if reports_root is None:
        return "任务级 analyse.md：不可用（盲评模式）。仅依据当前轨迹证据评分；将任务语义缺口记录为 unknown，不要从其他任务推断。"
    report = task_report_path(reports_root, instance)
    if report is None:
        return "任务级 analyse.md：不可用。仅依据当前轨迹证据评分；将任务语义缺口记录为 unknown，不要从其他任务推断。"
    text = report.read_text(encoding="utf-8", errors="replace")
    # Reports contain external resolved labels. Keep the report useful as an oracle
    # source while explicitly instructing the judge not to use labels as evidence.
    # Remove explicit outcome labels and common table cells containing them.  The
    # report remains an optional semantic hint; direct trajectory evidence wins.
    text = re.sub(r"(?im)^.*\bresolved\s*[:=]\s*(true|false)\b.*$", "[external label omitted]", text)
    text = re.sub(r"(?im)^.*\|\s*(true|false)\s*\|.*$", "[external label omitted]", text)
    if len(text) > 18000:
        text = text[:18000] + "\n[analyse.md truncated; treat omitted facts as unknown]"
    return text


def build_context_prompt(
    skill_dir: Path,
    instance: str,
    trajectory_path: str,
    *,
    reports_root: Path | None = None,
) -> str:
    """Build the dynamic skill context appended to the legacy prompt.

    This is deliberately additive: callers can preserve the established strict JSON
    contract while gaining criterion-level examples and optional task context.
    """
    rubric = load_rubric(skill_dir)
    cases = load_cases(skill_dir)
    criteria = []
    for dimension in rubric["dimensions"].values():
        for criterion in dimension.get("criteria", []):
            criteria.append(str(criterion["id"]))
    case_text = "\n\n".join(
        _case_block(cases, instance, criterion) for criterion in criteria
    )
    missing_criteria = [
        criterion for criterion in criteria
        if not any(
            criterion in {part.strip() for part in str(case.get("criterion", "")).split(",")}
            for case in cases
        )
    ]
    coverage_note = (
        "当前案例索引缺少评分点：" + ", ".join(missing_criteria) + "。这些评分点不得臆造案例。"
        if missing_criteria else "案例索引已覆盖 A1-F3；仍须以当前轨迹证据为准。"
    )
    task_case_available = (
        reports_root is not None and (reports_root / instance / "analyse.md").is_file()
    )
    return f"""

## 可组合评分 skill 上下文

当前任务：`{instance}`；轨迹来源：`{trajectory_path}`；
任务案例可用：`{"yes" if task_case_available else "no"}`。
评分前按 discover → oracle → criterion scoring → caps → validation 执行。以下案例
只用于解释评分档位，不能替代当前轨迹中的直接证据，也不能把其他任务的行为当作
当前任务的 oracle。外部 `resolved` 标签不是评分证据。

### 任务级资料

{_task_report_context(reports_root, instance)}

### 评分点案例索引

{case_text}

对 A1 至 F3 的每个评分点先单独判断，再汇总到 A-F；在评分证据中引用适用的
案例 id（可增加顶层 `criterion_scores` 数组记录每个评分点的分数、证据和
`case_refs`）。案例只定义档位锚点，不替代轨迹证据。如果任务级资料缺失，加入
`NO_TASK_CASES`；仍然可以根据轨迹直接证据评分，但必须在 `oracle.unknowns` 和
`evidence_gaps` 中记录无法确认的语义。
{coverage_note}
""".strip()
