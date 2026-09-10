#!/usr/bin/env python3
"""Join Codex trajectory scores with resolved labels into a tidy CSV.

For every ``(instance, submission)`` in the compare file, load the matching
``<base>.score.json`` produced by ``score_trajectories.py``, read the judging
micro-dimension scores, and emit one row in ``scored_resolved.csv`` together with
that pair's ``resolved`` value (taken ONLY here, from the compare file, never from
the score file). Pairs that cannot be scored land in ``unscorable.jsonl``.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import score_trajectories

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_COMPARE = score_trajectories.DEFAULT_COMPARE
DEFAULT_TRAJS_ROOT = score_trajectories.DEFAULT_TRAJS_ROOT
DEFAULT_OUTPUT = SCRIPT_DIR / "experiments" / "diff_trajectories" / "verified" / "scored_resolved.csv"
DEFAULT_UNSCORABLE = DEFAULT_OUTPUT.with_name("unscorable.jsonl")

CSV_FIELDS = [
    "instance", "submission", "model_name",
    "A", "B", "C", "D", "E", "F",
    "raw_total", "total_score", "evidence_confidence", "caps_or_flags", "resolved",
]


def _model_name(trajs_root: Path, instance: str, sub_key: str) -> str:
    try:
        obj = json.loads((trajs_root / instance / sub_key).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    agent = obj.get("agent") if isinstance(obj, dict) else None
    if isinstance(agent, dict):
        return str(agent.get("model_name") or "unknown")
    return "unknown"


def _row_from_score(pair: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
    scores = score.get("scores", {})
    row: dict[str, Any] = {
        "instance": pair["instance"],
        "submission": pair["submission"],
        "A": _num(scores, "A"),
        "B": _num(scores, "B"),
        "C": _num(scores, "C"),
        "D": _num(scores, "D"),
        "E": _num(scores, "E"),
        "F": _num(scores, "F"),
        "raw_total": score.get("raw_total_score", ""),
        "total_score": score.get("total_score", ""),
        "evidence_confidence": score.get("evidence_confidence", ""),
        "caps_or_flags": ";".join(filter(None, score.get("caps_or_flags", []) or [])),
        "resolved": pair["resolved"],
    }
    return row


def _num(scores: Any, dim: str) -> Any:
    entry = scores.get(dim) if isinstance(scores, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("score"), (int, float)):
        return entry["score"]
    return ""


def build(
    compare_path: Path,
    trajs_root: Path,
    output_csv: Path,
    unscorable_path: Path,
    *,
    judge_id: str | None = None,
) -> tuple[int, int]:
    """Write scored rows + unsocrable log. Returns (n_scored, n_unscorable)."""
    pairs = score_trajectories.enumerate_pairs(compare_path, trajs_root, judge_id)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    n_scored = 0
    unscorable: list[dict[str, Any]] = []
    with output_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for pair in pairs:
            sp = Path(pair["score_path"])
            if not sp.is_file():
                unscorable.append(_uns(pair, "missing_score"))
                continue
            try:
                score = json.loads(sp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                unscorable.append(_uns(pair, "score_not_json"))
                continue
            ok, reason = score_trajectories.validate_score(score)
            if not ok:
                unscorable.append(_uns(pair, f"invalid_score:{reason}"))
                continue
            # an invalid placeholder has _meta.valid=False (parse failure / API error);
            # it is structurally valid (6x zero summing to 0) but NOT a real score,
            # so drop it to unscorable unless the file predates `_meta`.
            meta = score.get("_meta") if isinstance(score, dict) else None
            if isinstance(meta, dict) and "valid" in meta and not bool(meta.get("valid")):
                reason = f"placeholder:{meta.get('reason','invalid')}"
                unscorable.append(_uns(pair, reason))
                continue
            row = _row_from_score(pair, score)
            sub_key = pair["submission"] + ".json"
            row["model_name"] = _model_name(trajs_root, pair["instance"], sub_key)
            writer.writerow(row)
            n_scored += 1
    with unscorable_path.open("w", encoding="utf-8") as fh:
        for u in unscorable:
            fh.write(json.dumps(u, ensure_ascii=False) + "\n")
    return n_scored, len(unscorable)


def _uns(pair: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"instance": pair["instance"], "submission": pair["submission"], "reason": reason}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--compare", type=Path, default=DEFAULT_COMPARE)
    p.add_argument("--trajs-root", "--trajs_root", type=Path, default=DEFAULT_TRAJS_ROOT)
    p.add_argument("--judge-id", "--judge_id", default=None)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--unscorable", type=Path, default=DEFAULT_UNSCORABLE)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.compare = args.compare.expanduser().resolve()
    args.trajs_root = args.trajs_root.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.unscorable = args.unscorable.expanduser().resolve()
    n_scored, n_unscorable = build(
        args.compare, args.trajs_root, args.output, args.unscorable, judge_id=args.judge_id,
    )
    print(f"wrote {n_scored} scored rows -> {args.output}")
    print(f"wrote {n_unscorable} unscorable entries -> {args.unscorable}")
    if n_unscorable:
        print("(run score_trajectories.py to fill missing scores)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
