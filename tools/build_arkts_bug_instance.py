from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from arkts_syntax_tree import (
    DEFAULT_MUTATION_OPERATORS,
    create_bug_instance,
    find_bug_candidates,
)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find an impactful ArkTS function, apply one of six type-safe mutation "
            "operators, then create a repository instance and its repair patch."
        ),
    )
    parser.add_argument("repo", type=Path, help="HarmonyOS/ArkTS repository root")
    parser.add_argument(
        "--syntax-tree",
        type=Path,
        help="syntax tree JSONL; default: syntax_trees/<repo-name>_syntax_tree.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "instances" / "error_fix",
        help="instance parent directory; default: ./instances/error_fix",
    )
    parser.add_argument(
        "--min-out-degree",
        type=positive_int,
        default=3,
        help="minimum number of unique callees; default: 3",
    )
    parser.add_argument(
        "--min-consumers",
        type=positive_int,
        default=2,
        help="minimum number of cross-file downstream consumer functions; default: 2",
    )
    parser.add_argument("--instance-id", help="explicit instance directory name")
    parser.add_argument(
        "--mutation-operator",
        choices=DEFAULT_MUTATION_OPERATORS,
        help="restrict generation/listing to one mutation operator",
    )
    parser.add_argument(
        "--selection-seed",
        type=int,
        help="deterministic diversity seed; default derives from repository and commit",
    )
    parser.add_argument(
        "--list-candidates",
        action="store_true",
        help="print matching candidates as JSON without creating an instance",
    )
    args = parser.parse_args(argv)

    syntax_tree = args.syntax_tree or (
        PROJECT_ROOT / "syntax_trees" / f"{args.repo.name}_syntax_tree.jsonl"
    )

    try:
        if args.list_candidates:
            candidates = find_bug_candidates(
                args.repo,
                syntax_tree,
                min_out_degree=args.min_out_degree,
                min_downstream_consumers=args.min_consumers,
                mutation_operators=[args.mutation_operator] if args.mutation_operator else None,
            )
            print(json.dumps([candidate.to_dict() for candidate in candidates], ensure_ascii=False, indent=2))
            return 0

        instance = create_bug_instance(
            args.repo,
            syntax_tree,
            output_dir=args.output_dir,
            min_out_degree=args.min_out_degree,
            min_downstream_consumers=args.min_consumers,
            instance_id=args.instance_id,
            mutation_operator=args.mutation_operator,
            selection_seed=args.selection_seed,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    target = instance["target"]
    function = target["function"]
    print(
        f"instance={instance['instance_id']} target={function['qualified_name']} "
        f"operator={target['mutation']['operator_id']} out_degree={target['out_degree']} "
        f"callers={target['downstream_function_count']}"
    )
    instance_dir = args.output_dir / instance["instance_id"]
    print(f"syntax_tree={instance_dir / 'syntax_tree.jsonl'}")
    print(f"fix_patch={instance_dir / 'fix.patch'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
