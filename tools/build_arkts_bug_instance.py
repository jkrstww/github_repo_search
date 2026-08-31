"""Build runnable ArkTS error-fix benchmark instances.

Purpose
-------
This script accepts a Git repository name, URL, or local Git repository. It
creates one independent benchmark instance for every eligible ArkTS function.
Each instance contains error.patch, gold.patch, test.patch, instruction.md,
run_tests.py, and instance.json.

Workflow
--------
1. Clone the repository temporarily into test_project/<repository-name>.
2. Generate or load the ArkTS/TypeScript syntax tree and call graph.
3. Select functions whose downstream dependency edge count reaches the threshold.
4. Generate an injected error patch and the corresponding reference repair patch.
5. Invoke the external Codex CLI separately for each instance to generate tests
   and a task instruction without revealing the exact repair.
6. Write the instance artifacts and remove the temporary checkout.

Usage
-----
Run from the project root, for example:

    python scripts/build_arkts_bug_instance.py owner/repository
    python scripts/build_arkts_bug_instance.py https://github.com/owner/repository.git
    python scripts/build_arkts_bug_instance.py D:/work/my-harmony-app

Important options:

--min-downstream-dependencies N  Minimum downstream dependency edge count.
--min-out-degree N / --min-consumers N  Additional call/consumer filters.
--mutation-operator OP  Restrict the mutation operator.
--output-dir DIR  Instance output directory.
--test-project-dir DIR  Temporary checkout directory; it is removed afterwards.
--codex-cli COMMAND  External Codex executable, default: codex.
--list-candidates  List eligible functions without creating instances.
--skip-codex  Offline mode; write an empty test patch and fallback instruction.

Example:

    python scripts/build_arkts_bug_instance.py owner/repository --min-downstream-dependencies 4
"""

from __future__ import annotations

import argparse
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
TEST_PROJECT_ROOT = PROJECT_ROOT / "test_project"
REFERENCE_RUNNER = PROJECT_ROOT / "instances" / "ausboyue-Wechat_HarmonyOS-E3DkQOL0LOThlnTp" / "run_tests.py"
MAX_CODEX_CANDIDATES = 5
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from arkts_syntax_tree import (  # noqa: E402
    DEFAULT_MUTATION_OPERATORS,
    create_bug_instance,
    find_bug_candidates,
    parse_repository,
    write_syntax_tree_outputs,
)
from arkts_syntax_tree.bug_instance import extract_callees, flatten_functions, load_syntax_tree_jsonl  # noqa: E402
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


def _repo_details(repo: str) -> tuple[str, str, str]:
    """Normalize repository input into clone URL, directory name, and metadata name."""
    """Return clone URL, checkout directory name, and metadata repository name."""
    raw = repo.strip().rstrip("/")
    if not raw:
        raise ValueError("repository name must not be empty")
    if raw.startswith("git@") or "://" in raw:
        url = raw
        name = Path(raw.rsplit("/", 1)[-1]).stem or "repository"
        metadata_repo = raw.removesuffix(".git")
        if "github.com/" in metadata_repo:
            metadata_repo = metadata_repo.split("github.com/", 1)[1]
        elif metadata_repo.startswith("git@github.com:"):
            metadata_repo = metadata_repo.split("git@github.com:", 1)[1]
        return url, name, metadata_repo
    local = Path(raw).expanduser()
    if local.is_dir() and (local / ".git").is_dir():
        return str(local.resolve()), local.name, local.name
    name = raw.rsplit("/", 1)[-1]
    return f"https://github.com/{raw.removesuffix('.git')}.git", name, raw.removesuffix(".git")


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


def _clone(repo: str, destination: Path) -> tuple[Path, str]:
    url, _, metadata_repo = _repo_details(repo)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        _remove_tree(destination)
    result = _run(["git", "clone", url, str(destination)], PROJECT_ROOT)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "git clone failed")
    return destination, metadata_repo


def _remove_tree(path: Path) -> None:
    """Remove a checkout including Windows read-only Git object files."""
    def make_writable_and_retry(function: Any, name: str, error: Any) -> None:
        del error
        os.chmod(name, stat.S_IWRITE)
        function(name)

    shutil.rmtree(path, onerror=make_writable_and_retry)


def _ensure_syntax_tree(repo: Path, path: Path) -> None:
    """Ensure the syntax tree exists, generating it from the checkout when needed."""
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    parsed = parse_repository(repo, extensions=[".ets", ".ts"])
    write_syntax_tree_outputs(
        parsed,
        output_path=path,
        summary_path=path.with_name(path.stem + "_summary.json"),
    )


