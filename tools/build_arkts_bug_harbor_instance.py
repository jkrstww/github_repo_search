"""生成 ArkTS 函数修复用的 Harbor 评测实例。

脚本用途：
    从指定 Git 仓库分析 ArkTS 函数及其依赖关系，选择排序靠前的候选函数，
    将每个候选函数替换为掩码实现，并为每个函数生成一个独立的 Harbor
    任务。任务包含待修复的仓库快照、错误补丁、标准答案补丁、回归测试和
    Docker 运行环境，供 Codex 或其他代理执行函数恢复评测。

基本用法：
    python tools/build_arkts_bug_harbor_instance.py --repo <仓库地址或本地路径>

主要输入：
    --repo：必填。GitHub owner/name、Git URL 或本地 Git 仓库路径。
    --syntax-tree：可选，已有的 ArkTS 语法树 JSONL 文件；省略时在 checkout
        根目录下生成。
    --output-dir：实例输出目录，默认是 harbor_instances/。
    --checkout-root：临时仓库 checkout 根目录，默认是 .tmp/arkts-harbor-checkouts/。
    --instance-id：实例 ID 前缀；脚本会追加 _0 和 _1。
    --skip-codex：跳过自动调用 Codex 生成回归测试，适合仅构建任务骨架。
    --list-candidates：只将符合筛选条件的候选函数以 JSON 输出到标准输出，
        不创建 Harbor 实例。
    --min-out-degree、--min-consumers、--min-downstream-dependencies、
    --mutation-operator：候选函数筛选条件。

输出：
    默认在 --output-dir 下创建两个实例目录（*_0、*_1）。每个目录包含
    instruction.md、instance.json、environment/、tests/ 和 solution/；其中
    environment/Dockerfile 构建掩码后的仓库，tests/ 保存验证脚本和测试补丁，
    solution/ 保存标准答案及其执行脚本。候选列表模式只输出 JSON，不写实例。

候选分析、排序、语法树生成和掩码规则复用
:mod:`tools.build_arkts_bug_mask_instance`；每个选中的函数都在独立 checkout
中处理，以避免不同 Harbor 实例之间相互影响。
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKOUT_ROOT = PROJECT_ROOT / ".tmp" / "arkts-harbor-checkouts"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "harbor_instances"
TEST_PATH = "tests/test_outputs.py"
INSTANCE_COUNT = 2

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from arkts_syntax_tree import DEFAULT_MUTATION_OPERATORS  # noqa: E402
from tools import build_arkts_bug_mask_instance as mask  # noqa: E402
from tools.track_agent import capture_patch, run_agent  # noqa: E402


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


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
    result = "".join(patch_lines)
    if not result:
        raise ValueError(f"empty patch for {relative_path}")
    return result if result.endswith("\n") else result + "\n"


def _mask_functions(repo: Path, functions: list[Any]) -> tuple[str, str]:
    """Mask all functions, processing each file bottom-up to preserve ranges.

    Syntax-tree catalogs may include both a function and nested functions such as
    a ``describe`` callback.  Replacing the nested range first changes the line
    numbers used by the enclosing function, so nested candidates are covered by
    masking their outermost candidate once.
    """
    originals: dict[str, str] = {}
    masked: dict[str, str] = {}
    for function in functions:
        if function.path not in originals:
            path = repo / function.path
            with path.open("r", encoding="utf-8", errors="replace", newline="") as source_file:
                originals[function.path] = source_file.read()
            masked[function.path] = originals[function.path]

    targets: list[Any] = []
    for function in functions:
        contained = any(
            other is not function
            and other.path == function.path
            and other.start_line <= function.start_line
            and function.end_line <= other.end_line
            and (other.start_line < function.start_line or function.end_line < other.end_line)
            for other in functions
        )
        if not contained:
            targets.append(function)

    for function in sorted(targets, key=lambda item: (item.path, -item.start_line)):
        masked_source, _ = mask._mask_function(masked[function.path], function)
        masked[function.path] = masked_source

    error_parts: list[str] = []
    gold_parts: list[str] = []
    for path in sorted(originals):
        error_parts.append(_make_patch(originals[path], masked[path], path))
        gold_parts.append(_make_patch(masked[path], originals[path], path))
        (repo / path).write_text(masked[path], encoding="utf-8", newline="")
    return "".join(error_parts), "".join(gold_parts)


def _reset_checkout(repo: Path, commit: str) -> None:
    """Restore a checkout to the original commit before building the next instance."""
    reset = _run(["git", "reset", "--hard", commit], repo)
    if reset.returncode:
        raise RuntimeError(reset.stderr or reset.stdout or "cannot reset checkout")
    clean = _run(["git", "clean", "-fd"], repo)
    if clean.returncode:
        raise RuntimeError(clean.stderr or clean.stdout or "cannot clean checkout")


def _fallback_test(functions: list[Any], originals: dict[str, str]) -> str:
    if len(functions) == 1 and functions[0].path.endswith("entry/src/ohosTest/ets/test/Ability.test.ets"):
        return _hypium_runtime_test(functions[0])
    del originals
    raise RuntimeError(
        "--skip-codex has no behavioral fallback for this function; "
        "run with Codex to generate a focused test"
    )


def _hypium_runtime_test(function: Any) -> str:
    """Exercise the generated Hypium registration function through a Node VM.

    The harness removes only the import/export syntax from this test-oriented
    ArkTS file and supplies small Hypium/Hilog doubles.  It then executes the
    exported function, so an empty or incomplete masked body fails for semantic
    reasons rather than because expected source text is missing.
    """
    del function
    return r'''import json
import subprocess
from pathlib import Path


def test_ability_registration_runtime():
    source_path = Path(__file__).resolve().parents[1] / "entry/src/ohosTest/ets/test/Ability.test.ets"
    source = source_path.read_text(encoding="utf-8")
    payload = json.dumps(source)
    script = r"""
