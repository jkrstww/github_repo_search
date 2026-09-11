#!/usr/bin/env python3
"""Correlate Codex trajectory scores with the resolved ground-truth label.

Reads the tidy ``scored_resolved.csv`` produced by ``build_scores.py`` and reports
how well the rubric's scores discriminate resolved vs unresolved runs. The
analysis deliberately keeps non-independence in view: the same SWE-bench
instance is scored under multiple models, so the *primary* signal is the
within-instance paired delta (which cancels instance difficulty), and global
estimates carry cluster-bootstrap CIs over instances.

Outputs ``evaluation_results.json`` (machine) and ``evaluation_report.md`` (human).
Pure stdlib + ``scoring_stats``; no numpy/scipy required.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import scoring_stats as ss

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "experiments" / "diff_trajectories" / "verified" / "scored_resolved.csv"
DEFAULT_RESULTS = SCRIPT_DIR / "experiments" / "diff_trajectories" / "verified" / "evaluation_results.json"
DEFAULT_REPORT = SCRIPT_DIR / "experiments" / "diff_trajectories" / "verified" / "evaluation_report.md"
DIMENSIONS = ("A", "B", "C", "D", "E", "F")
PROCESS_DIMS = ("A", "B", "E")
OUTCOME_DIMS = ("C", "D", "F")
DEFAULT_N_BOOT = 1000

# Schema consumed from scored_resolved.csv; keep in sync with build_scores.CSV_FIELDS.
CSV_FIELDS = [
    "instance", "submission", "model_name",
    "A", "B", "C", "D", "E", "F",
    "raw_total", "total_score", "evidence_confidence", "caps_or_flags", "resolved",
]

_LIMITATIONS = (
    "## Limitations\n",
    "- **Outcome-entangled caps**: the rubric's caps (empty/unappliable patch -> total<=49, "
    "persisting runtime errors -> <=59, no post-fix verification -> <=74) partly overlap the definition of NOT-resolved, "
    "so C/D/F (and total) correlate with resolved partly by construction. The A+B+E vs C+D+F AUC split quantifies the rubric's "
    "added signal beyond outcome.\n",
    "- **Judge self-family bias**: Codex (gpt-5 family) judges gpt-5.2/kimi/gemini trajectories; possible in-group favoritism. ",
    "Re-judge a subset with a different model or humans to bound it.\n",
    "- **Selection bias**: all 11 joinable submissions use the mini-swe-agent harness and skew resolved-high (~0.66); ",
    "AUC ceiling is limited. Population = mini-swe-agent family runs on SWE-bench Verified.\n",
    "- **Epistemic circularity**: this rubric was derived from LLM analysis of trajectories; validating with an LLM judge is ",
    "partly circular. Triangulate with a human spot-check of 30-50 cases (human vs codex, human vs resolved).\n",
    "- **Per-instance label trust**: compare_verified.json already drops the 0/500 gemini-pro submission; if any other ",
    "submission's per_instance_details.json is similarly poisoned, downstream labels are wrong.\n",
)


def _to_bool(v: str) -> int:
    return 1 if str(v).strip().lower() in ("true", "1", "yes") else 0


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                total = float(r["total_score"])
            except (TypeError, ValueError):
                continue
            dims = {}
            for d in DIMENSIONS:
                try:
                    dims[d] = float(r[d])
                except (TypeError, ValueError):
                    dims[d] = 0.0
            rows.append({
                "instance": r["instance"], "submission": r["submission"],
                "model": r.get("model_name", "unknown"), "dims": dims,
                "total": total, "resolved": _to_bool(r["resolved"]),
                "confidence": r.get("evidence_confidence", ""), "flags": r.get("caps_or_flags", ""),
            })
    return rows


def _by_instance(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["instance"], []).append(r)
    return out


def within_instance(results: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Primary signal: per-instance delta of total_score (T mean - F mean)."""
    deltas: list[float] = []
    by_inst = _by_instance(rows)
    for inst, group in by_inst.items():
        ts = [g["total"] for g in group if g["resolved"] == 1]
        fs = [g["total"] for g in group if g["resolved"] == 0]
        if ts and fs:
            deltas.append(ss.mean(ts) - ss.mean(fs))
    sign = ss.sign_test(deltas)
    wilc = ss.wilcoxon_signed_rank(deltas)
    results["within_instance"] = {
        "n_instances_with_both": len(deltas),
        "delta_mean": round(ss.mean(deltas), 4) if deltas else float("nan"),
        "delta_positive_fraction": round(sign["pos"] / (sign["n"] or 1), 4),
        "sign_test": sign,
        "wilcoxon": wilc,
    }


def _cluster_rows(rows: list[dict[str, Any]], key: str):
    cids = [r["instance"] for r in rows]
    vals = [(r["resolved"], r[key]) for r in rows]
    return cids, vals


