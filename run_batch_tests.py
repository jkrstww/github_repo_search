"""Batch-run agent evaluations for every instance under ``instances/``.

By default, each instance's own ``run_tests.py`` is invoked with Codex and
``gpt-5.6-sol``. Runs continue after individual failures and a machine-readable
summary is written to ``instance_tracks/batch_summary.json``.

Examples::

    python run_batch_tests.py
    python run_batch_tests.py --jobs 3
    python run_batch_tests.py --match "HarmonyPractice_*build*"
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_AGENT = "codex"
DEFAULT_MODEL = "gpt-5.6-sol"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


@dataclass(frozen=True)
class Instance:
    instance_id: str
    directory: Path
    runner: Path


@dataclass
class InstanceResult:
    instance_id: str
    instance_dir: str
    status: str
    return_code: int
    duration_seconds: float
    track_path: str | None
    reward: float | None = None
    agent_exit_code: int | None = None
    test_exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


def _matches(instance_id: str, relative_dir: str, patterns: Iterable[str]) -> bool:
    patterns = tuple(patterns)
    return not patterns or any(
        fnmatch.fnmatchcase(instance_id, pattern)
        or fnmatch.fnmatchcase(relative_dir, pattern)
        for pattern in patterns
    )


def discover_instances(
    instances_dir: Path, patterns: Iterable[str] = ()
) -> list[Instance]:
    """Find and validate runnable instance directories recursively."""
    if not instances_dir.is_dir():
        raise ValueError(f"instances directory does not exist: {instances_dir}")

    discovered: list[Instance] = []
    seen_ids: dict[str, Path] = {}
    metadata_paths = sorted(
        instances_dir.rglob("instance.json"), key=lambda path: path.as_posix()
    )
    for metadata_path in metadata_paths:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid instance metadata: {metadata_path}: {exc}") from exc
        instance_id = metadata.get("instance_id") if isinstance(metadata, dict) else None
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ValueError(f"missing instance_id in {metadata_path}")
        if Path(instance_id).name != instance_id or instance_id in {".", ".."}:
            raise ValueError(f"instance_id must be a plain file name: {instance_id!r}")
        if instance_id in seen_ids:
            raise ValueError(
                f"duplicate instance_id {instance_id!r}: "
                f"{seen_ids[instance_id]} and {metadata_path}"
            )
        seen_ids[instance_id] = metadata_path

        instance_dir = metadata_path.parent
        relative_dir = instance_dir.relative_to(instances_dir).as_posix()
        if not _matches(instance_id, relative_dir, patterns):
            continue
        runner = instance_dir / "run_tests.py"
        if not runner.is_file():
            raise ValueError(f"missing run_tests.py for instance {instance_id}: {runner}")
        discovered.append(Instance(instance_id, instance_dir, runner))
    return discovered


def build_instance_command(
    instance: Instance,
    *,
    agent: str,
    model: str,
    workspace_root: Path,
    output_dir: Path,
    extra_args: Iterable[str] = (),
) -> list[str]:
    command = [
        sys.executable,
        str(instance.runner),
        "--agent",
        agent,
        "--model",
        model,
        "--instance-dir",
        str(instance.directory),
        "--workspace-root",
        str(workspace_root),
        "--output-dir",
        str(output_dir),
    ]
    # Pin a writable sandbox so benchmark behavior does not depend on the
    # evaluator's personal Codex configuration.
    if Path(agent.split()[0]).name.lower() in {
        "codex",
        "codex.cmd",
        "codex.exe",
        "codex.ps1",
    }:
        command.extend(["--arg=--sandbox", "--arg=workspace-write"])
    command.extend(f"--arg={value}" for value in extra_args)
    return command


def _read_track_fields(track_path: Path) -> dict[str, Any]:
    if not track_path.is_file():
        return {}
    try:
        document = json.loads(track_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def run_instance(
    instance: Instance,
    *,
    agent: str,
    model: str,
    workspace_root: Path,
    output_dir: Path,
    extra_args: Iterable[str] = (),
) -> InstanceResult:
    command = build_instance_command(
        instance,
        agent=agent,
        model=model,
        workspace_root=workspace_root,
        output_dir=output_dir,
        extra_args=extra_args,
    )
    print(f"[{instance.instance_id}] starting", flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except OSError as exc:
        return_code = 127
        stdout = ""
        stderr = str(exc)

    duration = round(time.monotonic() - started, 3)
    track_path = output_dir / f"{instance.instance_id}.json"
    track = _read_track_fields(track_path)
    reward_value = track.get("reward")
    reward = float(reward_value) if isinstance(reward_value, (int, float)) else None
    status = "passed" if return_code == 0 and reward == 1.0 else "failed"
    print(
        f"[{instance.instance_id}] {status} "
        f"(exit={return_code}, reward={reward}, {duration:.3f}s)",
        flush=True,
    )
    return InstanceResult(
        instance_id=instance.instance_id,
        instance_dir=str(instance.directory),
        status=status,
        return_code=return_code,
        duration_seconds=duration,
        track_path=str(track_path) if track_path.is_file() else None,
        reward=reward,
        agent_exit_code=track.get("agent_exit_code"),
        test_exit_code=track.get("test_exit_code"),
        stdout=stdout.strip(),
        stderr=stderr.strip(),
    )


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch-evaluate instances with an agent CLI"
    )
    parser.add_argument(
        "--instances-dir", type=Path, default=PROJECT_ROOT / "instances"
    )
    parser.add_argument(
        "--workspace-root", type=Path, default=PROJECT_ROOT / "workspace"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "instance_tracks"
    )
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--jobs", type=int, default=1, help="number of instances to run concurrently"
    )
    parser.add_argument(
        "--match",
        action="append",
        default=[],
        help="glob matched against instance id or relative directory; repeatable",
    )
    parser.add_argument(
        "--arg",
        dest="extra_args",
        action="append",
        default=[],
        help="additional agent CLI argument; repeatable",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print commands without running them"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.jobs < 1:
        print("error: --jobs must be at least 1", file=sys.stderr)
        return 2

    instances_dir = args.instances_dir.resolve()
    workspace_root = args.workspace_root.resolve()
    output_dir = args.output_dir.resolve()
    try:
        instances = discover_instances(instances_dir, args.match)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not instances:
        print("error: no matching instances found", file=sys.stderr)
        return 2

    if args.dry_run:
        for instance in instances:
            command = build_instance_command(
                instance,
                agent=args.agent,
                model=args.model,
                workspace_root=workspace_root,
                output_dir=output_dir,
                extra_args=args.extra_args,
            )
            print(subprocess.list2cmdline(command))
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    started = time.monotonic()
    common = {
        "agent": args.agent,
        "model": args.model,
        "workspace_root": workspace_root,
        "output_dir": output_dir,
        "extra_args": args.extra_args,
    }
    if args.jobs == 1:
        results = [run_instance(instance, **common) for instance in instances]
    else:
        indexed_results: dict[int, InstanceResult] = {}
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {
                executor.submit(run_instance, instance, **common): index
                for index, instance in enumerate(instances)
            }
            for future in as_completed(futures):
                indexed_results[futures[future]] = future.result()
        results = [indexed_results[index] for index in range(len(instances))]

    passed = sum(result.status == "passed" for result in results)
    failed = len(results) - passed
    summary = {
        "schema_version": 1,
        "agent": args.agent,
        "model": args.model,
        "instances_dir": str(instances_dir),
        "workspace_root": str(workspace_root),
        "output_dir": str(output_dir),
        "jobs": args.jobs,
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": [asdict(result) for result in results],
    }
    summary_path = output_dir / "batch_summary.json"
    _write_summary(summary_path, summary)
    print(f"summary: {summary_path}")
    print(f"total={len(results)} passed={passed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