def _call_graph(repo: Path, syntax_tree: Path) -> dict[str, set[str]]:
    """Build a repository-local function call graph keyed by function identity."""
    records = load_syntax_tree_jsonl(syntax_tree)
    functions = flatten_functions(records)
    by_qualified: dict[str, set[str]] = {}
    by_name: dict[str, set[str]] = {}
    for function in functions:
        by_qualified.setdefault(function.qualified_name, set()).add(function.identity)
        by_name.setdefault(function.name, set()).add(function.identity)

    graph = {function.identity: set() for function in functions}
    for function in functions:
        source_path = repo / function.path
        try:
            source = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for callee in extract_callees(source, function):
            matches = by_qualified.get(callee)
            if not matches:
                matches = by_name.get(callee.rsplit(".", 1)[-1], set())
            graph[function.identity].update(matches)
    return graph


def _downstream_dependency_count(graph: dict[str, set[str]], root: str) -> int:
    """Count external incoming edges to the root's transitive downstream closure.

    For A -> B -> C with D -> B, E -> C and F -> C, the downstream closure is
    {B, C}. B -> C is internal and excluded, leaving four dependency edges.
    """
    downstream: set[str] = set()
    pending = list(graph.get(root, ()))
    while pending:
        function = pending.pop()
        if function == root or function in downstream:
            continue
        downstream.add(function)
        pending.extend(graph.get(function, ()))
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
) -> list[tuple[Any, int]]:
    candidates = find_bug_candidates(
        repo,
        syntax_tree,
        min_out_degree=min_out_degree,
        min_downstream_consumers=min_consumers,
        mutation_operators=[mutation_operator] if mutation_operator else None,
    )
    graph = _call_graph(repo, syntax_tree)
    eligible: list[tuple[Any, int]] = []
    for candidate in candidates:
        dependency_count = _downstream_dependency_count(
            graph, candidate.function.identity
        )
        if dependency_count >= min_downstream_dependencies:
            eligible.append((candidate, dependency_count))
    return eligible


def _candidate_screen_prompt(candidates: list[tuple[Any, int]]) -> str:
    """Ask Codex to rank candidates by importance, uniqueness, and testability."""
    summaries = []
    for candidate_id, (candidate, dependency_count) in enumerate(candidates):
        function = candidate.function
        summaries.append({
            "candidate_id": candidate_id,
            "function": {"path": function.path, "qualified_name": function.qualified_name,
                         "signature": function.signature, "start_line": function.start_line,
                         "end_line": function.end_line},
            "out_degree": candidate.out_degree,
            "downstream_dependency_count": dependency_count,
            "downstream_function_count": candidate.downstream_function_count,
            "consumed_return_count": candidate.consumed_return_count,
            "impact_score": candidate.impact_score,
            "mutation_operators": sorted({item.operator_id for item in candidate.mutations}),
            "downstream_consumers": [{"path": item.path, "function_name": item.function_name,
                                      "consumption_type": item.consumption_type}
                                     for item in candidate.consumers],
        })
    return f"""You are selecting high-value ArkTS error-fix benchmark targets.
Inspect the repository and evaluate the candidates below. Select no more than {MAX_CODEX_CANDIDATES}
candidates, prioritizing importance (user-visible/business-critical behavior and broad
impact), uniqueness (distinct behavior/failure mode), deterministic testability through a
downstream consumer, and maintainability. Return ONLY valid JSON, with no Markdown:
{{"selected_candidate_ids":[0,2],"rationales":{{"0":"short reason","2":"short reason"}}}}
Candidate ids must be copied from the input. Do not edit files.

Candidates:
{json.dumps(summaries, ensure_ascii=False, indent=2)}
"""


