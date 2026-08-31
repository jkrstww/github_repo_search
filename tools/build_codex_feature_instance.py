"""Build a Codex-assisted feature implementation benchmark instance.

The generator clones a GitHub repository into a temporary checkout, asks Codex
to identify one meaningful implementation and the files needed to understand
it, masks the selected line range, and asks Codex to add test/test.py.
Only patches and metadata are copied to the output instance; the source
checkout is never modified in place.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from arkts_syntax_tree.feature_instance import find_feature_candidates  # noqa: E402
from tools.track_agent import run_agent  # noqa: E402


ANALYSIS_FILE = ".codex_feature_analysis.json"
REFERENCE_RUNNER = PROJECT_ROOT / "instances" / "HarmonyPractice_Second_build_1" / "run_tests.py"


def _run(command: list[str], cwd: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _repo_details(value: str) -> tuple[str, str, str]:
    raw = value.strip().rstrip("/")
    if not raw:
        raise ValueError("repository name must not be empty")
    local = Path(raw).expanduser()
    if local.is_dir() and (local / ".git").is_dir():
        return str(local.resolve()), local.name, local.name
    if raw.startswith("git@") or "://" in raw:
        name = Path(raw.rsplit("/", 1)[-1]).stem or "repository"
        metadata = raw.removesuffix(".git")
        if "github.com/" in metadata:
            metadata = metadata.split("github.com/", 1)[1]
        elif metadata.startswith("git@github.com:"):
            metadata = metadata.split("git@github.com:", 1)[1]
        return raw, name, metadata
    name = raw.rsplit("/", 1)[-1].removesuffix(".git")
    return f"https://github.com/{raw.removesuffix('.git')}.git", name, raw.removesuffix(".git")


def _clone(repo_value: str, destination: Path) -> tuple[Path, str]:
    url, _, metadata_repo = _repo_details(repo_value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = _run(["git", "clone", "--no-tags", url, str(destination)], PROJECT_ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "git clone failed")
    return destination, metadata_repo


def _git_head(repo: Path) -> str:
    result = _run(["git", "rev-parse", "HEAD"], repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "cannot read repository HEAD")
    return result.stdout.strip()


def _safe_relative_path(repo: Path, value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip().replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    normalized = candidate.as_posix()
    try:
        (repo / candidate).resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    return normalized


def _walk_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(_walk_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_walk_values(child))
    return values


def _analysis_from_track(track: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(track.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    decoder = json.JSONDecoder()
    for value in _walk_values(document.get("events", [])):
        if isinstance(value, dict) and "target_file" in value:
            return value
        if not isinstance(value, str):
            continue
        for match in re.finditer(r"\{", value):
            try:
                parsed, _ = decoder.raw_decode(value[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "target_file" in parsed:
                return parsed
    return None


def _analysis_prompt() -> str:
    return f"""Analyze this HarmonyOS/ArkTS repository for a feature-implementation benchmark.
Choose one meaningful, self-contained function, method, class, or component implementation
whose behavior can be restored from the surrounding repository. Prefer user-visible or
business-relevant behavior and a deterministic static or Python regression test.

