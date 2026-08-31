"""Build ArkTS function-restoration benchmark instances.

The candidate pool and ordering match ``build_arkts_bug_instance.py``.  This
variant selects the first two candidates, replaces each function body with an
empty body, and asks Codex to create a focused ``test/test.py`` regression test.

Example:

    python tools/build_arkts_bug_mask_instance.py hi-dhl/HarmonyPractice
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKOUT_ROOT = PROJECT_ROOT / ".tmp" / "arkts-mask-checkouts"
REFERENCE_RUNNER = PROJECT_ROOT / "instances" / "HarmonyPractice_Index_build_0" / "run_tests.py"
INSTANCE_COUNT = 2
ALLOWED_TEST_PATH = "test/test.py"

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from arkts_syntax_tree import (  # noqa: E402
    DEFAULT_MUTATION_OPERATORS,
    find_bug_candidates,
    parse_repository,
    write_syntax_tree_outputs,
)
from arkts_syntax_tree.bug_instance import (  # noqa: E402
    FunctionInfo,
    _build_import_index,
    extract_callees,
    flatten_functions,
    load_syntax_tree_jsonl,
)
from tools.track_agent import capture_patch, run_agent  # noqa: E402


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be at least 0")
    return parsed


def _run(
    command: list[str],
    cwd: Path,
    *,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _metadata_repo_from_url(value: str) -> str | None:
    normalized = value.strip().rstrip("/").removesuffix(".git")
    if normalized.startswith("git@github.com:"):
        return normalized.split("git@github.com:", 1)[1]
    if "github.com/" in normalized:
        return normalized.split("github.com/", 1)[1]
    return None


def _repo_details(repo: str) -> tuple[str, str, str]:
    raw = repo.strip().rstrip("/")
    if not raw:
        raise ValueError("repository name must not be empty")

    local = Path(raw).expanduser()
    if local.is_dir() and (local / ".git").is_dir():
        resolved = local.resolve()
        remote = _run(["git", "config", "--get", "remote.origin.url"], resolved)
        metadata_repo = _metadata_repo_from_url(remote.stdout) or resolved.name
        return str(resolved), resolved.name, metadata_repo

    if raw.startswith("git@") or "://" in raw:
        name = Path(raw.rsplit("/", 1)[-1]).stem or "repository"
        return raw, name, _metadata_repo_from_url(raw) or raw.removesuffix(".git")

    normalized = raw.removesuffix(".git")
    return f"https://github.com/{normalized}.git", normalized.rsplit("/", 1)[-1], normalized


def _remove_tree(path: Path) -> None:
    def make_writable_and_retry(function: Any, name: str, error: Any) -> None:
        del error
        os.chmod(name, stat.S_IWRITE)
        function(name)

    shutil.rmtree(path, onerror=make_writable_and_retry)


def _clone(repo: str, destination: Path) -> tuple[Path, str]:
    url, _, metadata_repo = _repo_details(repo)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        _remove_tree(destination)
    result = _run(["git", "clone", url, str(destination)], PROJECT_ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "git clone failed")
    return destination, metadata_repo


def _ensure_syntax_tree(repo: Path, path: Path) -> None:
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    parsed = parse_repository(repo, extensions=[".ets", ".ts"])
    write_syntax_tree_outputs(
        parsed,
        output_path=path,
        summary_path=path.with_name(path.stem + "_summary.json"),
    )


def _function_catalog(syntax_tree: Path) -> dict[str, FunctionInfo]:
    functions = flatten_functions(load_syntax_tree_jsonl(syntax_tree))
    return {function.identity: function for function in functions}


def _call_graph(
    repo: Path,
    syntax_tree: Path,
) -> tuple[dict[str, set[str]], dict[str, FunctionInfo]]:
    catalog = _function_catalog(syntax_tree)
    by_qualified: dict[str, set[str]] = {}
    by_name: dict[str, set[str]] = {}
    for function in catalog.values():
        by_qualified.setdefault(function.qualified_name, set()).add(function.identity)
        by_name.setdefault(function.name, set()).add(function.identity)

    records = load_syntax_tree_jsonl(syntax_tree)
    functions_by_path: dict[str, list[FunctionInfo]] = {}
    for function in catalog.values():
        functions_by_path.setdefault(function.path, []).append(function)
    imports_by_path = _build_import_index(records, {record['path'] for record in records})

    graph = {identity: set() for identity in catalog}
    for function in catalog.values():
        source_path = repo / function.path
        try:
            source = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for callee in extract_callees(source, function):
            matches = by_qualified.get(callee)
            if not matches:
                matches = by_name.get(callee.rsplit(".", 1)[-1], set())
            matches = _resolve_callee(
                function, callee, functions_by_path, imports_by_path,
                by_qualified, by_name,
            )
            graph[function.identity].update(matches)
    return graph, catalog


def _resolve_callee(
    caller: FunctionInfo,
    callee: str,
    functions_by_path: dict[str, list[FunctionInfo]],
    imports_by_path: dict[str, list[Any]],
    by_qualified: dict[str, set[str]],
    by_name: dict[str, set[str]],
) -> set[str]:
    '''Resolve calls without conflating common method names across the repository.'''
    leaf = callee.rsplit('.', 1)[-1]
    same_path = functions_by_path.get(caller.path, [])
    if callee.startswith('this.'):
        owned = {
            item.identity
            for item in same_path
            if item.name == leaf and item.owner_name == caller.owner_name
        }
        if owned:
            return owned

    nested = {
        item.identity
        for item in same_path
        if item.name == leaf
        and caller.start_line <= item.start_line
        and item.end_line <= caller.end_line
        and item.identity != caller.identity
    }
    if nested:
        return nested

    for binding in imports_by_path.get(caller.path, []):
        imported_name: str | None = None
        if binding.kind == 'namespace' and callee.startswith(binding.local_name + '.'):
            imported_name = leaf
        elif binding.kind == 'named' and callee == binding.local_name:
            imported_name = binding.imported_name
        elif binding.kind == 'default' and callee == binding.local_name:
            imported_name = 'default'
        if imported_name is None:
            continue
        imported = {
            item.identity
            for item in functions_by_path.get(binding.source_path, [])
            if (
                (imported_name == 'default' and 'default' in item.modifiers)
                or item.name == imported_name
                or item.qualified_name == imported_name
            )
        }
        if imported:
            return imported

    local = {
        item.identity
        for item in same_path
        if item.name == leaf or item.qualified_name == callee
    }
    if local:
        return local
    qualified = by_qualified.get(callee, set())
    if len(qualified) == 1:
        return set(qualified)
    named = by_name.get(leaf, set())
    return set(named) if len(named) == 1 else set()


def _reachable_functions(graph: dict[str, set[str]], root: str) -> dict[str, int]:
    distances: dict[str, int] = {}
    pending = [(identity, 1) for identity in graph.get(root, ())]
    while pending:
        identity, distance = pending.pop(0)
        if identity == root or identity in distances:
            continue
        distances[identity] = distance
        pending.extend((child, distance + 1) for child in graph.get(identity, ()))
    return distances


def _downstream_dependency_count(graph: dict[str, set[str]], root: str) -> int:
    downstream = set(_reachable_functions(graph, root))
    return sum(
        1
        for caller, callees in graph.items()
        if caller not in downstream
        for callee in callees
        if callee in downstream
    )


def _eligible_candidates(
    repo: Path,
    syntax_tree: Path,
    *,
    min_out_degree: int,
    min_consumers: int,
    mutation_operator: str | None,
    min_downstream_dependencies: int,
) -> tuple[list[tuple[Any, int]], dict[str, set[str]], dict[str, FunctionInfo]]:
    candidates = find_bug_candidates(
        repo,
        syntax_tree,
        min_out_degree=min_out_degree,
        min_downstream_consumers=min_consumers,
        mutation_operators=[mutation_operator] if mutation_operator else None,
    )
    graph, catalog = _call_graph(repo, syntax_tree)
    eligible = []
    for candidate in candidates:
        dependency_count = _downstream_dependency_count(graph, candidate.function.identity)
        if dependency_count >= min_downstream_dependencies:
            eligible.append((candidate, dependency_count))
    return eligible, graph, catalog


def _line_ending(value: str) -> str:
    if value.endswith("\r\n"):
        return "\r\n"
    if value.endswith("\n"):
        return "\n"
    return ""


def _mask_function(source: str, function: FunctionInfo) -> tuple[str, str]:
    """Replace a named function's body with an empty body, preserving its declaration."""
    lines = source.splitlines(keepends=True)
    start = function.start_line - 1
    end = function.end_line - 1
    if start < 0 or end >= len(lines) or end < start:
        raise ValueError(f"invalid source range for {function.identity}")

    first = lines[start]
    open_brace = first.find("{")
    if open_brace < 0:
        raise ValueError(f"function declaration has no opening brace: {function.identity}")
    indent = re.match(r"\s*", first).group(0)
    ending = _line_ending(first) or "\n"
    declaration = first[: open_brace + 1].rstrip("\r\n")
    if start == end:
        masked_lines = [declaration + "}" + ending]
    else:
        last = lines[end]
        close_brace = last.rfind("}")
        suffix = last[close_brace:] if close_brace >= 0 else "}" + _line_ending(last)
        if not _line_ending(suffix):
            suffix += ending
        masked_lines = [declaration + ending, indent + suffix.lstrip()]

    original_function = "".join(lines[start : end + 1])
    masked = "".join(lines[:start] + masked_lines + lines[end + 1 :])
    if masked == source:
        raise ValueError(f"mask did not change {function.identity}")
    return masked, original_function


