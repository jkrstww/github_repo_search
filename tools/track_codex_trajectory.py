"""Run a Codex task and persist its native execution trajectory.

Codex exposes a JSONL event stream through ``codex exec --json`` and stores
the complete native session under ``~/.codex/sessions``.  This wrapper keeps
both formats and writes metadata in the same layout as the OpenCode tracker.
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
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
_THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


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


def _parse_json_line(line: str) -> Any | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def thread_id_from_event(event: Any) -> str | None:
    """Return the thread id emitted by the native ``thread.started`` event."""
    if not isinstance(event, dict) or event.get("type") != "thread.started":
        return None
    thread_id = event.get("thread_id")
    if isinstance(thread_id, str) and _THREAD_ID_PATTERN.fullmatch(thread_id):
        return thread_id
    return None


def find_session_path(thread_id: str, *, sessions_root: Path) -> Path | None:
    """Find Codex's persisted JSONL file for a thread without guessing dates."""
    if not _THREAD_ID_PATTERN.fullmatch(thread_id) or not sessions_root.is_dir():
        return None
    matches = sorted(sessions_root.rglob(f"*{thread_id}.jsonl"))
    return matches[-1] if matches else None


def _native_session_is_complete(path: Path, thread_id: str) -> bool:
    """Check that a native session belongs to the thread and reached completion."""
    has_matching_session = False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        event = _parse_json_line(line)
        if not isinstance(event, dict):
            continue
        if event.get("type") == "session_meta":
            payload = event.get("payload")
            has_matching_session = isinstance(payload, dict) and payload.get("id") == thread_id
        if event.get("type") == "event_msg":
            payload = event.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "task_complete":
                return has_matching_session
    return False


def build_run_command(
    executable: str,
    *,
    workspace: Path,
    instruction: str,
    model: str,
) -> list[str]:
    return [
        executable,
        "--ask-for-approval",
        "never",
        "exec",
        "--json",
        "--model",
        model,
        "--cd",
        str(workspace),
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        instruction,
    ]


def _run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"codex-{timestamp}-{uuid.uuid4().hex[:8]}"


def _copy_native_session(
    thread_id: str,
    *,
    sessions_root: Path,
    destination: Path,
) -> str | None:
    # Codex writes the native session asynchronously.  Do not copy a file just
    # because it exists: wait for its terminal task_complete event first.
    for _ in range(100):
        source = find_session_path(thread_id, sessions_root=sessions_root)
        if source is not None and _native_session_is_complete(source, thread_id):
            shutil.copyfile(source, destination)
            return None
        time.sleep(0.1)
    return f"native Codex session did not complete for thread {thread_id}"


def track_trajectory(
    *,
    workspace: Path,
    instruction: str,
    model: str,
    output_dir: Path,
    executable: str = "codex",
    sessions_root: Path | None = None,
) -> tuple[Path, int]:
    workspace = workspace.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    if not instruction.strip():
        raise ValueError("instruction must not be empty")
    if not model.strip():
        raise ValueError("model must not be empty")

    resolved_executable = shutil.which(executable)
    if resolved_executable is None:
        raise ValueError(f"Codex executable not found: {executable}")
    if sessions_root is None:
        codex_home = os.environ.get("CODEX_HOME")
        sessions_root = (
            Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
        ) / "sessions"
    sessions_root = sessions_root.expanduser()

    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id()
    metadata_path = output_dir / f"{run_id}.metadata.json"
    events_path = output_dir / f"{run_id}.events.jsonl"
    stderr_path = output_dir / f"{run_id}.stderr.log"
    # Keep the artifact suffix consistent with the OpenCode tracker; the
    # content itself is Codex's native JSONL session transcript.
    session_path = output_dir / f"{run_id}.session.json"
    command = build_run_command(
        resolved_executable,
        workspace=workspace,
        instruction=instruction,
        model=model,
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
        "resolved_model": model,
        "command": command,
        "thread_id": None,
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
            error=f"failed to start Codex: {exc}",
        )
        _atomic_write_json(metadata_path, metadata)
        return metadata_path, 127

    lines: queue.Queue[tuple[str, str]] = queue.Queue()

    def collect(stream_name: str, stream: Any) -> None:
        for line in iter(stream.readline, ""):
            lines.put((stream_name, line))
        stream.close()

    threads = [
        threading.Thread(target=collect, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=collect, args=("stderr", process.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    thread_id: str | None = None
    event_count = 0
    with events_path.open("w", encoding="utf-8", newline="") as events_file, \
        stderr_path.open("w", encoding="utf-8", newline="") as stderr_file:
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
                    thread_id = thread_id or thread_id_from_event(parsed)
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
        thread_id=thread_id,
        session_id=thread_id,
        event_count=event_count,
    )
    if thread_id is not None:
        copy_error = _copy_native_session(
            thread_id,
            sessions_root=sessions_root,
            destination=session_path,
        )
        if copy_error is None:
            metadata["artifacts"]["session"] = session_path.name
            metadata["session_complete"] = True
        else:
            metadata["session_complete"] = False
            metadata["session_export_error"] = copy_error
    else:
        metadata["session_complete"] = False
        metadata["session_export_error"] = "no thread id found in Codex events"
    _atomic_write_json(metadata_path, metadata)
    return metadata_path, exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Codex and save its native JSON trajectory"
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
