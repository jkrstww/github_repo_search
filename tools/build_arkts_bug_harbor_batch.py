"""Batch-build ArkTS Harbor instances for repository records in a JSONL file.

用途：根据仓库 JSONL 清单，批量调用 ArkTS Harbor instance 构造器。
每个仓库默认生成两个函数修复任务；已有该仓库 instance 的记录会自动跳过，
单个仓库失败也不会中断后续处理，因此可以重复运行脚本以继续未完成的批次。

使用说明：
    python tools/build_arkts_bug_harbor_batch.py
    python tools/build_arkts_bug_harbor_batch.py --max-repos 20 --model gpt-5.6-sol
    python tools/build_arkts_bug_harbor_batch.py <input.jsonl> --output-dir harbor_instances

常用参数：--max-repos 限制处理数量，--skip-codex 跳过 Codex 测试生成，
--output-dir 指定 instance 输出目录，--checkout-root 指定临时仓库目录。

"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / (
    "data/repositories_harmony_stars_gt10_language_typescript_harmonyos_arkts_"
    "PR_merged_gpt_filter.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "harbor_instances"
DEFAULT_CHECKOUT_ROOT = PROJECT_ROOT / ".tmp" / "arkts-harbor-checkouts-batch"
GENERATOR = Path(__file__).with_name("build_arkts_bug_harbor_instance.py")


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record must be an object at {path}:{line_number}")
            records.append(record)
    return records


def _repository_name(full_name: str) -> str:
    return full_name.rsplit("/", 1)[-1]


def _has_output(output_dir: Path, full_name: str) -> bool:
    """Return whether *output_dir* already contains an instance for full_name."""
    if not output_dir.is_dir():
        return False

    repo_name = _repository_name(full_name)
    default_prefix = f"{repo_name}_arkts_masked_functions"
    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        metadata_path = child / "instance.json"
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                metadata = None
            if isinstance(metadata, dict) and metadata.get("repo") == full_name:
                return True
        # Also recognize the normal builder directory name when an instance
        # predates metadata inspection or has incomplete metadata.
        if child.name == repo_name or child.name.startswith(default_prefix + "_"):
            return True
    return False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build ArkTS Harbor instances for the first repository records in JSONL."
    )
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--checkout-root", type=Path, default=DEFAULT_CHECKOUT_ROOT)
    parser.add_argument("--max-repos", type=int, default=20, help="number of input records to process")
    parser.add_argument("--codex-cli", default="codex")
    parser.add_argument("--model")
    parser.add_argument(
        "--codex-sandbox",
        choices=["read-only", "workspace-write", "danger-full-access"],
        default="workspace-write",
    )
    parser.add_argument("--skip-codex", action="store_true")
    parser.add_argument("--min-out-degree", type=int, default=1)
    parser.add_argument("--min-consumers", type=int, default=0)
    parser.add_argument("--min-downstream-dependencies", type=int, default=1)
    parser.add_argument("--mutation-operator")
    parser.add_argument("--keep-checkout", action="store_true")
    return parser


def _command(args: argparse.Namespace, full_name: str) -> list[str]:
    command = [
        sys.executable,
        str(GENERATOR),
        "--repo",
        full_name,
        "--output-dir",
        str(args.output_dir.resolve()),
        "--checkout-root",
        str(args.checkout_root.resolve()),
        "--codex-cli",
        args.codex_cli,
        "--codex-sandbox",
        args.codex_sandbox,
        "--min-out-degree",
        str(args.min_out_degree),
        "--min-consumers",
        str(args.min_consumers),
        "--min-downstream-dependencies",
        str(args.min_downstream_dependencies),
    ]
    if args.model:
        command.extend(["--model", args.model])
    if args.mutation_operator:
        command.extend(["--mutation-operator", args.mutation_operator])
    if args.skip_codex:
        command.append("--skip-codex")
    if args.keep_checkout:
        command.append("--keep-checkout")
    return command


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.max_repos < 1:
        print("error: --max-repos must be positive", file=sys.stderr)
        return 1
    if args.min_out_degree < 1 or args.min_downstream_dependencies < 1:
        print(
            "error: --min-out-degree and --min-downstream-dependencies must be positive",
            file=sys.stderr,
        )
        return 1
    if args.min_consumers < 0:
        print("error: --min-consumers must not be negative", file=sys.stderr)
        return 1
    if not args.input.is_file():
        print(f"error: input JSONL does not exist: {args.input}", file=sys.stderr)
        return 1

    try:
        records = _load_records(args.input)[: args.max_repos]
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.output_dir.resolve().mkdir(parents=True, exist_ok=True)
    args.checkout_root.resolve().mkdir(parents=True, exist_ok=True)
    attempted = built = skipped = errors = 0
    for record in records:
        full_name = str(record.get("full_name") or "").strip()
        if not full_name or "/" not in full_name:
            errors += 1
            print(f"error: record has invalid full_name: {record!r}", file=sys.stderr)
            continue
        if _has_output(args.output_dir.resolve(), full_name):
            skipped += 1
            print(f"skipped {full_name}: output already exists", file=sys.stderr)
            continue

        attempted += 1
        print(f"building {attempted}: {full_name}", file=sys.stderr)
        result = subprocess.run(
            _command(args, full_name),
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.stdout:
            print(result.stdout.rstrip())
        if result.returncode:
            errors += 1
            print(
                f"error: {full_name} failed with exit code {result.returncode}",
                file=sys.stderr,
            )
            if result.stderr:
                print(result.stderr.rstrip(), file=sys.stderr)
        else:
            built += 1

    print(
        f"repositories={len(records)} built={built} skipped={skipped} errors={errors} "
        f"output={args.output_dir.resolve()}"
    )
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
