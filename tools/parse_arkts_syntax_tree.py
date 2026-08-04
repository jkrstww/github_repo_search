from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from arkts_syntax_tree import parse_repository, write_syntax_tree_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse a HarmonyOS ArkTS project into a lightweight syntax tree JSONL.",
    )
    parser.add_argument("repo", type=Path, help="HarmonyOS/ArkTS project root to parse")
    parser.add_argument(
        "--output",
        type=Path,
        help="syntax tree JSONL path; default: syntax_trees/<repo-name>_syntax_tree.jsonl",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="summary JSON path; default: syntax_trees/<repo-name>_syntax_tree_summary.json",
    )
    parser.add_argument(
        "--extension",
        action="append",
        help="source extension to parse; can be repeated; default: .ets and .ts",
    )
    parser.add_argument("--pretty", action="store_true", help="write pretty-printed JSONL records")
    args = parser.parse_args(argv)

    repo = args.repo
    default_output_dir = PROJECT_ROOT / "syntax_trees"
    output = args.output or default_output_dir / f"{repo.name}_syntax_tree.jsonl"
    summary = args.summary or default_output_dir / f"{repo.name}_syntax_tree_summary.json"

    try:
        parsed_files = parse_repository(repo, extensions=args.extension or [".ets", ".ts"])
        result = write_syntax_tree_outputs(
            parsed_files,
            output_path=output,
            summary_path=summary,
            pretty=args.pretty,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"parsed files={result['files']} nodes={result['nodes']} imports={result['imports']} "
        f"calls={result['calls']} max_depth={result['max_depth']} output={output} summary={summary}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
