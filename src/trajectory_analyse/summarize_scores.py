#!/usr/bin/env python3
"""对一个 job 目录下的轨迹 rubric 打分结果做汇总。

用法:
    python summarize_scores.py <root> [--scores-dir NAME] [--rubric PATH] [--output PATH]

    root          job 根目录，目录布局 <root>/<sample>/agent/<scores-dir>/*.score.json
    --scores-dir  每个样本的打分目录名；在 job 内唯一时可自动检测，
                  不唯一时必须显式指定。
    --rubric      提供子准则名称/满分的 rubric YAML
                  （默认: trajectory_scoring_skill/rubric/base.yaml）
    --output      汇总输出路径（默认: <root>/<scores-dir-name>-summary.json）
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RUBRIC = SCRIPT_DIR / "trajectory_scoring_skill" / "rubric" / "base.yaml"
DIMENSIONS = "ABCDEF"

MEAN_POLICY = (
    "Each criterion/subcriterion mean excludes trajectories with a missing, "
    "errored, or non-applicable dimension result; unavailable scores are not "
    "treated as zero."
)


# ---------------------------------------------------------------------------
# rubric


def load_rubric(path: Path | None) -> dict[str, Any]:
    """Extract criterion/subcriterion names and max scores from base.yaml.

    The project is stdlib-only, so instead of a full YAML parser this uses an
    indentation-based reader for the fixed rubric layout::

        - id: A            (indent 4)
          name: ...        (indent 6)
          max_score: 15    (indent 6)
          subcriteria:
            - id: A1       (indent 8)
              name: ...    (indent 10)
              score: 5     (indent 10)
    """
    criteria: dict[str, Any] = {}
    if path is None or not Path(path).is_file():
        return criteria
    cur_c: str | None = None
    cur_s: str | None = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\s*)- id:\s*([A-F]\d*)\s*$", line)
        if m:
            indent = len(m.group(1))
            if indent <= 4:
                cur_c = m.group(2)
                cur_s = None
                criteria.setdefault(
                    cur_c, {"id": cur_c, "name": None, "max_score": None, "subcriteria": {}}
                )
            elif cur_c is not None:
                cur_s = m.group(2)
                criteria[cur_c]["subcriteria"].setdefault(
                    cur_s, {"id": cur_s, "name": None, "max_score": None}
                )
            continue
        m = re.match(r"^(\s*)(name|max_score|score):\s*(.+?)\s*$", line)
        if not m or cur_c is None:
            continue
        indent, key, val = len(m.group(1)), m.group(2), m.group(3)
        if cur_s is not None and indent >= 10:
            target: dict[str, Any] = criteria[cur_c]["subcriteria"][cur_s]
        elif indent <= 6:
            target = criteria[cur_c]
        else:
            continue
        if key == "name":
            target["name"] = val.strip('"').strip("'")
        else:
            try:
                target["max_score"] = float(val)
            except ValueError:
                pass
    return criteria


# ---------------------------------------------------------------------------
# score file discovery


def find_score_files(root: Path, scores_dir_name: str | None) -> tuple[list[Path], str]:
    """Locate merged score files under ``root``.

    Returns ``(files, scores_dir_name)``. Without an explicit ``scores_dir_name``
    the directory name is auto-detected; it must be unique across all matches.
    """
    files = sorted(p for p in root.rglob("*.score.json") if p.is_file())
    if scores_dir_name is not None:
        matched = [p for p in files if p.parent.name == scores_dir_name]
        return matched, scores_dir_name
    names = {p.parent.name for p in files}
    if not files:
        raise SystemExit(f"no *.score.json files found under {root}")
    if len(names) != 1:
        raise SystemExit(
            "multiple scores directories found; pick one with --scores-dir: "
            + ", ".join(sorted(names))
        )
    return files, names.pop()


# ---------------------------------------------------------------------------
# per-trajectory extraction


def dimension_errors(score_file: Path) -> dict[str, str]:
    """Recover per-dimension error strings from sibling ``*.score.<D>.json``."""
    stem = score_file.name[: -len(".score.json")]
    errors: dict[str, str] = {}
    for dim in DIMENSIONS:
        path = score_file.with_name(f"{stem}.score.{dim}.json")
        if not path.is_file():
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors[dim] = "dimension file is not valid JSON"
            continue
        if not isinstance(obj, dict):
            errors[dim] = "dimension file is not an object"
            continue
        if obj.get("error"):
            errors[dim] = str(obj["error"])
        elif obj.get("applicable") is False:
            errors[dim] = str(obj.get("error") or "dimension not applicable")
    return errors


def build_result(score_file: Path, root: Path, rubric: dict[str, Any]) -> dict[str, Any]:
    """Build one per-trajectory summary entry with full criterion detail."""
    score = json.loads(score_file.read_text(encoding="utf-8"))
    errors = dimension_errors(score_file)
    scores = score.get("scores") if isinstance(score.get("scores"), dict) else {}

    criteria: dict[str, Any] = {}
    for dim in DIMENSIONS:
        entry = scores.get(dim) if isinstance(scores.get(dim), dict) else None
        rubric_c = rubric.get(dim, {})
        name = (entry or {}).get("name") or rubric_c.get("name")
        max_score = (entry or {}).get("max_score") or rubric_c.get("max_score")
        dim_out: dict[str, Any] = {"name": name, "max_score": max_score}
        if dim in errors:
            dim_out.update({"status": "error", "error": errors[dim], "score": None})
        elif entry is None or not isinstance(entry.get("score"), (int, float)):
            dim_out.update({"status": "missing", "score": None})
        else:
            dim_out.update(
                {
                    "status": "ok",
                    "score": entry["score"],
                    "level": entry.get("level"),
                }
            )
        criteria[dim] = dim_out

    sub_by_id: dict[str, Any] = {}
    raw_subs = score.get("criterion_scores")
    if isinstance(raw_subs, list):
        for item in raw_subs:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                sub_by_id[item["id"]] = item

    subcriteria: dict[str, Any] = {}
    for dim in DIMENSIONS:
        rubric_subs = rubric.get(dim, {}).get("subcriteria", {})
        sub_ids = sorted(set(rubric_subs) | {s for s in sub_by_id if s.startswith(dim)})
        for sub_id in sub_ids:
            item = sub_by_id.get(sub_id, {})
            rubric_s = rubric_subs.get(sub_id, {})
            sub_out: dict[str, Any] = {
                "name": rubric_s.get("name"),
                "max_score": item.get("max_score") or rubric_s.get("max_score"),
            }
            if isinstance(item.get("pass_rate"), (int, float)):
                # score_rules rubric: `score` is the fraction of rules that
                # passed (0..1), so surface the rule-level detail too.
                sub_out.update({
                    "scoring_format": "score_rules",
                    "pass_rate": item["pass_rate"],
                    "passed_rules": item.get("passed_rules"),
                    "total_rules": item.get("total_rules"),
                    "weighted_score": item.get("weighted_score"),
                })
            if dim in errors:
                sub_out.update({"status": "error", "error": errors[dim], "score": None})
            elif not isinstance(item.get("score"), (int, float)):
                sub_out.update({"status": "missing", "score": None})
            else:
                sub_out.update({"status": "ok", "score": item["score"]})
            subcriteria[sub_id] = sub_out

    rel = score_file.relative_to(root)
    return {
        "sample": rel.parts[0] if len(rel.parts) > 1 else score_file.stem,
        "trajectory": score.get("trajectory"),
        "score_file": str(score_file),
        "total_score": score.get("total_score"),
        "raw_total_score": score.get("raw_total_score"),
        "status": "error" if errors else "ok",
        "errors": [f"{d}: {errors[d]}" for d in DIMENSIONS if d in errors],
        "criteria": criteria,
        "subcriteria": subcriteria,
        "model": score.get("model"),
    }


# ---------------------------------------------------------------------------
# aggregation


def _stats(values: list[float], max_score: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {"sample_count": len(values)}
    if max_score is not None:
        out["max_score"] = max_score
    if not values:
        out.update({"mean_score": None, "mean_ratio": None})
        return out
    out["mean_score"] = round(st.fmean(values), 6)
    out["min_score"] = min(values)
    out["max_observed_score"] = max(values)
    out["median_score"] = st.median(values)
    out["std"] = round(st.pstdev(values), 6) if len(values) > 1 else 0.0
    if max_score:
        out["mean_ratio"] = round(st.fmean(values) / max_score, 4)
    else:
        out["mean_ratio"] = None
    return out


def aggregate(results: list[dict[str, Any]], rubric: dict[str, Any]) -> dict[str, Any]:
    criterion_means: dict[str, Any] = {}
    subcriterion_means: dict[str, Any] = {}
    for dim in DIMENSIONS:
        entries = [r["criteria"][dim] for r in results if dim in r["criteria"]]
        ok = [e for e in entries if e["status"] == "ok" and isinstance(e.get("score"), (int, float))]
        errored = [e for e in entries if e["status"] == "error"]
        max_score = next(
            (e["max_score"] for e in entries if isinstance(e.get("max_score"), (int, float))),
            rubric.get(dim, {}).get("max_score"),
        )
        name = next(
            (e["name"] for e in entries if e.get("name")), rubric.get(dim, {}).get("name")
        )
        stats = _stats([float(e["score"]) for e in ok], max_score)
        stats.update({"name": name, "error_count": len(errored)})
        criterion_means[dim] = stats

        sub_ids = sorted({s for r in results for s in r["subcriteria"] if s.startswith(dim)})
        sub_means: dict[str, Any] = {}
        for sub_id in sub_ids:
            entries_s = [r["subcriteria"][sub_id] for r in results if sub_id in r["subcriteria"]]
            ok_s = [
                e for e in entries_s if e["status"] == "ok" and isinstance(e.get("score"), (int, float))
            ]
            errored_s = [e for e in entries_s if e["status"] == "error"]
            max_s = next(
                (e["max_score"] for e in entries_s if isinstance(e.get("max_score"), (int, float))),
                rubric.get(dim, {}).get("subcriteria", {}).get(sub_id, {}).get("max_score"),
            )
            name_s = next(
                (e["name"] for e in entries_s if e.get("name")),
                rubric.get(dim, {}).get("subcriteria", {}).get(sub_id, {}).get("name"),
            )
            # score_rules subcriteria score on a 0..1 pass rate; aggregate
            # those against 1.0 and keep the weight only as metadata.
            rules_s = [float(e["pass_rate"]) for e in ok_s
                       if isinstance(e.get("pass_rate"), (int, float))]
            legacy_s = [float(e["score"]) for e in ok_s
                        if not isinstance(e.get("pass_rate"), (int, float))]
            if rules_s:
                stats_s = _stats(rules_s, 1.0)
                stats_s.update({
                    "scoring_format": "score_rules",
                    "subcriterion_weight": max_s,
                })
            else:
                stats_s = _stats(legacy_s, max_s)
            stats_s.update({"name": name_s, "error_count": len(errored_s)})
            sub_means[sub_id] = stats_s
        if sub_means:
            subcriterion_means[dim] = sub_means
    return {"criterion_means": criterion_means, "subcriterion_means": subcriterion_means}


def summarize(root: Path, scores_dir_name: str | None = None, rubric_path: Path | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve()
    rubric = load_rubric(rubric_path if rubric_path is not None else DEFAULT_RUBRIC)
    files, dir_name = find_score_files(root, scores_dir_name)
    results = [build_result(p, root, rubric) for p in files]
    results.sort(key=lambda r: r["sample"])

    totals = [float(r["total_score"]) for r in results if isinstance(r.get("total_score"), (int, float))]
    ok_results = [r for r in results if r["status"] == "ok"]
    ok_totals = [float(r["total_score"]) for r in ok_results if isinstance(r.get("total_score"), (int, float))]

    # sample directories under root that have an agent dir but no score file
    covered = {r["sample"] for r in results}
    samples_without_scores = sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and (d / "agent").is_dir() and d.name not in covered
    )

    summary: dict[str, Any] = {
        "root": str(root),
        "scores_dir": dir_name,
        "model": next(iter({r["model"] for r in results if r.get("model")}), None),
        "count": len(results),
        "successful_count": len(ok_results),
        "error_count": len(results) - len(ok_results),
        "all_results_mean": st.fmean(totals) if totals else None,
        "all_results_median": st.median(totals) if totals else None,
        "all_results_min": min(totals) if totals else None,
        "all_results_max": max(totals) if totals else None,
        "all_results_std": round(st.pstdev(totals), 6) if len(totals) > 1 else 0.0,
        "successful_results_mean": st.fmean(ok_totals) if ok_totals else None,
        "all_results_total": sum(totals),
        "successful_results_total": sum(ok_totals),
        "samples_without_scores": samples_without_scores,
        "mean_denominator_policy": MEAN_POLICY,
    }
    summary.update(aggregate(results, rubric))
    summary["results"] = results
    return summary


# ---------------------------------------------------------------------------
# console output


def _fmt_ratio(ratio: float | None) -> str:
    return f"{ratio * 100:5.1f}%" if isinstance(ratio, (int, float)) else "  n/a"


def print_report(summary: dict[str, Any]) -> None:
    print(f"root: {summary['root']}  scores_dir: {summary['scores_dir']}")
    print(
        f"count={summary['count']} ok={summary['successful_count']} "
        f"error={summary['error_count']} "
        f"mean={summary['all_results_mean'] and round(summary['all_results_mean'], 2)} "
        f"ok_mean={summary['successful_results_mean'] and round(summary['successful_results_mean'], 2)}"
    )
    header = f"{'id':<4} {'name':<22} {'mean':>7} {'max':>5} {'ratio':>7} {'n':>4} {'err':>4}"
    print(header)
    print("-" * len(header))
    for dim in DIMENSIONS:
        c = summary["criterion_means"].get(dim)
        if not c:
            continue
        mean = c["mean_score"]
        print(
            f"{dim:<4} {str(c.get('name') or ''):<22} "
            f"{mean if mean is not None else 'n/a':>7} {c.get('max_score') or '':>5} "
            f"{_fmt_ratio(c.get('mean_ratio')):>7} {c['sample_count']:>4} {c['error_count']:>4}"
        )
        for sub_id, s in summary["subcriterion_means"].get(dim, {}).items():
            smean = s["mean_score"]
            print(
                f"  {sub_id:<4} {str(s.get('name') or ''):<20} "
                f"{smean if smean is not None else 'n/a':>7} {s.get('max_score') or '':>5} "
                f"{_fmt_ratio(s.get('mean_ratio')):>7} {s['sample_count']:>4} {s['error_count']:>4}"
            )
    if summary["samples_without_scores"]:
        print("samples without scores: " + ", ".join(summary["samples_without_scores"]))


# ---------------------------------------------------------------------------
# CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="job root containing <sample>/agent/<scores-dir>/")
    parser.add_argument(
        "--scores-dir",
        default=None,
        help="name of the per-sample scores directory (auto-detected when unique)",
    )
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="summary path (default: <root>/<scores-dir-name>-summary.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = summarize(args.root, scores_dir_name=args.scores_dir, rubric_path=args.rubric)
    output = args.output or Path(summary["root"]) / f"{summary['scores_dir']}-summary.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_report(summary)
    print(f"wrote summary -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