Do not edit production or test files. Write exactly one JSON object to {ANALYSIS_FILE} in
the repository root, with these fields:
{{
  "target_file": "repository-relative source path",
  "mask_start_line": 1,
  "mask_end_line": 2,
  "symbol": "qualified symbol name",
  "task_description": "actionable repair task without revealing the exact implementation",
  "key_files": ["repository-relative files needed to understand and repair the task"],
  "reason": "short rationale"
}}
The line range must cover the complete implementation to remove. Include the target file
and the most relevant interface, caller, model, component, or configuration files in
key_files. Paths must be relative to the repository root. Return no Markdown.
"""


def _fallback_analysis(repo: Path, min_implementations: int, include_structural_usage: bool) -> dict[str, Any]:
    """Provide a deterministic offline target when Codex is unavailable."""
    candidates = find_feature_candidates(
        repo,
        min_implementation_files=min_implementations,
        include_structural_usage=include_structural_usage,
    )
    target_file: str | None = None
    symbol = "repository feature"
    key_files: list[str] = []
    if candidates:
        candidate = candidates[0]
        implementation = candidate.implementation_files[0]
        target_file = implementation.path
        symbol = implementation.local_name or candidate.abstract_node.name
        key_files = [candidate.abstract_node.path, *[item.path for item in candidate.implementation_files]]
    if target_file is None:
        for path in sorted(repo.rglob("*")):
            if path.is_file() and path.suffix in {".ets", ".ts", ".tsx"} and "node_modules" not in path.parts:
                target_file = path.relative_to(repo).as_posix()
                break
    if target_file is None:
        raise ValueError("no ArkTS/TypeScript source file found for fallback analysis")
    source = (repo / target_file).read_text(encoding="utf-8", errors="replace")
    line_count = max(1, len(source.splitlines()))
    key_files = list(dict.fromkeys([target_file, *key_files]))
    return {
        "target_file": target_file,
        "mask_start_line": 1,
        "mask_end_line": line_count,
        "symbol": symbol,
        "task_description": (
            f"Restore the implementation of {symbol} in {target_file}. Preserve the existing "
            "public API and behavior of related components, then validate it with tests."
        ),
        "key_files": key_files,
        "reason": "deterministic offline fallback",
    }


def _validate_analysis(repo: Path, raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Codex analysis must be a JSON object")
    target_file = _safe_relative_path(repo, raw.get("target_file"))
    if target_file is None or not (repo / target_file).is_file():
        raise ValueError("Codex analysis target_file is not a repository file")
    try:
        start = int(raw["mask_start_line"])
        end = int(raw["mask_end_line"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Codex analysis must provide integer mask_start_line/mask_end_line") from exc
    lines = (repo / target_file).read_text(encoding="utf-8", errors="replace").splitlines()
    if start < 1 or end < start or end > max(1, len(lines)):
        raise ValueError("Codex analysis line range is outside target_file")
    key_files = []
    raw_key_files = raw.get("key_files", [])
    if isinstance(raw_key_files, str):
        raw_key_files = [raw_key_files]
    for item in raw_key_files:
        path = _safe_relative_path(repo, item)
        if path and (repo / path).is_file() and path not in key_files:
            key_files.append(path)
    if target_file not in key_files:
        key_files.insert(0, target_file)
    description = str(raw.get("task_description") or "Restore the masked feature implementation and validate it with tests.").strip()
    return {
        "target_file": target_file,
        "mask_start_line": start,
        "mask_end_line": end,
        "symbol": str(raw.get("symbol") or "feature implementation"),
        "task_description": description,
        "key_files": key_files,
        "reason": str(raw.get("reason") or "").strip(),
    }


def _replace_lines(source: str, start: int, end: int, replacement: list[str]) -> str:
    newline = "\r\n" if "\r\n" in source else "\n"
    lines = source.splitlines(keepends=True)
    had_newline = bool(lines and lines[-1].endswith(("\n", "\r")))
    replacement_text = newline.join(replacement)
    if replacement_text:
        replacement_text += newline
    lines[start - 1 : end] = [replacement_text]
    result = "".join(lines)
    if not had_newline:
        result = result.rstrip("\r\n")
    return result


def _masked_source(source: str, analysis: dict[str, Any]) -> str:
    target = (source.splitlines() or [""])[analysis["mask_start_line"] - 1]
    indent = target[: len(target) - len(target.lstrip())]
    symbol = analysis["symbol"]
    return _replace_lines(
        source,
        analysis["mask_start_line"],
        analysis["mask_end_line"],
        [
            f"{indent}// CODE BENCHMARK MASK",
            f"{indent}// Restore implementation for: {symbol}",
            f"{indent}// Key files: {', '.join(analysis['key_files'])}",
        ],
    )


def _make_patch(before: str, after: str, relative_path: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="/dev/null" if not before else f"a/{relative_path}",
        tofile=f"b/{relative_path}",
        n=3,
    )
    result = "".join(line if line.endswith(("\n", "\r")) else line + "\n" for line in diff)
    return result if result.endswith("\n") else result + "\n"


def _test_prompt(target_file: str, symbol: str) -> str:
    return f"""The repository contains a deliberately masked implementation in {target_file}