def _walk_agent_values(value: Any) -> list[Any]:
    values = [value]
    if isinstance(value, dict):
        for child in value.values():
            values.extend(_walk_agent_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(_walk_agent_values(child))
    return values


def _selection_ids_from_track(track: Path, candidate_count: int) -> list[int]:
    document = json.loads(track.read_text(encoding="utf-8"))
    raw_selection: Any = None
    # Search Codex response events only; the stored prompt contains an example JSON.
    values = _walk_agent_values(document.get("events", []))
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("selected_candidate_ids"), list):
            raw_selection = value["selected_candidate_ids"]
            break
    if raw_selection is None:
        decoder = json.JSONDecoder()
        for value in values:
            if not isinstance(value, str):
                continue
            for match in re.finditer(r"\{", value):
                try:
                    parsed, _ = decoder.raw_decode(value[match.start():])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and isinstance(parsed.get("selected_candidate_ids"), list):
                    raw_selection = parsed["selected_candidate_ids"]
                    break
            if raw_selection is not None:
                break
    if not isinstance(raw_selection, list):
        raise ValueError("Codex candidate selection did not contain a JSON list")
    selected = []
    for item in raw_selection:
        if isinstance(item, bool) or not isinstance(item, int) or item < 0 or item >= candidate_count:
            raise ValueError("Codex candidate selection contains an invalid candidate id")
        if item not in selected:
            selected.append(item)
    if not selected:
        raise ValueError("Codex candidate selection was empty")
    return selected[:MAX_CODEX_CANDIDATES]


def _screen_candidates(candidates: list[tuple[Any, int]], *, repo: Path, codex_cli: str,
                       tracks: Path, task_id: str) -> list[tuple[Any, int]]:
    track, exit_code = run_agent(workspace=repo, agent=codex_cli, model=None,
                                 task_id=task_id, output_dir=tracks,
                                 prompt=_candidate_screen_prompt(candidates))
    if exit_code != 0:
        raise RuntimeError(f"codex candidate screening failed (track: {track})")
    try:
        selected_ids = _selection_ids_from_track(track, len(candidates))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid Codex candidate screening response: {exc}") from exc
    return [candidates[item] for item in selected_ids]


def _downstream_consumer(candidate: Any) -> Any | None:
    if not candidate.consumers:
        return None
    return sorted(candidate.consumers, key=lambda item: (item.path, item.function_name, item.call_line))[0]



def _fallback_instruction(candidate: Any) -> str:
    consumer = _downstream_consumer(candidate)
    name = consumer.function_name if consumer else "its caller"
    path = f" in {consumer.path}" if consumer else ""
    return (f"Repair the incorrect behavior observed by downstream function {name}{path}. "
            "Preserve public APIs and validate the repair with tests.\n")
def _apply_patch(repo: Path, patch_path: Path) -> None:
    result = _run(["git", "apply", "--ignore-whitespace", "--check", str(patch_path)], repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"cannot apply {patch_path}")
    result = _run(["git", "apply", "--ignore-whitespace", str(patch_path)], repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"cannot apply {patch_path}")


