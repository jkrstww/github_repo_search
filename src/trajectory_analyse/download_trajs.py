#!/usr/bin/env python3
"""Batch-download trajectory artifacts for submissions from a given year.

Run this script from the project directory or any working directory, for example::

    python download_trajs.py
    python download_trajs.py --year 2025 --evaluation-dir experiments/evaluation

Submission directories are identified by a leading four-digit year in their
name (for example, ``20260902_mini-v2.4.6_gemini-3-5-flash``).  A submission
is skipped as soon as its target directory already contains a ``trajs/``
directory, including an empty one.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_EVALUATION_DIR = Path(__file__).resolve().parent / "experiments" / "evaluation"


def find_submissions(evaluation_dir: Path, year: int) -> list[tuple[Path, str]]:
    """Return ``(submission_dir, download_logs_path)`` pairs for *year*.

    Only directories immediately below a split directory are considered.  A
    split is any directory directly under ``evaluation_dir`` (for example,
    ``verified`` or ``multilingual``).
    """
    year_prefix = f"{year:04d}"
    submissions: list[tuple[Path, str]] = []
    if not evaluation_dir.is_dir():
        raise ValueError(f"Evaluation directory does not exist: {evaluation_dir}")

    for split_dir in sorted(path for path in evaluation_dir.iterdir() if path.is_dir()):
        for submission_dir in sorted(path for path in split_dir.iterdir() if path.is_dir()):
            if submission_dir.name.startswith(year_prefix):
                relative_path = submission_dir.relative_to(evaluation_dir.parent)
                submissions.append((submission_dir, relative_path.as_posix()))
    return submissions


def download_submission(submission_dir: Path, download_logs_path: str, *, experiments_dir: Path) -> int:
    """Download trajectories for one submission unless ``trajs/`` exists."""
    trajs_dir = submission_dir / "trajs"
    if trajs_dir.is_dir():
        print(f"Skipping {submission_dir} (trajs/ already exists)")
        return 0

    command = [
        sys.executable,
        "-m",
        "analysis.download_logs",
        download_logs_path,
        "--only_trajs",
    ]
    print(f"Downloading trajectories for {download_logs_path}")
    result = subprocess.run(command, cwd=experiments_dir)
    if result.returncode:
        print(
            f"Failed to download {download_logs_path} (exit code {result.returncode})",
            file=sys.stderr,
        )
    return result.returncode


def main(year: int, evaluation_dir: Path) -> int:
    experiments_dir = evaluation_dir.parent
    submissions = find_submissions(evaluation_dir, year)
    if not submissions:
        print(f"No submissions found for year {year} under {evaluation_dir}")
        return 0

    failures = 0
    for submission_dir, download_logs_path in submissions:
        failures += download_submission(
            submission_dir, download_logs_path, experiments_dir=experiments_dir
        ) != 0
    return 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--year",
        type=int,
        default=2026,
        help="Year prefix of submission directories (default: 2026)",
    )
    parser.add_argument(
        "--evaluation-dir",
        type=Path,
        default=DEFAULT_EVALUATION_DIR,
        help="Root containing split directories (default: script-dir/experiments/evaluation)",
    )
    args = parser.parse_args()
    if not 1 <= args.year <= 9999:
        parser.error("--year must be between 1 and 9999")
    return args


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(main(args.year, args.evaluation_dir))