def _make_patch(before: str, after: str, relative_path: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
        n=3,
    )
    patch_lines: list[str] = []
    for line in diff:
        patch_lines.append(line if line.endswith(("\n", "\r")) else line + "\n")
        if not line.endswith(("\n", "\r")):
            patch_lines.append("\\ No newline at end of file\n")
    patch = "".join(patch_lines)
    if not patch:
        raise ValueError(f"empty patch for {relative_path}")
    return patch if patch.endswith("\n") else patch + "\n"


def _reset_checkout(repo: Path, commit: str) -> None:
    reset = _run(["git", "reset", "--hard", commit], repo)
    if reset.returncode != 0:
        raise RuntimeError(reset.stderr or reset.stdout)
    clean = _run(["git", "clean", "-fd"], repo)
    if clean.returncode != 0:
        raise RuntimeError(clean.stderr or clean.stdout)


def _checkpoint_mask(repo: Path) -> None:
    add = _run(["git", "add", "--all"], repo)
    if add.returncode != 0:
        raise RuntimeError(add.stderr or add.stdout)
    commit = _run(
        [
            "git",
            "-c",
            "user.name=benchmark",
            "-c",
            "user.email=benchmark@localhost",
            "commit",
            "--allow-empty",
            "-m",
            "masked-function-baseline",
        ],
        repo,
    )
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr or commit.stdout)