const vm = require("vm");
const source = JSON.parse(process.argv[1])
  .replace(/^import[^\n]*\n/gm, "")
  .replace("export default function", "function");
const events = { suites: [], hooks: [], cases: [], logs: [], assertions: [] };
const context = {
  describe(name, callback) {
    const suite = { name, hooks: [], cases: [] };
    events.suites.push(suite);
    callback();
  },
  beforeAll(callback) { events.hooks.push("beforeAll"); callback(); },
  beforeEach(callback) { events.hooks.push("beforeEach"); callback(); },
  afterEach(callback) { events.hooks.push("afterEach"); callback(); },
  afterAll(callback) { events.hooks.push("afterAll"); callback(); },
  it(name, filter, callback) {
    events.cases.push({ name, filter });
    callback();
  },
  hilog: { info(...args) { events.logs.push(args); } },
  expect(value) {
    return {
      assertContain(other) {
        if (!String(value).includes(String(other))) throw new Error("assertContain failed");
        events.assertions.push("contain");
      },
      assertEqual(other) {
        if (value !== other) throw new Error("assertEqual failed");
        events.assertions.push("equal");
      },
    };
  },
  module: { exports: {} },
};
vm.runInNewContext(source + "\nmodule.exports = abilityTest;", context);
if (typeof context.module.exports !== "function") throw new Error("abilityTest was not exported");
context.module.exports();
if (events.suites.length !== 1 || events.suites[0].name !== "ActsAbilityTest") throw new Error("suite registration is incomplete");
if (JSON.stringify(events.hooks) !== JSON.stringify(["beforeAll", "beforeEach", "afterEach", "afterAll"])) throw new Error("lifecycle hooks are incomplete");
if (events.cases.length !== 1 || events.cases[0].name !== "assertContain" || events.cases[0].filter !== 0) throw new Error("assertion case is incomplete");
if (events.logs.length !== 1 || events.logs[0][0] !== 0x0000 || events.logs[0][1] !== "testTag") throw new Error("test logging is incomplete");
if (JSON.stringify(events.assertions) !== JSON.stringify(["contain", "equal"])) throw new Error("assertions were not executed");
"""
    result = subprocess.run(["node", "-e", script, payload], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
'''


def _test_prompt(functions: list[Any], originals: dict[str, str]) -> str:
    targets = "\n".join(
        f"- `{function.qualified_name}` in `{function.path}` (currently empty)"
        for function in functions
    )
    del originals
    return f"""This ArkTS repository has these deliberately masked functions:\n{targets}\n\nCreate a focused deterministic test file at exactly `{TEST_PATH}`. The test must execute the target function and verify its observable behavior, not compare the complete expected source text or merely check that a body is non-empty. Do not copy the missing production implementation into the test. For the Hypium ability test, use a Node.js VM harness with mocks for describe, beforeAll, beforeEach, afterEach, afterAll, it, expect, and hilog, then assert the registered suite, lifecycle hooks, test case, log call, and assertion calls. Use only Python's standard library plus pytest and Node.js already available in the environment. Only create or modify `{TEST_PATH}`.\n"""


