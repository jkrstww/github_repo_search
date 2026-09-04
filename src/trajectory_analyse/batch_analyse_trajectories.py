#!/usr/bin/env python3
"""Analyze every sample directory below a trajectory-difference directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from analyse_trajectories import analyse


ROOT = Path(__file__).resolve().parent
DEFAULT_TRAJECTORIES_ROOT = ROOT / "experiments" / "diff_trajectories" / "verified"


def find_sample_directories(trajectories_root: Path) -> list[Path]:
    """Return immediate sample directories in deterministic order."""
    trajectories_root = trajectories_root.expanduser().resolve()
    if not trajectories_root.is_dir():
        raise ValueError(f"Trajectories root does not exist: {trajectories_root}")
    return sorted(path for path in trajectories_root.iterdir() if path.is_dir())


def batch_analyse(
    trajectories_root: Path,
    label_path: Path,
    *,
    codex_command: str = "codex",
    timeout: int = 1800,
) -> tuple[list[Path], list[tuple[Path, Exception]]]:
    """Analyze all samples, continuing after individual failures."""
    samples = find_sample_directories(trajectories_root)
    succeeded: list[Path] = []
    failed: list[tuple[Path, Exception]] = []
    for index, sample_dir in enumerate(samples, start=1):
        print(f"[{index}/{len(samples)}] Analyzing {sample_dir.name}")
        try:
            output = analyse(
                sample_dir,
                label_path,
                codex_command=codex_command,
                timeout=timeout,
            )
        except Exception as exc:  # keep the batch moving for one bad sample
            failed.append((sample_dir, exc))
            print(f"[{index}/{len(samples)}] Failed {sample_dir.name}: {exc}")
            continue
        succeeded.append(output)
        print(f"[{index}/{len(samples)}] Wrote {output}")
    return succeeded, failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trajectories_root",
        "--trajectories-root",
        type=Path,
        default=DEFAULT_TRAJECTORIES_ROOT,
        help="Root containing sample directories",
    )
    parser.add_argument(
        "--trajectories_lable",
        "--trajectories-label",
        "--trajectories_label",
        type=Path,
        default=None,
        help="compare.json (default: <trajectories_root>/compare.json)",
    )
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    root = args.trajectories_root.expanduser().resolve()
    if args.trajectories_lable is None:
        args.trajectories_lable = root / "compare.json"
    return args


def main(args: argparse.Namespace) -> int:
    try:
        succeeded, failed = batch_analyse(
            args.trajectories_root,
            args.trajectories_lable,
            codex_command=args.codex_command,
            timeout=args.timeout,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Completed: {len(succeeded)} succeeded, {len(failed)} failed")
    for sample_dir, exc in failed:
        print(f"  {sample_dir.name}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(parse_args()))
