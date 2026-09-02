"""Run an OpenCode task and persist its native execution trajectory.

OpenCode already exposes the data needed for trajectory tracking:

* ``opencode run --format json`` streams raw execution events as JSONL.
* ``opencode export <sessionID>`` exports the complete persisted session.

This wrapper keeps both native formats and adds a small metadata document with
the resolved model, command, timestamps, paths, and exit status.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
SESSION_ID_KEYS = {"sessionid", "session_id", "session"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _normalized_model_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def resolve_model(
    executable: str,
    requested: str,
    *,
    cwd: Path,
) -> str:
    """Resolve a display name such as ``Kimi K3`` to a provider/model id."""
    requested = requested.strip()
    if not requested:
        raise ValueError("model must not be empty")
    if "/" in requested:
        return requested

    result = subprocess.run(
        [executable, "models"],
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"cannot list OpenCode models: {detail or 'unknown error'}")

    models = [line.strip() for line in result.stdout.splitlines() if "/" in line]
    normalized = _normalized_model_name(requested)
    exact = [
        model
        for model in models
        if normalized
        in {
            _normalized_model_name(model),
            _normalized_model_name(model.rsplit("/", 1)[-1]),
        }
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(
            f"model name {requested!r} is ambiguous: " + ", ".join(exact)
        )

    available = ", ".join(models[:10])
    suffix = " ..." if len(models) > 10 else ""
    raise ValueError(
        f"unknown OpenCode model {requested!r}; pass provider/model or one of: "
        f"{available}{suffix}"
    )


def _walk_session_ids(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = key.lower().replace("-", "_")
            if (
                normalized_key in SESSION_ID_KEYS
                and isinstance(child, str)
                and child.startswith("ses_")
            ):
                yield child
            yield from _walk_session_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_session_ids(child)


def session_id_from_event(event: Any) -> str | None:
    return next(iter(_walk_session_ids(event)), None)


def _parse_json_line(line: str) -> Any | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"opencode-{timestamp}-{uuid.uuid4().hex[:8]}"


def build_run_command(
    executable: str,
    *,
    workspace: Path,
    instruction: str,
    model: str,
) -> list[str]:
    return [
        executable,
        "run",
        "--format",
        "json",
        "--model",
        model,
        "--dir",
        str(workspace),
        instruction,
    ]


def _export_session(
    executable: str,
    session_id: str,
    *,
    cwd: Path,
    destination: Path,
) -> str | None:
    result = subprocess.run(
        [executable, "export", session_id],
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip() or "OpenCode export failed"
    destination.write_text(result.stdout, encoding="utf-8")
    return None


def track_trajectory(
    *,
    workspace: Path,
    instruction: str,
    model: str,
    output_dir: Path,
    executable: str = "opencode",
) -> tuple[Path, int]:
    workspace = workspace.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    if not instruction.strip():
        raise ValueError("instruction must not be empty")

    resolved_executable = shutil.which(executable)
    if resolved_executable is None:
        raise ValueError(f"OpenCode executable not found: {executable}")
    resolved_model = resolve_model(resolved_executable, model, cwd=workspace)

    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id()
    metadata_path = output_dir / f"{run_id}.metadata.json"
    events_path = output_dir / f"{run_id}.events.jsonl"
    stderr_path = output_dir / f"{run_id}.stderr.log"
    session_path = output_dir / f"{run_id}.session.json"
    command = build_run_command(
        resolved_executable,
        workspace=workspace,
        instruction=instruction,
        model=resolved_model,
    )
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "started_at": utc_now(),
        "finished_at": None,
        "workspace": str(workspace),
        "instruction": instruction,
        "requested_model": model,
        "resolved_model": resolved_model,
        "command": command,
        "session_id": None,
        "exit_code": None,
        "artifacts": {
            "events": events_path.name,
            "stderr": stderr_path.name,
            "session": None,
        },
    }
    _atomic_write_json(metadata_path, metadata)

    try:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        metadata.update(
            status="failed",
            finished_at=utc_now(),
            exit_code=127,
            error=f"failed to start OpenCode: {exc}",
        )
        _atomic_write_json(metadata_path, metadata)
        return metadata_path, 127

    lines: queue.Queue[tuple[str, str]] = queue.Queue()

    def collect(stream_name: str, stream: Any) -> None:
        for line in iter(stream.readline, ""):
            lines.put((stream_name, line))
        stream.close()

    threads = [
        threading.Thread(
            target=collect, args=("stdout", process.stdout), daemon=True
        ),
        threading.Thread(
            target=collect, args=("stderr", process.stderr), daemon=True
        ),
    ]
    for thread in threads:
        thread.start()

    session_id: str | None = None
    event_count = 0
    with events_path.open("w", encoding="utf-8", newline="") as events_file, (
        stderr_path.open("w", encoding="utf-8", newline="")
    ) as stderr_file:
        while any(thread.is_alive() for thread in threads) or not lines.empty():
            try:
                stream_name, line = lines.get(timeout=0.05)
            except queue.Empty:
                continue
            if stream_name == "stdout":
                events_file.write(line)
                events_file.flush()
                event_count += 1
                parsed = _parse_json_line(line)
                if parsed is not None:
                    session_id = session_id or session_id_from_event(parsed)
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                stderr_file.write(line)
                stderr_file.flush()
                sys.stderr.write(line)
                sys.stderr.flush()

    for thread in threads:
        thread.join()
    exit_code = process.wait()

    metadata.update(
        status="completed" if exit_code == 0 else "failed",
        finished_at=utc_now(),
        exit_code=exit_code,
        session_id=session_id,
        event_count=event_count,
    )
    if session_id is not None:
        export_error = _export_session(
            resolved_executable,
            session_id,
            cwd=workspace,
            destination=session_path,
        )
        if export_error is None:
            metadata["artifacts"]["session"] = session_path.name
        else:
            metadata["session_export_error"] = export_error
    else:
        metadata["session_export_error"] = "no session id found in OpenCode events"
    _atomic_write_json(metadata_path, metadata)
    return metadata_path, exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OpenCode and save its native JSON trajectory"
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output_dir", "--output-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metadata_path, exit_code = track_trajectory(
            workspace=args.workspace,
            instruction=args.instruction,
            model=args.model,
            output_dir=args.output_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"trajectory={metadata_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