def _changed_paths(repo: Path) -> set[str]:
    diff = _run(["git", "diff", "--name-only", "HEAD", "--"], repo)
    others = _run(["git", "ls-files", "--others", "--exclude-standard"], repo)
    if diff.returncode or others.returncode:
        raise RuntimeError("cannot inspect Codex changes")
    return {line.strip().replace("\\", "/") for line in (diff.stdout + others.stdout).splitlines() if line.strip()}


def _generate_test(repo: Path, functions: list[Any], originals: dict[str, str], args: argparse.Namespace, track_dir: Path, task_id: str) -> str:
    if args.skip_codex:
        test = repo / TEST_PATH
        test.parent.mkdir(parents=True, exist_ok=True)
        test.write_text(_fallback_test(functions, originals), encoding="utf-8")
    else:
        _, code = run_agent(
            workspace=repo,
            agent=args.codex_cli,
            model=args.model,
            task_id=f"{task_id}_tests",
            output_dir=track_dir,
            prompt=_test_prompt(functions, originals),
            extra_args=["--sandbox", args.codex_sandbox],
        )
        if code != 0:
            raise RuntimeError("Codex test generation failed")
    if _changed_paths(repo) != {TEST_PATH}:
        raise RuntimeError(f"Codex must change only {TEST_PATH}")
    patch = capture_patch(repo)
    if not patch.strip():
        raise RuntimeError("Codex produced an empty test patch")
    return patch


def _apply_patch(repo: Path, patch: str) -> None:
    patch_file = repo.parent / ".arkts-gold.patch"
    patch_file.write_text(patch, encoding="utf-8", newline="")
    try:
        check = _run(["git", "apply", "--check", str(patch_file)], repo)
        if check.returncode:
            raise RuntimeError(check.stderr or check.stdout or "gold patch does not apply")
        applied = _run(["git", "apply", str(patch_file)], repo)
        if applied.returncode:
            raise RuntimeError(applied.stderr or applied.stdout or "gold patch failed")
    finally:
        patch_file.unlink(missing_ok=True)


def _verify_test(repo: Path, gold_patch: str) -> None:
    masked = _run([sys.executable, "-m", "pytest", "-q", TEST_PATH], repo)
    if masked.returncode == 0:
        raise RuntimeError("generated test passes against masked functions")
    _apply_patch(repo, gold_patch)
    restored = _run([sys.executable, "-m", "pytest", "-q", TEST_PATH], repo)
    if restored.returncode:
        raise RuntimeError((restored.stdout + restored.stderr).strip() or "test failed after restoration")


def _shell_patch(patch: str) -> str:
    return "#!/bin/bash\nset -euo pipefail\ncd \"$(git rev-parse --show-toplevel)\"\npatch -p1 <<'__ARKTS_GOLD_PATCH__'\n" + patch + "__ARKTS_GOLD_PATCH__\n"


def _dockerfile(repo_url: str, commit: str) -> str:
    safe_url = repo_url.replace("\\", "\\\\").replace('"', '\\"')
    return f"""FROM node:20-slim AS masked-repo
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git patch \\
    && rm -rf /var/lib/apt/lists/*
RUN git clone {safe_url!r} /workspace/repo && cd /workspace/repo && git checkout {commit}
COPY error.patch /tmp/error.patch
RUN cd /workspace/repo \\
    && git apply /tmp/error.patch \\
    && rm -f /tmp/error.patch \\
    && rm -rf .git \\
    && git init --initial-branch=masked \\
    && git config user.name 'ArkTS Benchmark' \\
    && git config user.email 'benchmark@localhost' \\
    && git add -A \\
    && git commit -m 'Masked task baseline'

FROM node:20-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git patch ripgrep python3 python3-pytest \\
    && ln -s /usr/bin/python3 /usr/local/bin/python \\
    && npm install -g @openai/codex \\
    && rm -rf /var/lib/apt/lists/*
COPY --from=masked-repo /workspace/repo /workspace/repo
WORKDIR /workspace/repo
"""


def _docker_compose() -> str:
    return """services:
  main:
    networks:
      - agent
    depends_on:
      - model-gateway
  model-gateway:
    image: node:20-slim
    environment:
      MODEL_ENDPOINT_HOST: "${ARKTS_MODEL_ENDPOINT_HOST:-api.openai.com}"
    command:
      - node
      - -e
      - |
        const http = require("http");
        const https = require("https");
        const upstreamHost = process.env.MODEL_ENDPOINT_HOST;
        http.createServer((request, response) => {
          const headers = { ...request.headers, host: upstreamHost };
          const upstream = https.request({
            hostname: upstreamHost,
            port: 443,
            method: request.method,
            path: request.url,
            headers,
          }, upstreamResponse => {
            response.writeHead(upstreamResponse.statusCode, upstreamResponse.headers);
            upstreamResponse.pipe(response);
          });
          upstream.on("error", error => {
            response.writeHead(502, { "content-type": "text/plain" });
            response.end(error.message);
          });
          request.pipe(upstream);
        }).listen(8080, "0.0.0.0");
    networks:
      - agent
      - outbound
networks:
  agent:
    internal: true
  outbound: {}
"""


