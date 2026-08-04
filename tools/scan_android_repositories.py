from __future__ import annotations

import argparse
import concurrent.futures
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from arkts_syntax_tree import scan_repository_android_calls
from github_repo_filter.jsonl import load_jsonl, overwrite_jsonl, write_jsonl


DEFAULT_INPUT = Path(
    "data/repositories_harmony_stars_gt10_language_typescript_PR_merged.jsonl"
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.workers < 1 or args.clone_timeout < 1:
        parser.error("--workers and --clone-timeout must be positive")
    if args.max_repos is not None and args.max_repos < 1:
        parser.error("--max-repos must be positive")

    output = args.output or args.input.with_name(f"{args.input.stem}_android_calls.jsonl")
    if not args.input.is_file():
        print(f"error: input JSONL does not exist: {args.input}", file=sys.stderr)
        return 1
    if output.resolve() == args.input.resolve():
        print("error: output must differ from input", file=sys.stderr)
        return 1
    try:
        records = load_jsonl(args.input)
        if args.max_repos is not None:
            records = records[: args.max_repos]
        existing = [] if args.overwrite else load_jsonl(output)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.overwrite:
        overwrite_jsonl(output, [])

    completed_names = {
        str(record.get("full_name") or "")
        for record in existing
        if record.get("status") == "ok"
    }
    pending = [
        record for record in records if str(record.get("full_name") or "") not in completed_names
    ]
    skipped = len(records) - len(pending)

    def scan(record: dict[str, Any]) -> dict[str, Any]:
        return scan_repository_android_calls(
            record,
            clone_root=args.clone_root,
            clone_timeout=args.clone_timeout,
        )

    processed = 0
    errors = 0
    matches = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(scan, record): record for record in pending}
        for future in concurrent.futures.as_completed(futures):
            source_record = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # Keep the batch resumable after an unexpected worker failure.
                result = {
                    "full_name": source_record.get("full_name"),
                    "html_url": source_record.get("html_url"),
                    "status": "error",
                    "error": {"stage": "worker", "message": str(exc)},
                }
            write_jsonl(output, [result], dedupe=True)
            processed += 1
            if result.get("status") == "error":
                errors += 1
            if int(result.get("android_call_count") or 0) > 0:
                matches += 1
            if not args.no_progress:
                print(
                    f"processed {processed}/{len(pending)}; skipped {skipped}; "
                    f"matches {matches}; errors {errors}; {result.get('full_name', '<unknown>')}",
                    file=sys.stderr,
                )

    final_records = load_jsonl(output)
    selected_names = {str(record.get("full_name") or "") for record in records}
    selected_results = [
        record for record in final_records if str(record.get("full_name") or "") in selected_names
    ]
    total_matches = sum(int(record.get("android_call_count") or 0) for record in selected_results)
    repositories_with_matches = sum(
        int(record.get("android_call_count") or 0) > 0 for record in selected_results
    )
    total_errors = sum(record.get("status") == "error" for record in selected_results)
    print(
        f"repositories={len(records)} scanned={len(selected_results) - total_errors} "
        f"errors={total_errors} repositories_with_android_calls={repositories_with_matches} "
        f"android_calls={total_matches} output={output}"
    )
    return 2 if total_errors else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clone repository candidates, parse ArkTS/TS, and detect android.* calls.",
    )
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, help="result JSONL path")
    parser.add_argument(
        "--clone-root",
        type=Path,
        help="parent for temporary clones; default: system temporary directory",
    )
    parser.add_argument("--workers", type=int, default=4, help="parallel clones, default: 4")
    parser.add_argument(
        "--clone-timeout",
        type=int,
        default=300,
        help="timeout per git clone in seconds, default: 300",
    )
    parser.add_argument("--max-repos", type=int, help="only scan the first N input records")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="discard existing output; otherwise successful records are skipped and errors retried",
    )
    parser.add_argument("--no-progress", action="store_true", help="disable per-repository progress")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