def _checkpoint(repo: Path) -> None:
    result = _run(["git", "add", "--all"], repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def _reset_checkout(repo: Path, commit: str) -> None:
    result = _run(["git", "reset", "--hard", commit], repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    result = _run(["git", "clean", "-fd"], repo)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    result = _run(
        [
            "git", "-c", "user.name=benchmark", "-c",
            "user.email=benchmark@localhost", "commit", "--allow-empty",
            "-m", "benchmark-error-baseline",
        ],
        repo,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def _invoke_codex(
    repo: Path,
    codex_cli: str,
    prompt: str,
    tracks: Path,
    task_id: str,
) -> str:
    track, exit_code = run_agent(
        workspace=repo,
        agent=codex_cli,
        model=None,
        task_id=task_id,
        output_dir=tracks,
        prompt=prompt,
    )
    if exit_code != 0:
        document: dict[str, Any] = {}
        try:
            document = json.loads(track.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        detail = document.get("error") or f"codex exited with status {exit_code}"
        raise RuntimeError(str(detail))
    return capture_patch(repo)


def _make_test_patch(
    repo: Path,
    error_patch: Path,
    *,
    codex_cli: str,
    tracks: Path,
    task_id: str,
) -> str:
    _apply_patch(repo, error_patch)
    _checkpoint(repo)
    patch = error_patch.read_text(encoding="utf-8")
    return _invoke_codex(
        repo,
        codex_cli,
        f"""Inspect this HarmonyOS ArkTS repository and the injected defect represented by
the provided error patch. Add focused, deterministic regression tests that fail on the
broken baseline and pass after the intended repair. Only add or modify test files; do
not modify production source files. Do not change build configuration unless it is
required for the tests. Leave the working tree containing the test changes.

The exact patch applied to this checkout is:
{patch}
""",
        tracks,
        f"{task_id}_tests",
    )


def _make_instruction(
    repo: Path,
    error_patch: Path,
    candidate: Any,
    *,
    codex_cli: str,
    tracks: Path,
    task_id: str,
) -> str:
    patch = error_patch.read_text(encoding="utf-8")
    consumer = _downstream_consumer(candidate)
    consumer_text = "No concrete downstream consumer was found; describe the observable caller behavior from repository evidence."
    if consumer is not None:
        consumer_text = (f"Focus on downstream function `{consumer.function_name}` in `{consumer.path}` "
                         f"(call line {consumer.call_line}, consumption: {consumer.consumption_type}).")
    _run(["git", "reset", "--hard", "HEAD"], repo)
    _run(["git", "clean", "-fd"], repo)
    prompt = f"""Study this HarmonyOS ArkTS repository and the injected defect. Write a file named
instruction.md in the repository root containing a concrete repair task. The injected candidate is `{candidate.function.qualified_name}` in `{candidate.function.path}`; do not mention that target in the instruction.
Explicitly identify this downstream function as exhibiting the faulty observable behavior: {consumer_text}
Describe affected area, constraints, and validation. The task must be actionable but must
NOT reveal the exact changed expression, replacement value, patch text, line number,
or implementation strategy. Do not edit source code.

Injected patch (for context only):
{patch}
"""
    _invoke_codex(repo, codex_cli, prompt, tracks, f"{task_id}_instruction")
    instruction = repo / "instruction.md"
    if instruction.is_file() and instruction.read_text(encoding="utf-8").strip():
        return instruction.read_text(encoding="utf-8").strip() + "\n"
    return _fallback_instruction(candidate)


def _copy_runner(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if REFERENCE_RUNNER.is_file():
        shutil.copy2(REFERENCE_RUNNER, destination)
        return
    destination.write_text(
        """from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
repo = root / "repo"
subprocess.run(["git", "apply", str(root / "error.patch")], cwd=repo, check=True)
subprocess.run(["git", "apply", str(root / "test.patch")], cwd=repo, check=False)
result = subprocess.run([sys.executable, "-m", "pytest"], cwd=repo)
raise SystemExit(result.returncode)
""",
        encoding="utf-8",
    )


def _write_instance(
    legacy: dict[str, Any],
    *,
    instance_dir: Path,
    repo_name: str,
    commit: str | None,
    instruction: str,
    test_patch: str,
) -> dict[str, Any]:
    (instance_dir / "instruction.md").write_text(instruction, encoding="utf-8")
    (instance_dir / "test.patch").write_text(test_patch, encoding="utf-8")
    _copy_runner(instance_dir / "run_tests.py")
    target = legacy["target"]
    metadata = {
        "schema_version": 1,
        "instance_id": legacy["instance_id"],
        "task_type": "error_fix",
        "repo": repo_name,
        "commit": commit,
        "details": target,
        "description": instruction.strip(),
    }
    (instance_dir / "instance.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def build_parser() -> argparse.ArgumentParser:
    """Define command-line arguments and their help text."""
    parser = argparse.ArgumentParser(
        description="Clone a repository and build an ArkTS error-fix instance"
    )
    parser.add_argument(
        "repo",
        help="GitHub repository name (owner/name), URL, or local git repository",
    )
    parser.add_argument("--syntax-tree", type=Path, help="syntax tree JSONL; generated when absent")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "instances")
    parser.add_argument("--test-project-dir", type=Path, default=TEST_PROJECT_ROOT)
    parser.add_argument("--codex-cli", default="codex", help="external Codex executable")
    parser.add_argument("--min-out-degree", type=positive_int, default=1)
    parser.add_argument(
        "--min-consumers",
        type=nonnegative_int,
        default=0,
        help="optional direct-caller filter retained for compatibility (default: 0)",
    )
    parser.add_argument("--instance-id")
    parser.add_argument("--mutation-operator", choices=DEFAULT_MUTATION_OPERATORS)
    parser.add_argument("--selection-seed", type=int)
    parser.add_argument(
        "--min-downstream-dependencies",
        "--min-downstream-dependency-count",
        dest="min_downstream_dependencies",
        type=positive_int,
        default=1,
        help="minimum downstream dependency edges for each candidate (default: 1)",
    )
    parser.add_argument("--list-candidates", action="store_true")
    parser.add_argument("--skip-codex", action="store_true", help="write empty test.patch and fallback instruction (offline mode)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the build workflow and always clean up the temporary checkout."""
    args = build_parser().parse_args(argv)
    _, repo_basename, _ = _repo_details(args.repo)
    test_project_dir = args.test_project_dir.resolve()
    checkout = test_project_dir / repo_basename
    output_dir = args.output_dir.resolve()
    syntax_tree = (args.syntax_tree or PROJECT_ROOT / "syntax_trees" / f"{repo_basename}_syntax_tree.jsonl").resolve()
    staging_root = output_dir / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    try:
        repo, metadata_repo = _clone(args.repo, checkout)
        _ensure_syntax_tree(repo, syntax_tree)
        head_result = _run(["git", "rev-parse", "HEAD"], repo)
        if head_result.returncode != 0:
            raise RuntimeError(head_result.stderr or head_result.stdout)
        original_commit = head_result.stdout.strip()
        eligible = _eligible_candidates(
            repo,
            syntax_tree,
            min_out_degree=args.min_out_degree,
            min_consumers=args.min_consumers,
            mutation_operator=args.mutation_operator,
            min_downstream_dependencies=args.min_downstream_dependencies,
        )
        if args.list_candidates:
            listed = []
            for candidate, dependency_count in eligible:
                record = candidate.to_dict()
                record["downstream_dependency_count"] = dependency_count
                listed.append(record)
            print(json.dumps(listed, ensure_ascii=False, indent=2))
            return 0

        if not eligible:
            raise ValueError(
                "no function matches downstream dependency count >= "
                f"{args.min_downstream_dependencies}"
            )

        with tempfile.TemporaryDirectory(prefix="arkts-instance-", dir=staging_root) as temporary:
            legacy_root = Path(temporary)
            selected = eligible[:MAX_CODEX_CANDIDATES]
            if not args.skip_codex:
                try:
                    selected = _screen_candidates(
                        eligible,
                        repo=repo,
                        codex_cli=args.codex_cli,
                        tracks=legacy_root / "tracks" / "screening",
                        task_id=f"{repo_basename}_candidate_screening",
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    print(f"warning: Codex screening unavailable; using top {MAX_CODEX_CANDIDATES} candidates: {exc}", file=sys.stderr)
                    selected = eligible[:MAX_CODEX_CANDIDATES]
            for index, (candidate, dependency_count) in enumerate(selected):
                _reset_checkout(repo, original_commit)
                seed = (args.selection_seed or 0) + index
                target_name = candidate.function.qualified_name.replace(".", "_")
                instance_id = args.instance_id
                if len(selected) > 1 or instance_id is None:
                    instance_id = instance_id or f"{repo_basename}_{target_name}_{index}"
                    if args.instance_id and len(selected) > 1:
                        instance_id = f"{instance_id}_{index}"
                legacy = create_bug_instance(
                    repo,
                    syntax_tree,
                    output_dir=legacy_root,
                    min_out_degree=args.min_out_degree,
                    min_downstream_consumers=args.min_consumers,
                    instance_id=instance_id,
                    mutation_operator=args.mutation_operator,
                    selection_seed=seed,
                    candidate_identity=candidate.function.identity,
                )
                legacy_dir = legacy_root / legacy["instance_id"]
                error_patch = legacy_dir / "bug.patch"
                gold_patch = legacy_dir / "fix.patch"
                output_instance = output_dir / legacy["instance_id"]
                if output_instance.exists():
                    raise FileExistsError(f"instance already exists: {output_instance}")
                test_patch = ""
                instruction = _fallback_instruction(candidate)
                if not args.skip_codex:
                    # Each instance gets an isolated checkout state and task id.
                    tracks = legacy_root / "tracks" / legacy["instance_id"]
                    test_patch = _make_test_patch(repo, error_patch, codex_cli=args.codex_cli, tracks=tracks, task_id=legacy["instance_id"])
                    instruction = _make_instruction(repo, error_patch, candidate, codex_cli=args.codex_cli, tracks=tracks, task_id=legacy["instance_id"])
                output_instance.mkdir(parents=True)
                shutil.copy2(error_patch, output_instance / "error.patch")
                shutil.copy2(gold_patch, output_instance / "gold.patch")
                legacy["target"]["downstream_dependency_count"] = dependency_count
                metadata = _write_instance(legacy, instance_dir=output_instance, repo_name=metadata_repo, commit=legacy["source_repo"].get("commit"), instruction=instruction, test_patch=test_patch)
                metadata["downstream_dependency_count"] = dependency_count
                (output_instance / "instance.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"instance={legacy['instance_id']}")
                print(f"path={output_instance}")
            return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if checkout.exists():
            try:
                _remove_tree(checkout)
            except OSError as exc:
                print(f"warning: failed to remove checkout {checkout}: {exc}", file=sys.stderr)
        try:
            staging_root.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