def _key_words(
    function: Any,
    graph: dict[str, set[str]],
    catalog: dict[str, Any],
) -> list[str]:
    """Return the target's immediate repository dependents and dependencies."""
    direct_dependents = sorted(
        caller
        for caller, callees in graph.items()
        if function.identity in callees
    )
    pre_mask_dependencies = sorted(graph.get(function.identity, set()))
    return list(
        dict.fromkeys(
            catalog[identity].qualified_name
            for identity in [*direct_dependents, *pre_mask_dependencies]
            if identity in catalog and identity != function.identity
        )
    )


def _write_instance(destination: Path, *, repo_url: str, commit: str, metadata_repo: str, functions: list[Any], key_words: list[str], error_patch: str, gold_patch: str, test_patch: str, test_source: str) -> None:
    destination.mkdir(parents=True)
    instruction = (
        "Restore the complete implementation of the following ArkTS function. Preserve its public declaration and surrounding code, and make all tests pass:\n\n"
        + "\n".join(f"- `{item.qualified_name}` in `{item.path}`" for item in functions)
        + "\n\n"
        + "## Restrictions\n\n"
        + "- Do not use external network or remote Git resources to search for the solution. This includes git clone, git fetch, git pull, git remote, GitHub/GitLab APIs, web search, curl, wget, Python/Node HTTP requests, and any equivalent tool or command.\n"
        + "- Do not inspect benchmark answer artifacts or agent/session logs outside the repository working tree, including error.patch, gold.patch, /tmp, or Codex session files. Recover the implementation only from the checked-out repository, its local history, and the task context.\n"
    )
    (destination / "instruction.md").write_text(instruction, encoding="utf-8")
    (destination / "error.patch").write_text(error_patch, encoding="utf-8", newline="")
    (destination / "tests").mkdir()
    (destination / "tests" / "test_outputs.py").write_text(test_source, encoding="utf-8")
    (destination / "tests" / "test.patch").write_text(test_patch, encoding="utf-8", newline="")
    (destination / "tests" / "f2p_patch.diff").write_text(test_patch, encoding="utf-8", newline="")
    (destination / "solution").mkdir()
    (destination / "solution" / "gold.patch").write_text(gold_patch, encoding="utf-8", newline="")
    (destination / "solution" / "gold_patch.diff").write_text(gold_patch, encoding="utf-8", newline="")
    (destination / "solution" / "solve.sh").write_text(_shell_patch(gold_patch), encoding="utf-8", newline="")
    (destination / "solution" / "solve.sh").chmod(0o755)
    (destination / "environment").mkdir()
    (destination / "environment" / "Dockerfile").write_text(_dockerfile(repo_url, commit), encoding="utf-8")
    (destination / "environment" / "docker-compose.yaml").write_text(_docker_compose(), encoding="utf-8")
    (destination / "environment" / "error.patch").write_text(error_patch, encoding="utf-8", newline="")
    (destination / "tests" / "test.sh").write_text(
        "#!/bin/bash\nset -uo pipefail\ncd /workspace/repo\ngit apply --check /tests/f2p_patch.diff && git apply /tests/f2p_patch.diff\npython -m pytest -q tests/test_outputs.py\ncode=$?\nmkdir -p /logs/verifier\nif [ $code -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi\nexit 0\n",
        encoding="utf-8",
    )
    (destination / "tests" / "test.sh").chmod(0o755)
    (destination / "task.toml").write_text(
        'version = "1.0"\n\n[metadata]\nauthor_name = "ArkTS Benchmark"\ndifficulty = "medium"\ncategory = "debugging"\ntags = ["arkts", "function-restoration"]\n\n[verifier]\ntimeout_sec = 900.0\n\n[agent]\ntimeout_sec = 3600.0\n\n[environment]\nbuild_timeout_sec = 900.0\nnetwork_mode = "public"\n',
        encoding="utf-8",
    )
    metadata = {
        "schema_version": 1,
        "task_type": "arkts_function_restore",
        "repo": metadata_repo,
        "commit": commit,
        "functions": [item.to_dict() for item in functions],
        "key_words": key_words,
        "patches": {"error": "error.patch", "gold": "solution/gold_patch.diff", "test": "tests/f2p_patch.diff"},
    }
    (destination / "instance.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build two Harbor ArkTS function-restoration tasks")
    parser.add_argument("--repo", required=True, help="GitHub owner/name, URL, or local Git repository")
    parser.add_argument("--syntax-tree", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--checkout-root", type=Path, default=DEFAULT_CHECKOUT_ROOT)
    parser.add_argument("--instance-id", help="base instance id; _0 and _1 are appended")
    parser.add_argument("--codex-cli", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--codex-sandbox", choices=["read-only", "workspace-write", "danger-full-access"], default="workspace-write")
    parser.add_argument("--min-out-degree", type=mask.positive_int, default=1)
    parser.add_argument("--min-consumers", type=mask.nonnegative_int, default=0)
    parser.add_argument("--min-downstream-dependencies", type=mask.positive_int, default=1)
    parser.add_argument("--mutation-operator", choices=DEFAULT_MUTATION_OPERATORS)
    parser.add_argument("--list-candidates", action="store_true", help="print the ordered eligible functions and exit")
    parser.add_argument("--skip-codex", action="store_true")
    parser.add_argument("--keep-checkout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _, repo_name, metadata_repo = mask._repo_details(args.repo)
    checkout = args.checkout_root.resolve() / repo_name
    syntax_tree = (args.syntax_tree or args.checkout_root.resolve() / f"{repo_name}_syntax_tree.jsonl").resolve()
    try:
        repo, metadata_repo = mask._clone(args.repo, checkout)
        if args.syntax_tree is None:
            syntax_tree.unlink(missing_ok=True)
        mask._ensure_syntax_tree(repo, syntax_tree)
        head = _run(["git", "rev-parse", "HEAD"], repo)
        if head.returncode:
            raise RuntimeError(head.stderr or head.stdout)
        commit = head.stdout.strip()
        eligible, graph, catalog = mask._eligible_candidates(repo, syntax_tree, min_out_degree=args.min_out_degree, min_consumers=args.min_consumers, mutation_operator=args.mutation_operator, min_downstream_dependencies=args.min_downstream_dependencies)
        if args.list_candidates:
            print(json.dumps([
                candidate.to_dict()
                | {
                    "downstream_dependency_count": dependency_count,
                    "key_words": _key_words(candidate.function, graph, catalog),
                }
                for candidate, dependency_count in eligible
            ], ensure_ascii=False, indent=2))
            return 0
        if len(eligible) < INSTANCE_COUNT:
            raise ValueError(f"expected at least {INSTANCE_COUNT} eligible functions, found {len(eligible)}")
        args.output_dir.resolve().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="arkts-harbor-", dir=args.output_dir.resolve()) as temp:
            base_id = args.instance_id or f"{repo_name}_arkts_masked_functions"
            output_url, _, _ = mask._repo_details(args.repo)
            for index, (candidate, _) in enumerate(eligible[:INSTANCE_COUNT]):
                function = candidate.function
                instance_id = f"{base_id}_{index}"
                destination = args.output_dir.resolve() / instance_id
                if destination.exists():
                    raise FileExistsError(f"instance already exists: {destination}")

                _reset_checkout(repo, commit)
                target_path = repo / function.path
                with target_path.open("r", encoding="utf-8", errors="replace", newline="") as source_file:
                    original_source = source_file.read()
                masked_source, _ = mask._mask_function(original_source, function)
                error_patch = _make_patch(original_source, masked_source, function.path)
                gold_patch = _make_patch(masked_source, original_source, function.path)
                target_path.write_text(masked_source, encoding="utf-8", newline="")
                mask._checkpoint_mask(repo)

                originals = {function.path: original_source}
                test_patch = _generate_test(
                    repo, [function], originals, args, Path(temp) / "tracks", instance_id
                )
                _verify_test(repo, gold_patch)
                generated_test = repo / TEST_PATH
                if not generated_test.is_file():
                    raise RuntimeError(f"Codex did not create {TEST_PATH}")
                test_source = generated_test.read_text(encoding="utf-8")
                _write_instance(
                    destination,
                    repo_url=output_url,
                    commit=commit,
                    metadata_repo=metadata_repo,
                    functions=[function],
                    key_words=_key_words(function, graph, catalog),
                    error_patch=error_patch,
                    gold_patch=gold_patch,
                    test_patch=test_patch,
                    test_source=test_source,
                )
                print(f"instance={instance_id}\npath={destination}")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if checkout.exists() and not args.keep_checkout:
            mask._remove_tree(checkout)


if __name__ == "__main__":
    raise SystemExit(main())
