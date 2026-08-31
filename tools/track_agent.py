"""Run an agent CLI and persist its execution transcript.

The wrapper deliberately keeps the stored format provider-neutral.  Agent CLIs
which emit JSONL retain the decoded object in ``data`` while every line is
always available in ``text``.  This makes the file useful for both evaluation
and debugging when a provider changes its output format.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse the small dotenv format used by agent configuration files."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        key = key.strip(chr(34) + chr(39)).strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_agent_environment(workspace: Path, agent: str) -> dict[str, str]:
    """Load dotenv files relevant to *agent* without overriding the process."""
    names = [".env", f".env.{agent.lower()}", f".{agent.lower()}.env"]
    loaded: dict[str, str] = {}
    # Specific files are applied after generic .env files.
    roots: list[Path] = []
    for root in (Path(__file__).resolve().parents[1], Path.cwd(), workspace):
        root = root.resolve()
        if root not in roots:
            roots.append(root)
    for root in roots:
        loaded.update(parse_env_file(root / ".env"))
    for root in roots:
        for name in names[1:]:
            loaded.update(parse_env_file(root / name))
    result = os.environ.copy()
    for key, value in loaded.items():
        result.setdefault(key, value)
    return result

def build_command(agent: str, model: str | None, extra_args: Iterable[str]) -> list[str]:
    """Build a non-interactive command for common CLIs.

    ``--arg`` can be used for provider-specific flags.  Unknown agents are
    treated as executable names and receive the conventional ``--model`` flag
    when a model was supplied.
    """
    command = shlex.split(agent, posix=os.name != "nt")
    if not command:
        raise ValueError("agent must not be empty")
    name = Path(command[0]).name.lower()
    if os.name == "nt" and name == "codex":
        # Popen does not resolve PowerShell aliases; prefer npm batch launcher.
        resolved = shutil.which("codex.cmd") or shutil.which("codex.exe")
        if resolved:
            command[0] = resolved
            name = Path(resolved).name.lower()
    # npm installs the Windows launcher as codex.cmd (PowerShell may resolve
    # the same command through codex.ps1). Treat both launchers as Codex CLI.
    if name in {"codex", "codex.exe", "codex.cmd", "codex.ps1"}:
        command += ["exec", "--json"]
        if model:
            command += ["--model", model]
    elif name in {"claude", "claude.exe"}:
        command += ["--print", "--output-format", "stream-json"]
        if model:
            command += ["--model", model]
    elif name in {"gemini", "gemini.exe"}:
        command += ["--output-format", "stream-json"]
        if model:
            command += ["--model", model]
    elif model:
        command += ["--model", model]
    command.extend(str(item) for item in extra_args)
    return command


def _safe_task_id(task_id: str) -> str:
    if not task_id or task_id in {".", ".."} or Path(task_id).name != task_id:
        raise ValueError("task_id must be a plain file name without path separators")
    return task_id


def _atomic_write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _parse_line(line: str) -> Any | None:
    stripped = line.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def capture_patch(workspace: Path) -> str:
    """Return the tracked-file diff currently present in an agent workspace."""
    try:
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--"],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return f"# unable to capture patch: {exc}\n"
    patch = result.stdout
    try:
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout.splitlines()
        for relative in untracked:
            file_path = workspace / relative
            if file_path.is_file():
                diff = subprocess.run(
                    ["git", "diff", "--no-index", "--binary", "--", os.devnull, relative],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                patch += diff.stdout
    except OSError:
        pass
    return patch



def update_track(
    path: str | Path,
    *,
    reward: float | int | None = None,
    test_result: str | None = None,
    test_exit_code: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Atomically add evaluation fields to a completed trajectory document."""
    track_path = Path(path)
    try:
        document = json.loads(track_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid track JSON: {track_path}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"track JSON must contain an object: {track_path}")
    if reward is not None:
        document["reward"] = float(reward)
    if test_result is not None:
        document["test_result"] = test_result
    if test_exit_code is not None:
        document["test_exit_code"] = int(test_exit_code)
    if extra:
        document.update(extra)
    document["updated_at"] = utc_now()
    _atomic_write(track_path, document)


def run_agent(
    *,
    workspace: Path,
    agent: str,
    model: str | None,
    task_id: str,
    output_dir: Path,
    prompt: str | None = None,
    extra_args: Iterable[str] = (),
) -> tuple[Path, int]:
    task_id = _safe_task_id(task_id)
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    output_path = output_dir / f"{task_id}.json"
    command = build_command(agent, model, extra_args)
    started_at = utc_now()
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "track_id": str(uuid.uuid4()),
        "task_id": task_id,
        "agent": agent,
        "model": model,
        "workspace": str(workspace.resolve()),
        "command": command,
        "prompt": prompt,
        "started_at": started_at,
        "finished_at": None,
        "status": "running",
        "exit_code": None,
        "patch": "",
        "events": [],
    }
    _atomic_write(output_path, document)

    try:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=load_agent_environment(workspace, agent),
            stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        document.update(
            status="failed",
            finished_at=utc_now(),
            exit_code=127,
            error=f"failed to start agent: {exc}",
            patch=capture_patch(workspace),
        )
        _atomic_write(output_path, document)
        return output_path, 127

    if prompt is not None and process.stdin is not None:
        process.stdin.write(prompt)
        if not prompt.endswith("\n"):
            process.stdin.write("\n")
        process.stdin.close()

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

    sequence = 0
    while any(thread.is_alive() for thread in threads) or not lines.empty():
        try:
            stream_name, line = lines.get(timeout=0.05)
        except queue.Empty:
            continue
        sequence += 1
        event: dict[str, Any] = {
            "seq": sequence,
            "timestamp": utc_now(),
            "stream": stream_name,
            "text": line.rstrip("\r\n"),
        }
        parsed = _parse_line(line)
        if parsed is not None:
            event["data"] = parsed
        document["events"].append(event)
        _atomic_write(output_path, document)

    for thread in threads:
        thread.join()
    exit_code = process.wait()
    document.update(
        status="completed" if exit_code == 0 else "failed",
        finished_at=utc_now(),
        exit_code=exit_code,
        patch=capture_patch(workspace),
    )
    _atomic_write(output_path, document)
    return output_path, exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record an agent CLI execution trajectory")
    parser.add_argument("--workspace", required=True, type=Path, help="agent working directory")
    parser.add_argument("--agent", required=True, help="agent CLI, for example codex")
    parser.add_argument("--model", default=None, help="model passed to the agent CLI")
    parser.add_argument("--task_id", "--task-id", required=True, help="task identifier")
    parser.add_argument("--output_dir", "--output-dir", required=True, type=Path)
    parser.add_argument("--prompt", help="optional task prompt sent to the CLI stdin")
    parser.add_argument(
        "--arg",
        dest="extra_args",
        action="append",
        default=[],
        help="additional CLI argument; repeat for multiple arguments",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_path, exit_code = run_agent(
            workspace=args.workspace,
            agent=args.agent,
            model=args.model,
            task_id=args.task_id,
            output_dir=args.output_dir,
            prompt=args.prompt,
            extra_args=args.extra_args,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(output_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