def _test_prompt(function: FunctionInfo, original_function: str) -> str:
    return f"""This HarmonyOS ArkTS repository contains a deliberately masked function:
`{function.qualified_name}` in `{function.path}`. Its body is empty in the current checkout.

Create a focused deterministic regression test at exactly `test/test.py`. The test must fail
against the current empty-body baseline and pass when the intended function implementation is
restored. The test is run with Python from the repository root, so use only the Python standard
library. Static source assertions are acceptable and expected when HarmonyOS tooling is not
available, but assert the function's meaningful intended behavior rather than only checking that
the body is non-empty. Only create or modify `test/test.py`; do not edit production files, build
configuration, or any existing ArkTS tests.

Use this original implementation only to derive the regression assertions:
```arkts
{original_function.rstrip()}
```
"""


def _fallback_test(function: FunctionInfo, original_function: str) -> str:
    expected = original_function.replace("\r\n", "\n").strip()
    return (
        "from pathlib import Path\n\n"
        f"path = Path(__file__).resolve().parents[1] / {function.path!r}\n"
        "source = path.read_text(encoding='utf-8').replace('\\r\\n', '\\n')\n"
        f"expected = {expected!r}\n"
        f"assert expected in source, {function.qualified_name!r} + ' was not restored'\n"
        f"print({function.qualified_name!r} + ' restoration test passed')\n"
    )