def global_discrimination(results: dict[str, Any], rows: list[dict[str, Any]], n_boot: int) -> None:
    y = [r["resolved"] for r in rows]
    s = [r["total"] for r in rows]
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    auc = ss.auc_roc(y, s)
    pr = ss.pr_auc(y, s)
    qcids, qvals = _cluster_rows(rows, "total")
    point, clo, chi = ss.cluster_bootstrap_ci(
        qcids, qvals,
        lambda subset: ss.point_correlation([v[0] for v in subset], [v[1] for v in subset]),
        n_boot=n_boot, seed=1,
    )
    auc_ci = ss.cluster_bootstrap_ci(
        qcids, qvals,
        lambda subset: ss.auc_roc([v[0] for v in subset], [v[1] for v in subset]),
        n_boot=n_boot, seed=2,
    )[1:3]
    t_scores = [r["total"] for r in rows if r["resolved"] == 1]
    f_scores = [r["total"] for r in rows if r["resolved"] == 0]
    mw = ss.mann_whitney_u(t_scores, f_scores)
    results["global"] = {
        "n_pairs": len(rows), "n_resolved_true": n_pos, "n_resolved_false": n_neg,
        "base_rate": round(n_pos / (len(y) or 1), 4),
        "auc_roc": round(auc, 4) if auc == auc else None, "auc_roc_ci95": _ci_fmt(auc_ci),
        "pr_auc": round(pr, 4) if pr == pr else None,
        "point_biserial": round(point, 4) if point == point else None,
        "point_biserial_ci95": _ci_fmt((clo, chi)),
        "score_mean_true": round(ss.mean(t_scores), 4), "score_mean_false": round(ss.mean(f_scores), 4),
        "mann_whitney": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in mw.items()},
    }


def per_dimension(results: dict[str, Any], rows: list[dict[str, Any]], n_boot: int) -> None:
    out: dict[str, Any] = {}
    for d in DIMENSIONS:
        y = [r["resolved"] for r in rows]
        vals = [r["dims"][d] for r in rows]
        cids = [r["instance"] for r in rows]
        pairs = list(zip(y, vals))
        pt, lo, hi = ss.cluster_bootstrap_ci(
            cids, pairs,
            lambda subset: ss.point_correlation([v[0] for v in subset], [v[1] for v in subset]),
            n_boot=n_boot, seed=3 + DIMENSIONS.index(d),
        )
        out[d] = {
            "point_biserial": round(pt, 4) if pt == pt else None,
            "ci95": _ci_fmt((lo, hi)),
        }
    results["per_dimension"] = out


def dimension_decomposition(results: dict[str, Any], rows: list[dict[str, Any]], n_boot: int) -> None:
    sets = {
        "process_only_AB_E": PROCESS_DIMS,
        "outcome_CDF": OUTCOME_DIMS,
        "full_total": None,
    }
    out: dict[str, Any] = {}
    for name, dims in sets.items():
        if dims is None:
            key = "total"
        else:
            key = "_sum_" + name
            for r in rows:
                r[key] = sum(r["dims"][d] for d in dims)
        y = [r["resolved"] for r in rows]
        cids = [r["instance"] for r in rows]
        pairs = list(zip(y, [r[key] for r in rows]))
        auc = ss.auc_roc(y, [r[key] for r in rows])
        ci = ss.cluster_bootstrap_ci(
            cids, pairs,
            lambda subset: ss.auc_roc([v[0] for v in subset], [v[1] for v in subset]),
            n_boot=n_boot, seed=10,
        )[1:3]
        out[name] = {"auc_roc": round(auc, 4) if auc == auc else None, "ci95": _ci_fmt(ci)}
    results["decomposition"] = out


def _youden_balanced_accuracy(y: list[int], scores: list[float]) -> dict[str, Any]:
    if not y or not scores:
        return {}
    thresholds = sorted(set(scores))
    best = {"ba": 0.0, "threshold": None}
    for t in thresholds:
        tp = sum(1 for yv, sv in zip(y, scores) if yv == 1 and sv >= t)
        fn = sum(1 for yv, sv in zip(y, scores) if yv == 1 and sv < t)
        fp = sum(1 for yv, sv in zip(y, scores) if yv == 0 and sv >= t)
        tn = sum(1 for yv, sv in zip(y, scores) if yv == 0 and sv < t)
        tpr = tp / (tp + fn) if (tp + fn) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        tnr = 1.0 - fpr
        ba = (tpr + tnr) / 2.0
        if ba > best["ba"]:
            best = {"ba": round(ba, 4), "threshold": t, "tpr": round(tpr, 4), "tnr": round(tnr, 4)}
    return best


