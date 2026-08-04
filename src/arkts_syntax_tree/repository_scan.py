from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .migration import detect_android_calls
from .parser import parse_repository, write_syntax_tree_outputs


REPOSITORY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def scan_repository_android_calls(
    record: dict[str, Any],
    *,
    clone_root: str | Path | None = None,
    clone_timeout: int = 300,
) -> dict[str, Any]:
    """Shallow-clone one repository, scan its AST, and always remove the clone."""
    full_name = str(record.get("full_name") or "")
    base_result = {
        "full_name": full_name,
        "html_url": record.get("html_url"),
        "default_branch": record.get("default_branch"),
        "pr_merged_count": record.get("pr_merged_count"),
    }
    try:
        clone_url = _clone_url(record)
        parent = _prepare_clone_root(clone_root)
    except (OSError, ValueError) as exc:
        return _error_result(base_result, "input", exc)

    try:
        with tempfile.TemporaryDirectory(prefix="android-call-scan-", dir=parent) as temp_dir:
            workspace = Path(temp_dir)
            repository = workspace / "repo"
            syntax_tree = workspace / "syntax_tree.jsonl"
            clone_error = _clone_repository(
                clone_url,
                repository,
                default_branch=str(record.get("default_branch") or ""),
                timeout=clone_timeout,
            )
            if clone_error is not None:
                return _error_result(base_result, "clone", clone_error)

            commit = _git_head(repository)
            try:
                parsed_files = parse_repository(repository)
                write_syntax_tree_outputs(parsed_files, output_path=syntax_tree)
                report = detect_android_calls(syntax_tree)
            except (OSError, ValueError) as exc:
                return _error_result({**base_result, "commit": commit}, "parse", exc)

            report.pop("syntax_tree", None)
            return {
                **base_result,
                "status": "ok",
                "commit": commit,
                **report,
            }
    except OSError as exc:
        return _error_result(base_result, "cleanup", exc)


def _clone_url(record: dict[str, Any]) -> str:
    explicit_url = str(record.get("clone_url") or "").strip()
    if explicit_url:
        return explicit_url
    full_name = str(record.get("full_name") or "")
    if not REPOSITORY_NAME_PATTERN.fullmatch(full_name):
        raise ValueError(f"invalid GitHub repository name: {full_name!r}")
    return f"https://github.com/{full_name}.git"


def _prepare_clone_root(clone_root: str | Path | None) -> str | None:
    if clone_root is None:
        return None
    root = Path(clone_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _clone_repository(
    clone_url: str,
    destination: Path,
    *,
    default_branch: str,
    timeout: int,
) -> str | None:
    command = ["git", "clone", "--depth", "1", "--single-branch", "--no-tags"]
    if default_branch:
        command.extend(["--branch", default_branch])
    command.extend([clone_url, str(destination)])
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return f"git clone timed out after {timeout} seconds"
    except OSError as exc:
        return str(exc)
    if result.returncode == 0:
        return None
    detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    return detail


def _git_head(repository: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _error_result(base_result: dict[str, Any], stage: str, error: object) -> dict[str, Any]:
    return {
        **base_result,
        "status": "error",
        "error": {
            "stage": stage,
            "message": str(error),
        },
    }