def _write_fallback_test(repo: Path, function: FunctionInfo, original_function: str) -> None:
    test_file = repo / ALLOWED_TEST_PATH
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(_fallback_test(function, original_function), encoding="utf-8")


def _changed_paths(repo: Path) -> set[str]:
    changed = _run(["git", "diff", "--name-only", "HEAD", "--"], repo)
    untracked = _run(["git", "ls-files", "--others", "--exclude-standard"], repo)
    if changed.returncode != 0 or untracked.returncode != 0:
        raise RuntimeError(changed.stderr or untracked.stderr or "cannot inspect Codex changes")
    return {
        line.strip().replace("\\", "/")
        for line in (changed.stdout + "\n" + untracked.stdout).splitlines()
        if line.strip()
    }


def _generate_test_patch(
    repo: Path,
    function: FunctionInfo,
    original_function: str,
    *,
    codex_cli: str,
    model: str | None,
    codex_sandbox: str,
    tracks: Path,
    task_id: str,
    skip_codex: bool,
) -> str:
    if skip_codex:
        _write_fallback_test(repo, function, original_function)
    else:
        track, exit_code = run_agent(
            workspace=repo,
            agent=codex_cli,
            model=model,
            task_id=f"{task_id}_tests",
            output_dir=tracks,
            prompt=_test_prompt(function, original_function),
            extra_args=["--sandbox", codex_sandbox],
        )
        if exit_code != 0:
            raise RuntimeError(f"Codex test generation failed (track: {track})")

    changed_paths = _changed_paths(repo)
    if changed_paths != {ALLOWED_TEST_PATH}:
        raise RuntimeError(
            "test generation must change only test/test.py; changed: "
            + ", ".join(sorted(changed_paths))
        )
    patch = capture_patch(repo)
    if not patch.strip():
        raise RuntimeError("test generation produced an empty patch")
    return patch


def _apply_patch(repo: Path, patch_path: Path) -> None:
    check = _run(["git", "apply", "--check", str(patch_path)], repo)
    if check.returncode != 0:
        raise RuntimeError(check.stderr or check.stdout or f"cannot apply {patch_path}")
    applied = _run(["git", "apply", str(patch_path)], repo)
    if applied.returncode != 0:
        raise RuntimeError(applied.stderr or applied.stdout or f"cannot apply {patch_path}")


def _run_python_test(repo: Path) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, ALLOWED_TEST_PATH], repo)


def _verify_test(repo: Path, gold_patch: Path) -> None:
    masked = _run_python_test(repo)
    if masked.returncode == 0:
        raise RuntimeError("generated test unexpectedly passes against the masked function")
    _apply_patch(repo, gold_patch)
    restored = _run_python_test(repo)
    if restored.returncode != 0:
        output = (restored.stdout + restored.stderr).strip()
        raise RuntimeError(f"generated test fails after restoration: {output}")


def _function_record(function: FunctionInfo, *, distance: int | None = None) -> dict[str, Any]:
    record = function.to_dict()
    record["identity"] = function.identity
    if distance is not None:
        record["call_distance"] = distance
    return record