def _ci_fmt(ci: tuple[float, float]) -> Any:
    lo, hi = ci
    if lo != lo or hi != hi:  # nan
        return None
    return [round(lo, 4), round(hi, 4)]


def reliability(results: dict[str, Any], rows: list[dict[str, Any]], extra_rows: list[dict[str, Any]]) -> None:
    idx2 = {(r["instance"], r["submission"]): r for r in extra_rows}
    j1: list[float] = []
    j2: list[float] = []
    by_dim_j1 = {d: [] for d in DIMENSIONS}
    by_dim_j2 = {d: [] for d in DIMENSIONS}
    for r in rows:
        key = (r["instance"], r["submission"])
        if key not in idx2:
            continue
        o = idx2[key]
        try:
            ot = float(o["total"])
        except (TypeError, ValueError, KeyError):
            continue
        j1.append(r["total"])
        j2.append(ot)
        for d in DIMENSIONS:
            by_dim_j1[d].append(r["dims"][d])
            # load_rows stores per-dim under row["dims"][d] (not row[d]); handle empties
            dim_o = o["dims"].get(d)
            try:
                by_dim_j2[d].append(float(dim_o))
            except (TypeError, ValueError):
                by_dim_j2[d].append(0.0)
    if len(j1) < 2:
        results["reliability"] = {"n_overlapping": len(j1), "note": "too few overlapping pairs"}
        return
    total_icc = ss.icc_2_1(j1, j2)
    dims = {}
    for d in DIMENSIONS:
        dims[d] = ss.icc_2_1(by_dim_j1[d], by_dim_j2[d])
    results["reliability"] = {
        "n_overlapping": len(j1),
        "total_icc": round(total_icc["icc"], 4) if total_icc["icc"] == total_icc["icc"] else None,
        "total_pearson": round(total_icc["pearson"], 4) if total_icc["pearson"] == total_icc["pearson"] else None,
        "per_dimension": {
            d: {
                "icc": round(v["icc"], 4) if v["icc"] == v["icc"] else None,
                "pearson": round(v["pearson"], 4) if v["pearson"] == v["pearson"] else None,
            } for d, v in dims.items()
        },
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    return str(v)


def write_report(path: Path, results: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    g = results.get("global", {})
    w = results.get("within_instance", {})
    dec = results.get("decomposition", {})
    pdim = results.get("per_dimension", {})
    lines: list[str] = []
    lines.append("# 轨迹打分 vs resolved 相关性报告\n")
    lines.append(f"- 可打分 pair 数: {g.get('n_pairs')}; " f"resolved True/False = {g.get('n_resolved_true')}/{g.get('n_resolved_false')}; "
                  f"base rate = {g.get('base_rate')}\n")
    lines.append(f"- 抽样单元: 非独立的 (instance × model) 对; 主信号=实例内配对 Δ(cluster-bootstrap CI 聚类于 instance)\n\n")

    lines.append("## 1. 主信号:实例内配对 (primary)\n")
    lines.append(f"- 在 {w.get('n_instances_with_both')} 个同时含 True/False 的 instance 内计算 Δ = mean(分|True) - mean(分|False)\n")
    lines.append(f"- Δ 均值 = {w.get('delta_mean')}; Δ>0 的 instance 比例 = {w.get('delta_positive_fraction')}\n")
    st_ = w.get("sign_test", {})
    lines.append(f"- 符号检验: +{st_.get('pos')}/-{st_.get('neg')}/0×{st_.get('zeros')} "
                  f"(n={st_.get('n')}, p={st_.get('p')})\n")
    wl = w.get("wilcoxon", {})
    lines.append(f"- Wilcoxon signed-rank: W+={wl.get('W_plus')}, z={wl.get('z')}, p={wl.get('p')}\n")
    lines.append("> 同题内消掉实例难度后才回答「分高的 run 更可能是 resolved 的吗」。\n\n")

    lines.append("## 2. 全局判别力 (cluster-bootstrap CI 聚类于 instance)\n")
    lines.append(f"- AUC-ROC = {g.get('auc_roc')}  CI95 = {g.get('auc_roc_ci95')}\n")
    lines.append(f"- PR-AUC = {g.get('pr_auc')}  (base rate = {g.get('base_rate')})\n")
    lines.append(f"- point-biserial r = {g.get('point_biserial')}  CI95 = {g.get('point_biserial_ci95')}\n")
    lines.append(f"- 分组均值: True={g.get('score_mean_true')}, False={g.get('score_mean_false')}\n")
    mw = g.get("mann_whitney", {})
    lines.append(f"- Mann-Whitney: U={mw.get('U')}, z={mw.get('z')}, p={mw.get('p')}, effect={mw.get('effect')}\n")
    ba = results.get("threshold", {})
    if ba:
        lines.append(f"- Youden 最优阈值: threshold={ba.get('threshold')}, balanced acc={ba.get('ba')} "
                      f"(TPR={ba.get('tpr')}, TNR={ba.get('tnr')})\n")
    lines.append("\n## 3. 逐维度 point-biserial (cluster-bootstrap CI 聚类于 instance)\n")
    lines.append("| 维度 | r | CI95 |\n|---|---|---|\n")
    names = {"A": "A 任务理解", "B": "B 根因定位", "C": "C 实现正确性", "D": "D 测试证据",
             "E": "E 执行纪律", "F": "F 收尾卫生"}
    for d in DIMENSIONS:
        v = pdim.get(d, {})
        lines.append(f"| {names[d]} | {v.get('point_biserial')} | {v.get('ci95')} |\n")

    lines.append("\n## 4. 维度分解:过程信号 vs outcome 同义\n")
    lines.append("> 若 process_only(A+B+E) 的 AUC 明显低于 outcome(C+D+F)/full,说明相关主要来自与 resolved 同义的 caps;\n")
    lines.append("> 若 A+B+E 仍有实质性 AUC,说明 rubric 确实贡献了独立的过程判别信号。\n\n")
    lines.append("| predictor | AUC-ROC | CI95 |\n|---|---|---|\n")
    for k in ("process_only_AB_E", "outcome_CDF", "full_total"):
        v = dec.get(k, {})
        lines.append(f"| {k} | {v.get('auc_roc')} | {v.get('ci95')} |\n")

    if "reliability" in results:
        r = results["reliability"]
        lines.append("\n## 5. 跨 judge 一致性 (inter-rater)\n")
        lines.append(f"- 重叠 pair 数: {r.get('n_overlapping')}\n")
        if "total_icc" in r:
            lines.append(f"- total score ICC(2,1) = {r.get('total_icc')}, Pearson = {r.get('total_pearson')}\n")
            lines.append("| 维度 | ICC | Pearson |\n|---|---|---|\n")
            for d, dv in r.get("per_dimension", {}).items():
                lines.append(f"| {d} | {dv.get('icc')} | {dv.get('pearson')} |\n")

    lines.append("\n")
    lines.extend(_LIMITATIONS)
    path.write_text("".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    p.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--extra-judge-csv", "--extra_judge_csv", type=Path, default=None,
                   help="a second judge's scored_resolved.csv for inter-rater ICC")
    p.add_argument("--n-boot", "--n_boot", type=int, default=DEFAULT_N_BOOT)
    return p.parse_args(argv)


def run(path: Path, n_boot: int, extra_path: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = load_rows(path)
    results: dict[str, Any] = {"n_pairs": len(rows),
                               "n_instances": len({r["instance"] for r in rows})}
    within_instance(results, rows)
    global_discrimination(results, rows, n_boot)
    per_dimension(results, rows, n_boot)
    dimension_decomposition(results, rows, n_boot)
    results["threshold"] = _youden_balanced_accuracy([r["resolved"] for r in rows], [r["total"] for r in rows])
    if extra_path:
        reliability(results, rows, load_rows(extra_path))
    # flag frequency
    flags: dict[str, int] = {}
    for r in rows:
        for f in (r["flags"].split(";") if r["flags"] else []):
            f = f.strip()
            if f:
                flags[f] = flags.get(f, 0) + 1
    results["caps_or_flags_frequency"] = dict(sorted(flags.items(), key=lambda kv: -kv[1]))
    results.pop("_conf_seen", None)
    return results, rows


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.input = args.input.expanduser().resolve()
    args.results = args.results.expanduser().resolve()
    args.report = args.report.expanduser().resolve()
    if args.extra_judge_csv:
        args.extra_judge_csv = args.extra_judge_csv.expanduser().resolve()
    results, _rows = run(args.input, args.n_boot, args.extra_judge_csv)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(args.report, results, _rows)
    print(f"wrote results -> {args.results}")
    print(f"wrote report -> {args.report}")
    g = results.get("global", {})
    print("\nHEADLINE:")
    print(f"  AUC-ROC={g.get('auc_roc')}  PR-AUC={g.get('pr_auc')}  r_pb={g.get('point_biserial')}")
    w = results.get("within_instance", {})
    print(f"  within-instance Δ mean={w.get('delta_mean')}  sign p={w.get('sign_test',{}).get('p')}  wilcoxon p={w.get('wilcoxon',{}).get('p')}")
    dec = results.get("decomposition", {})
    print(f"  AUC process(A+B+E)={dec.get('process_only_AB_E',{}).get('auc_roc')} "
          f"outcome(C+D+F)={dec.get('outcome_CDF',{}).get('auc_roc')} full={dec.get('full_total',{}).get('auc_roc')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
