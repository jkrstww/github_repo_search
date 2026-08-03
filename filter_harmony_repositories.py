from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from github_repo_filter.github import GitHubApiError
from github_repo_filter.harmony_filter import (
    CONFIDENCE_LEVELS,
    classify_harmony_repository,
    fetch_repository_tree,
)
from github_repo_filter.jsonl import load_jsonl, overwrite_jsonl
from github_repo_filter.post_filter import suffixed_jsonl_path


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env) or _token_from_env_file(args.env_file, args.token_env)
    if not token:
        print(
            f"error: GitHub token not found in {args.token_env} or {args.env_file}",
            file=sys.stderr,
        )
        return 1

    try:
        records = load_jsonl(args.input)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output = args.output or suffixed_jsonl_path(args.input, "harmonyos_arkts")
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    errors: list[tuple[str, str]] = []

    def inspect(record: dict[str, Any]) -> tuple[dict[str, Any], bool, str | None]:
        full_name = str(record.get("full_name") or "<unknown>")
        try:
            paths, tree_truncated = fetch_repository_tree(
                record,
                token=token,
                timeout=args.timeout,
            )
            evidence = classify_harmony_repository(
                record,
                paths,
                tree_truncated=tree_truncated,
                min_confidence=args.min_confidence,
            )
            enriched = {**record, "harmony_project": evidence.to_dict()}
            return enriched, evidence.accepted, None
        except (GitHubApiError, OSError, ValueError) as exc:
            enriched = {
                **record,
                "harmony_project": {
                    "confidence": "unknown",
                    "accepted": False,
                    "error": str(exc),
                },
            }
            return enriched, False, f"{full_name}: {exc}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        for index, (record, is_accepted, error) in enumerate(executor.map(inspect, records), start=1):
            if is_accepted:
                accepted.append(record)
            else:
                rejected.append(record)
            if error:
                errors.append((str(record.get("full_name") or "<unknown>"), error))
            if not args.no_progress and (index == len(records) or index % 25 == 0):
                print(
                    f"processed {index}/{len(records)}; accepted {len(accepted)}; errors {len(errors)}",
                    file=sys.stderr,
                )

    overwrite_jsonl(output, accepted)
    if args.rejected_output:
        overwrite_jsonl(args.rejected_output, rejected)

    confidence_counts = {
        level: sum(record["harmony_project"].get("confidence") == level for record in accepted)
        for level in reversed(CONFIDENCE_LEVELS)
    }
    print(f"HarmonyOS/ArkTS structure filter: {len(records)} -> {len(accepted)} saved {output}")
    print(
        "accepted confidence: "
        + ", ".join(f"{level}={count}" for level, count in confidence_counts.items() if count)
    )
    if args.rejected_output:
        print(f"rejected: {len(rejected)} saved {args.rejected_output}")
    if errors:
        print(f"warning: {len(errors)} repositories could not be inspected", file=sys.stderr)
        for _, error in errors[: args.max_error_messages]:
            print(f"warning: {error}", file=sys.stderr)
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Filter repository JSONL by HarmonyOS/ArkTS source and build structure.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=Path("data/repositories_harmony_stars_gt10_language_typescript.jsonl"),
        type=Path,
        help="input repository JSONL",
    )
    parser.add_argument("--output", type=Path, help="accepted repository JSONL path")
    parser.add_argument("--rejected-output", type=Path, help="optional rejected repository JSONL path")
    parser.add_argument(
        "--min-confidence",
        choices=tuple(CONFIDENCE_LEVELS),
        default="medium",
        help="minimum accepted confidence, default: medium",
    )
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="environment variable containing a GitHub token")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="fallback dotenv file")
    parser.add_argument("--workers", type=int, default=8, help="parallel GitHub requests, default: 8")
    parser.add_argument("--timeout", type=int, default=60, help="GitHub request timeout in seconds")
    parser.add_argument("--no-progress", action="store_true", help="disable progress output")
    parser.add_argument("--max-error-messages", type=int, default=10, help="maximum API errors to print")
    return parser


def _token_from_env_file(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip("\"'") or None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