(symbol: {symbol}). Create a focused, deterministic regression test at test/test.py.
The test must fail against the masked baseline and pass after the original implementation
is restored. Static source-based Python assertions are acceptable when HarmonyOS tooling is
unavailable. Only create or modify test/test.py; do not edit production files or build
configuration. Ensure the file is self-contained and uses only the Python standard library.
"""


def _fallback_test(target_file: str) -> str:
    escaped = target_file.replace("\\", "/")
    return (
        "from pathlib import Path\n\n"
        "source = (Path(__file__).resolve().parents[1] / "
        f"{escaped!r}).read_text(encoding=\"utf-8\")\n"
        "assert \"CODE BENCHMARK MASK\" not in source, \"the implementation is still masked\"\n"
        "print(\"feature implementation restored\")\n"
    )


def _copy_runner(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if REFERENCE_RUNNER.is_file():
        shutil.copy2(REFERENCE_RUNNER, destination)
        return
    destination.write_text(
        """import subprocess, sys
from pathlib import Path

root = Path(__file__).resolve().parent
repo = root / "repo"
subprocess.run(["git", "apply", str(root / "error.patch")], cwd=repo, check=True)
subprocess.run(["git", "apply", str(root / "test.patch")], cwd=repo, check=False)
raise SystemExit(subprocess.run([sys.executable, str(repo / "test" / "test.py")], cwd=repo).returncode)
""",
        encoding="utf-8",
    )


def _default_instance_id(repo_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name).strip(".-") or "repo"
    return f"{safe}-feature-{uuid.uuid4()}"


def _validate_instance_id(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError("instance-id must contain only letters, digits, dots, underscores, and hyphens")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Codex-assisted HarmonyOS feature instance")
    parser.add_argument("repo", help="GitHub owner/name, repository URL, or local Git repository")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "instances")
    parser.add_argument("--work-dir", type=Path, default=PROJECT_ROOT / ".tmp" / "codex-feature")
    parser.add_argument("--instance-id")
    parser.add_argument("--codex-cli", default="codex")
    parser.add_argument("--codex-sandbox", choices=["read-only", "workspace-write", "danger-full-access"], default="workspace-write")
    parser.add_argument("--model", default=None)
    parser.add_argument("--min-implementations", type=int, default=2)
    parser.add_argument("--include-structural-usage", action="store_true")
    parser.add_argument("--skip-codex", action="store_true", help="use deterministic local fallback instead of Codex")
    parser.add_argument("--require-codex", action="store_true", help="fail instead of falling back when Codex is unavailable")
    parser.add_argument("--keep-worktree", action="store_true", help="keep the temporary clone for debugging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checkout: Path | None = None
    try:
        _, repo_name, metadata_repo = _repo_details(args.repo)
        work_dir = args.work_dir.resolve()
        checkout = work_dir / repo_name
        if checkout.exists():
            shutil.rmtree(checkout)
        repo, metadata_repo = _clone(args.repo, checkout)
        commit = _git_head(repo)
        tracks = work_dir / "tracks"
        analysis: dict[str, Any] | None = None
        if not args.skip_codex:
            track, exit_code = run_agent(
                workspace=repo,
                agent=args.codex_cli,
                model=args.model,
                task_id=f"{repo_name}_feature_analysis",
                output_dir=tracks,
                prompt=_analysis_prompt(),
                extra_args=["--sandbox", args.codex_sandbox],
            )
            if exit_code == 0 and (repo / ANALYSIS_FILE).is_file():
                try:
                    analysis = json.loads((repo / ANALYSIS_FILE).read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    analysis = None
            if analysis is None and exit_code == 0:
                analysis = _analysis_from_track(track)
            if analysis is None and args.require_codex:
                raise RuntimeError(f"Codex analysis failed (track: {track})")
            if analysis is None:
                print("warning: Codex analysis unavailable; using local fallback", file=sys.stderr)
        if analysis is None:
            analysis = _fallback_analysis(repo, args.min_implementations, args.include_structural_usage)
        analysis = _validate_analysis(repo, analysis)
        (repo / ANALYSIS_FILE).unlink(missing_ok=True)
        reset = _run(["git", "reset", "--hard", commit], repo)
        if reset.returncode != 0:
            raise RuntimeError(reset.stderr or reset.stdout or "cannot reset analysis checkout")
        clean = _run(["git", "clean", "-fd"], repo)
        if clean.returncode != 0:
            raise RuntimeError(clean.stderr or clean.stdout or "cannot clean analysis checkout")

        target_path = repo / analysis["target_file"]
        original_source = target_path.read_text(encoding="utf-8", errors="replace")
        masked_source = _masked_source(original_source, analysis)
        mask_patch = _make_patch(original_source, masked_source, analysis["target_file"])
        gold_patch = _make_patch(masked_source, original_source, analysis["target_file"])
        target_path.write_text(masked_source, encoding="utf-8", newline="")

        test_path = repo / "test" / "test.py"
        old_test = test_path.read_text(encoding="utf-8", errors="replace") if test_path.is_file() else ""
        if not args.skip_codex:
            _run(["git", "add", "--all"], repo)
            _run(["git", "-c", "user.name=benchmark", "-c", "user.email=benchmark@localhost", "commit", "--allow-empty", "-m", "masked-baseline"], repo)
            _, test_exit = run_agent(
                workspace=repo,
                agent=args.codex_cli,
                model=args.model,
                task_id=f"{repo_name}_feature_tests",
                output_dir=tracks,
                prompt=_test_prompt(analysis["target_file"], analysis["symbol"]),
                extra_args=["--sandbox", args.codex_sandbox],
            )
            if test_exit != 0:
                print("warning: Codex test generation failed; using fallback test", file=sys.stderr)
        if not test_path.is_file() or not test_path.read_text(encoding="utf-8", errors="replace").strip():
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(_fallback_test(analysis["target_file"]), encoding="utf-8")
        new_test = test_path.read_text(encoding="utf-8", errors="replace")
        test_patch = _make_patch(old_test, new_test, "test/test.py")

        output_dir = args.output_dir.resolve()
        instance_id = args.instance_id or _default_instance_id(repo_name)
        _validate_instance_id(instance_id)
        instance_dir = output_dir / instance_id
        if instance_dir.exists():
            raise FileExistsError(f"instance already exists: {instance_dir}")
        instance_dir.mkdir(parents=True)
        (instance_dir / "mask.patch").write_text(mask_patch, encoding="utf-8")
        (instance_dir / "error.patch").write_text(mask_patch, encoding="utf-8")
        (instance_dir / "gold.patch").write_text(gold_patch, encoding="utf-8")
        (instance_dir / "test.patch").write_text(test_patch, encoding="utf-8")
        (instance_dir / "instruction.md").write_text(analysis["task_description"].rstrip() + "\n", encoding="utf-8")
        materialized_test = instance_dir / "test" / "test.py"
        materialized_test.parent.mkdir(parents=True)
        materialized_test.write_text(new_test, encoding="utf-8")
        _copy_runner(instance_dir / "run_tests.py")

        metadata = {
            "schema_version": 1,
            "instance_id": instance_id,
            "task_type": "feature_implementation",
            "repo": metadata_repo,
            "commit": commit,
            "description": analysis["task_description"],
            "key_files": analysis["key_files"],
            "details": {
                "target_file": analysis["target_file"],
                "symbol": analysis["symbol"],
                "mask_start_line": analysis["mask_start_line"],
                "mask_end_line": analysis["mask_end_line"],
                "reason": analysis["reason"],
                "mask_kind": "line_range",
                "patches": {"mask": "mask.patch", "error": "error.patch", "gold": "gold.patch", "test": "test.patch"},
            },
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (instance_dir / "instance.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if checkout is not None and checkout.exists() and not args.keep_worktree:
            shutil.rmtree(checkout, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