def _call_metadata(
    target: FunctionInfo,
    graph: dict[str, set[str]],
    catalog: dict[str, FunctionInfo],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    direct_ids = sorted(
        (caller for caller, callees in graph.items() if target.identity in callees),
        key=lambda identity: (
            catalog[identity].path,
            catalog[identity].start_line,
            catalog[identity].qualified_name,
        ),
    )
    reachable = _reachable_functions(graph, target.identity)
    upstream_ids = sorted(
        reachable,
        key=lambda identity: (
            reachable[identity],
            catalog[identity].path,
            catalog[identity].start_line,
            catalog[identity].qualified_name,
        ),
    )
    return (
        [_function_record(catalog[identity]) for identity in direct_ids],
        [
            _function_record(catalog[identity], distance=reachable[identity])
            for identity in upstream_ids
        ],
    )


def _instruction(function: FunctionInfo) -> str:
    return (
        f"Restore the complete implementation of function `{function.qualified_name}` in "
        f"`{function.path}`. Preserve its declaration and surrounding code, and restore the "
        "function's original behavior. Do not modify tests.\n"
    )


def _safe_target_name(function: FunctionInfo) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", function.qualified_name).strip("._-")
    return value.replace(".", "_") or "function"


def _instance_id(base: str | None, repo_name: str, function: FunctionInfo, index: int) -> str:
    default = f"{repo_name}_{_safe_target_name(function)}_mask_{index}"
    if base is None:
        return default
    safe_base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
    if not safe_base:
        raise ValueError("instance-id must contain a letter or digit")
    return f"{safe_base}_{index}"


def _copy_runner(destination: Path) -> None:
    if not REFERENCE_RUNNER.is_file():
        raise FileNotFoundError(f"reference runner is absent: {REFERENCE_RUNNER}")
    shutil.copy2(REFERENCE_RUNNER, destination)


def _write_instance(
    staging_dir: Path,
    *,
    instance_id: str,
    metadata_repo: str,
    commit: str,
    candidate: Any,
    dependency_count: int,
    direct_callers: list[dict[str, Any]],
    upstream_functions: list[dict[str, Any]],
    error_patch: str,
    gold_patch: str,
    test_patch: str,
) -> None:
    function = candidate.function
    staging_dir.mkdir(parents=True)
    (staging_dir / "error.patch").write_text(error_patch, encoding="utf-8", newline="")
    (staging_dir / "gold.patch").write_text(gold_patch, encoding="utf-8", newline="")
    (staging_dir / "test.patch").write_text(test_patch, encoding="utf-8", newline="")
    instruction = _instruction(function)
    (staging_dir / "instruction.md").write_text(instruction, encoding="utf-8")
    _copy_runner(staging_dir / "run_tests.py")

    metadata = {
        "schema_version": 2,
        "instance_id": instance_id,
        "task_type": "function_restore",
        "repo": metadata_repo,
        "commit": commit,
        "description": instruction.strip(),
        "details": {
            "function": _function_record(function),
            "mask_kind": "empty_function_body",
            "out_degree": candidate.out_degree,
            "impact_score": candidate.impact_score,
            "downstream_dependency_count": dependency_count,
            "direct_callers": direct_callers,
            "upstream_functions": upstream_functions,
            "patches": {
                "error": "error.patch",
                "gold": "gold.patch",
                "test": "test.patch",
            },
        },
    }
    (staging_dir / "instance.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build two Codex-tested ArkTS masked-function instances"
    )
    parser.add_argument("repo", help="GitHub owner/name, URL, or local Git repository")
    parser.add_argument("--syntax-tree", type=Path, help="syntax tree JSONL; generated when absent")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "instances")
    parser.add_argument("--checkout-root", type=Path, default=DEFAULT_CHECKOUT_ROOT)
    parser.add_argument("--instance-id", help="base id; _0 and _1 are appended")
    parser.add_argument("--codex-cli", default="codex")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--codex-sandbox",
        choices=["read-only", "workspace-write", "danger-full-access"],
        default="workspace-write",
    )
    parser.add_argument("--min-out-degree", type=positive_int, default=1)
    parser.add_argument("--min-consumers", type=nonnegative_int, default=0)
    parser.add_argument("--mutation-operator", choices=DEFAULT_MUTATION_OPERATORS)
    parser.add_argument("--min-downstream-dependencies", type=positive_int, default=1)
    parser.add_argument("--list-candidates", action="store_true")
    parser.add_argument(
        "--skip-codex",
        action="store_true",
        help="generate an exact source-regression test locally instead of invoking Codex",
    )
    parser.add_argument("--keep-checkout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _, repo_basename, _ = _repo_details(args.repo)
    checkout = args.checkout_root.resolve() / repo_basename
    output_dir = args.output_dir.resolve()
    syntax_tree = (
        args.syntax_tree
        or PROJECT_ROOT / "syntax_trees" / f"{repo_basename}_syntax_tree.jsonl"
    ).resolve()

    try:
        repo, metadata_repo = _clone(args.repo, checkout)
        _ensure_syntax_tree(repo, syntax_tree)
        head = _run(["git", "rev-parse", "HEAD"], repo)
        if head.returncode != 0:
            raise RuntimeError(head.stderr or head.stdout)
        original_commit = head.stdout.strip()
        eligible, graph, catalog = _eligible_candidates(
            repo,
            syntax_tree,
            min_out_degree=args.min_out_degree,
            min_consumers=args.min_consumers,
            mutation_operator=args.mutation_operator,
            min_downstream_dependencies=args.min_downstream_dependencies,
        )

        if args.list_candidates:
            records = []
            for candidate, dependency_count in eligible:
                direct, upstream = _call_metadata(candidate.function, graph, catalog)
                records.append(
                    {
                        **candidate.to_dict(),
                        "downstream_dependency_count": dependency_count,
                        "direct_callers": direct,
                        "upstream_functions": upstream,
                    }
                )
            print(json.dumps(records, ensure_ascii=False, indent=2))
            return 0

        if len(eligible) < INSTANCE_COUNT:
            raise ValueError(
                f"expected at least {INSTANCE_COUNT} eligible functions, found {len(eligible)}"
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        staging_parent = output_dir / ".staging"
        staging_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="arkts-mask-", dir=staging_parent) as temporary:
            temporary_root = Path(temporary)
            for index, (candidate, dependency_count) in enumerate(eligible[:INSTANCE_COUNT]):
                function = candidate.function
                instance_id = _instance_id(args.instance_id, repo_basename, function, index)
                final_dir = output_dir / instance_id
                if final_dir.exists():
                    raise FileExistsError(f"instance already exists: {final_dir}")

                _reset_checkout(repo, original_commit)
                target_path = repo / function.path
                with target_path.open(
                    "r", encoding="utf-8", errors="replace", newline=""
                ) as source_file:
                    original_source = source_file.read()
                masked_source, original_function = _mask_function(original_source, function)
                error_patch = _make_patch(original_source, masked_source, function.path)
                gold_patch = _make_patch(masked_source, original_source, function.path)
                target_path.write_text(masked_source, encoding="utf-8", newline="")
                _checkpoint_mask(repo)

                gold_path = temporary_root / f"{instance_id}.gold.patch"
                gold_path.write_text(gold_patch, encoding="utf-8", newline="")
                test_patch = _generate_test_patch(
                    repo,
                    function,
                    original_function,
                    codex_cli=args.codex_cli,
                    model=args.model,
                    codex_sandbox=args.codex_sandbox,
                    tracks=temporary_root / "tracks" / instance_id,
                    task_id=instance_id,
                    skip_codex=args.skip_codex,
                )
                _verify_test(repo, gold_path)
                direct_callers, upstream_functions = _call_metadata(function, graph, catalog)

                staged_instance = temporary_root / "instances" / instance_id
                _write_instance(
                    staged_instance,
                    instance_id=instance_id,
                    metadata_repo=metadata_repo,
                    commit=original_commit,
                    candidate=candidate,
                    dependency_count=dependency_count,
                    direct_callers=direct_callers,
                    upstream_functions=upstream_functions,
                    error_patch=error_patch,
                    gold_patch=gold_patch,
                    test_patch=test_patch,
                )
                shutil.move(str(staged_instance), str(final_dir))
                print(f"instance={instance_id}")
                print(f"path={final_dir}")
        try:
            staging_parent.rmdir()
        except OSError:
            pass
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if checkout.exists() and not args.keep_checkout:
            try:
                _remove_tree(checkout)
            except OSError as exc:
                print(f"warning: failed to remove checkout {checkout}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
