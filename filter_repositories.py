from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from github_repo_filter.post_filter import filter_steps_for_pipeline, run_filter_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply the default post-filters to a repository JSONL file.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="data/repositories.jsonl",
        type=Path,
        help="input JSONL path, default: data/repositories.jsonl",
    )
    parser.add_argument(
        "--pipeline",
        choices=["default", "harmony"],
        default="default",
        help="filter pipeline to run; use harmony for stars > 10 then language == TypeScript",
    )
    args = parser.parse_args(argv)

    try:
        summaries = run_filter_pipeline(args.input, steps=filter_steps_for_pipeline(args.pipeline))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for summary in summaries:
        print(
            f"{summary.step.name}: {summary.input_count} -> {summary.output_count} "
            f"saved {summary.output_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
