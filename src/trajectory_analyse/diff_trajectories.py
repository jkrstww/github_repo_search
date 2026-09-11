#!/usr/bin/env python3
"""Collect trajectories whose evaluation result differs between submissions.

For each submission under ``experiments/evaluation/<dataset>`` whose name starts
with the requested year, ``per_instance_details.json`` is used as the source of
the ``resolved`` value.  Samples with different values are copied from the
submission's Harbor trajectory directory into the output directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_EVALUATION_DIR = ROOT / "experiments" / "evaluation"
DEFAULT_OUTPUT_ROOT = ROOT / "experiments" / "diff_trajectories"


def find_submissions(evaluation_dir: Path, dataset: str, year: int) -> list[Path]:
    """Find submission directories for *dataset* and the given year."""
    dataset_dir = evaluation_dir / dataset
    if not dataset_dir.is_dir():
        raise ValueError(f"Dataset directory does not exist: {dataset_dir}")

    prefix = f"{year:04d}"
    return sorted(
        path
        for path in dataset_dir.iterdir()
        if path.is_dir() and path.name.startswith(prefix)
    )


def load_resolved(submission_dir: Path) -> dict[str, Any]:
    """Load ``sample -> resolved`` values from one submission."""
    details_path = submission_dir / "per_instance_details.json"
    if not details_path.is_file():
        raise FileNotFoundError(f"Missing {details_path}")
    payload = json.loads(details_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object in {details_path}")

    resolved: dict[str, Any] = {}
    for sample, details in payload.items():
        if not isinstance(sample, str) or not isinstance(details, dict):
            continue
        if "resolved" in details:
            resolved[sample] = details["resolved"]
    return resolved


def _values_differ(values: list[Any]) -> bool:
    """Compare JSON values, including values that are not hashable."""
    if len(values) < 2:
        return False
    first = values[0]
    return any(value != first for value in values[1:])


def collect_differences(
    submissions: list[Path],
    diff_num: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Return differing samples as ``sample -> submission.json -> resolved``.

    When ``diff_num`` is set, keep only samples where the smaller of the true
    and false groups has at least that many submissions.
    """
    results = {submission: load_resolved(submission) for submission in submissions}
    samples = sorted({sample for values in results.values() for sample in values})

    differences: dict[str, dict[str, Any]] = {}
    for sample in samples:
        # A missing result is represented as null so incomplete submissions are
        # visible rather than silently treated as equal.
        statuses = {
            f"{submission.name}.json": values.get(sample)
            for submission, values in results.items()
        }
        if _values_differ(list(statuses.values())):
            if diff_num is not None:
                true_count = sum(value is True for value in statuses.values())
                false_count = sum(value is False for value in statuses.values())
                if min(true_count, false_count) < diff_num:
                    continue
            differences[sample] = statuses
    return differences


def load_available_submissions(submissions: list[Path]) -> list[Path]:
    """Keep submissions with Harbor trajectories and readable result metadata."""
    available: list[Path] = []
    for submission in submissions:
        harbor_dir = submission / "trajs_harbor"
        if not harbor_dir.is_dir() or not any(harbor_dir.iterdir()):
            print(f"Warning: skipping {submission}: missing or empty trajs_harbor")
            continue
        try:
            load_resolved(submission)
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
            print(f"Warning: skipping {submission}: {exc}")
            continue
        available.append(submission)
    return available


def copy_differing_trajectories(
    submissions: list[Path],
    differences: dict[str, dict[str, Any]],
    output_dir: Path,
) -> list[Path]:
    """Copy Harbor trajectories for differing samples and return missing sources."""
    missing: list[Path] = []
    for sample in differences:
        sample_output = output_dir / sample
        sample_output.mkdir(parents=True, exist_ok=True)
        for submission in submissions:
            source = submission / "trajs_harbor" / sample / "trajectory.json"
            destination = sample_output / f"{submission.name}.json"
            if not source.is_file():
                missing.append(source)
                continue
            shutil.copy2(source, destination)
    return missing


def write_compare(output_dir: Path, differences: dict[str, dict[str, Any]]) -> Path:
    """Write the comparison index and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    compare_path = output_dir / "compare.json"
    compare_path.write_text(
        json.dumps(differences, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return compare_path


def clear_output(output_dir: Path) -> None:
    """Remove files and sample directories from a previous generated result."""
    if not output_dir.is_dir():
        return
    for child in output_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def main(
    year: int,
    dataset: str,
    evaluation_dir: Path,
    output_dir: Path,
    diff_num: int | None = None,
) -> int:
    submissions = find_submissions(evaluation_dir, dataset, year)
    submissions = load_available_submissions(submissions)
    clear_output(output_dir)
    differences = collect_differences(submissions, diff_num)
    missing = copy_differing_trajectories(submissions, differences, output_dir)
    compare_path = write_compare(output_dir, differences)

    for source in missing:
        print(f"Warning: trajectory not found: {source}")
    if len(submissions) < 2:
        print(f"Found {len(submissions)} submission(s); no comparison was possible")
    else:
        print(
            f"Compared {len(submissions)} submissions; found "
            f"{len(differences)} differing samples"
        )
    print(f"Wrote {compare_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--dataset", default="verified")
    parser.add_argument(
        "--diff_num",
        "--diff-num",
        type=int,
        default=None,
        help="Keep samples whose smaller true/false group has at least this size",
    )
    parser.add_argument(
        "--evaluation_dir",
        "--evaluation-dir",
        type=Path,
        default=DEFAULT_EVALUATION_DIR,
        help="Evaluation root (default: script-dir/experiments/evaluation)",
    )
    parser.add_argument(
        "--output_dir",
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: experiments/diff_trajectories/<dataset>)",
    )
    args = parser.parse_args()
    if not 1 <= args.year <= 9999:
        parser.error("--year must be between 1 and 9999")
    if args.diff_num is not None and args.diff_num < 1:
        parser.error("--diff_num must be a positive integer")
    if args.output_dir is None:
        args.output_dir = DEFAULT_OUTPUT_ROOT / args.dataset
    return args


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(
        main(args.year, args.dataset, args.evaluation_dir, args.output_dir, args.diff_num)
    )
