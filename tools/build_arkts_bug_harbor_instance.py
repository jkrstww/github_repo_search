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
    --list-candidates：只将符合筛选条件的候选函数以 JSON 输出到标准输出，
        不创建 Harbor 实例。
    --min-upstream-direct-call-count：候选函数复杂度筛选条件。

语法树来源与作用：
    --syntax-tree 不是第二个仓库，而是语法树索引 JSONL 文件的路径。脚本先
    将 --repo 准备到本地 checkout，再使用本地源码生成或读取该索引；语法树
    用于展开函数目录、分析导入和调用关系，并筛选高影响候选函数。
    --repo 为远程地址时，仓库准备阶段执行 git clone 并需要相应网络；为本地
    Git 仓库时也会复制到临时 checkout，避免修改原目录。语法树本身不通过
    GitHub/Git API 获取，也不访问网络，而是递归读取 checkout 中的 .ets 和
    .ts 文件，由项目内置的轻量解析器提取文件、类、函数、方法、属性、导入、
    调用、签名、行号和嵌套关系等信息。
    未指定 --syntax-tree 时，脚本在 checkout 根目录生成
    <repo-name>_syntax_tree.jsonl 及对应的汇总 JSON，并会先删除同名旧文件以
    避免使用过期结果；显式指定且文件已存在时直接复用，因此应确保它与当前
    checkout 的仓库版本匹配。

输出：
    默认在 --output-dir 下创建两个实例目录（*_0、*_1）。每个目录包含
    instruction.md、instance.json、environment/、tests/ 和 solution/；其中
    environment/Dockerfile 构建掩码后的仓库，tests/ 保存验证脚本和测试补丁，
    solution/ 保存标准答案及其执行脚本。候选列表模式只输出 JSON，不写实例。

候选分析由 :mod:`tools.filter_complex_arkts_functions` 提供；仓库准备、语法树
生成和函数掩码均在本脚本内独立实现。每个选中的函数都在独立 checkout 中
处理，以避免不同 Harbor 实例之间相互影响。

执行顺序：

    main
      -> _prepare_repository       # clone、语法树、提交号
      -> _select_candidates        # 基础候选 + 调用图筛选
      -> _build_instances          # 选择前两个候选
           -> _build_instance      # 每个候选的完整构建流程
                -> _generate_test  # Codex 生成测试
                -> _verify_test    # 掩码失败、标准答案通过
                -> _write_instance # 写入 Harbor 文件树
      -> finally: 删除临时 checkout（除非 --keep-checkout）
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKOUT_ROOT = PROJECT_ROOT / ".tmp" / "arkts-harbor-checkouts"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "harbor_instances"
TEST_PATH = "tests/test_outputs.py"
INSTANCE_COUNT = 2

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from arkts_syntax_tree import parse_repository, write_syntax_tree_outputs  # noqa: E402
from tools.filter_complex_arkts_functions import find_complex_function_candidates  # noqa: E402
from tools.track_agent import capture_patch, run_agent  # noqa: E402


@dataclass(frozen=True)
class InstanceArtifacts:
    """Generated content that is persisted in one Harbor instance."""

    error_patch: str
    gold_patch: str
    test_patch: str
    test_source: str


@dataclass(frozen=True)
class RepositoryContext:
    """仓库准备阶段产出的共享上下文，供筛选和实例构建复用。"""

    repo: Path
    checkout: Path
    repo_url: str
    repo_name: str
    metadata_repo: str
    commit: str
    syntax_tree: Path


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """在指定 checkout 中执行命令，并始终捕获其输出供上层报错使用。"""
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def positive_int(value: str) -> int:
    """将命令行参数解析为正整数。"""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _repo_details(repo: str) -> tuple[str, str, str]:
    """规范化本地路径、Git URL 或 owner/name，并给出元数据仓库名。"""
    raw = repo.strip().rstrip("/")
    if not raw:
        raise ValueError("repository name must not be empty")
    local = Path(raw).expanduser()
    if local.is_dir() and (local / ".git").is_dir():
        resolved = local.resolve()
        remote = _run(["git", "config", "--get", "remote.origin.url"], resolved).stdout.strip()
        metadata = remote.removesuffix(".git").split("github.com/")[-1] if "github.com/" in remote else resolved.name
        return str(resolved), resolved.name, metadata
    if raw.startswith("git@") or "://" in raw:
        return raw, Path(raw.rsplit("/", 1)[-1]).stem or "repository", raw.removesuffix(".git")
    normalized = raw.removesuffix(".git")
    return f"https://github.com/{normalized}.git", normalized.rsplit("/", 1)[-1], normalized


def _remove_tree(path: Path) -> None:
    """删除临时 checkout，并处理 Git 产生的只读文件。"""
    def retry(function: Any, name: str, error: Any) -> None:
        """将失败目标设为可写后重试 shutil 的删除操作。"""
        del error
        os.chmod(name, stat.S_IWRITE)
        function(name)
    shutil.rmtree(path, onerror=retry)


def _clone(repo: str, destination: Path) -> tuple[Path, str]:
    """将仓库 clone 到专用临时目录。"""
    url, _, metadata_repo = _repo_details(repo)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        _remove_tree(destination)
    result = _run(["git", "clone", url, str(destination)], PROJECT_ROOT)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or "git clone failed")
    return destination, metadata_repo


def _ensure_syntax_tree(repo: Path, path: Path) -> None:
    """生成缺失的 ArkTS 语法树 JSONL。"""
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    parsed = parse_repository(repo, extensions=[".ets", ".ts"])
    write_syntax_tree_outputs(parsed, output_path=path, summary_path=path.with_name(path.stem + "_summary.json"))


def _checkpoint_mask(repo: Path) -> None:
    """提交掩码后的代码，作为 Codex 创建测试时的 Git 基线。"""
    add = _run(["git", "add", "-A"], repo)
    commit = _run(["git", "commit", "-m", "Masked task baseline"], repo)
    if add.returncode or commit.returncode:
        raise RuntimeError(add.stderr or add.stdout or commit.stderr or commit.stdout or "cannot checkpoint masked checkout")


def _mask_function(source: str, function: Any) -> str:
    """清空目标函数体，同时保留声明、缩进和行尾风格。"""
    lines = source.splitlines(keepends=True)
    start, end = function.start_line - 1, function.end_line - 1
    if start < 0 or end >= len(lines) or end < start:
        raise ValueError(f"invalid source range for {function.identity}")
    first = lines[start]
    open_brace = first.find("{")
    if open_brace < 0:
        raise ValueError(f"function declaration has no opening brace: {function.identity}")
    ending = "\r\n" if first.endswith("\r\n") else "\n"
    declaration = first[:open_brace + 1].rstrip("\r\n")
    if start == end:
        replacement = [declaration + "}" + ending]
    else:
        suffix = lines[end][lines[end].rfind("}"):] if "}" in lines[end] else "}" + ending
        replacement = [declaration + ending, re.match(r"\s*", first).group(0) + suffix.lstrip()]
    masked = "".join(lines[:start] + replacement + lines[end + 1:])
    if masked == source:
        raise ValueError(f"mask did not change {function.identity}")
    return masked


def _make_patch(before: str, after: str, relative_path: str) -> str:
    """为单个仓库内文件生成带 Git 路径前缀的统一 diff。"""
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


def _read_source(path: Path) -> str:
    """读取源码且保留原始换行符，避免补丁行号或内容发生意外变化。"""
    with path.open("r", encoding="utf-8", errors="replace", newline="") as source_file:
        return source_file.read()


def _reset_checkout(repo: Path, commit: str) -> None:
    """在构建下一个实例前，将共享 checkout 恢复到指定提交且清理未跟踪文件。"""
    reset = _run(["git", "reset", "--hard", commit], repo)
    if reset.returncode:
        raise RuntimeError(reset.stderr or reset.stdout or "cannot reset checkout")
    clean = _run(["git", "clean", "-fd"], repo)
    if clean.returncode:
        raise RuntimeError(clean.stderr or clean.stdout or "cannot clean checkout")


def _test_prompt(function: Any) -> str:
    """构造发给 Codex 的单目标回归测试生成指令。"""
    target = f"- `{function.qualified_name}` in `{function.path}` (currently empty)"
    return f"""This ArkTS repository has this deliberately masked function:
{target}

Create a focused deterministic test file at exactly `{TEST_PATH}`. The test must
execute the target function and verify its observable behavior, not compare the
complete expected source text or merely check that a body is non-empty. Do not
copy the missing production implementation into the test — the test must FAIL
against the masked, empty version and only PASS once the real implementation is
restored.

For the Hypium ability test, use a Node.js VM harness with mocks for describe,
beforeAll, beforeEach, afterEach, afterAll, it, expect, and hilog, then assert
the registered suite, lifecycle hooks, test case, log call, and assertion calls.
Use only Python's standard library plus pytest and Node.js already available in
the environment.

Write the test into `{TEST_PATH}`; any extra helper modules, stubs, or fixtures
should also live under `tests/` and stay self-contained (no network access).
These two hard rules must hold regardless of approach:
1. Do NOT edit, restore, or otherwise change `{function.path}` — the masked
   function's own source file. You may read it read-only to understand its shape
   and signature, but leave it exactly as the masked baseline leaves it.
2. Do NOT modify other production source code to make the test pass; drive the
   masked behavior through the public API as-is. Existing test entry files under
   `entry/src/ohosTest/` may be read or minimally adjusted only if strictly needed
   to execute the VM harness.
"""


def _changed_paths(repo: Path) -> set[str]:
    """返回相对 HEAD 有改动或新增的所有未忽略路径。"""
    diff = _run(["git", "diff", "--name-only", "HEAD", "--"], repo)
    others = _run(["git", "ls-files", "--others", "--exclude-standard"], repo)
    if diff.returncode or others.returncode:
        raise RuntimeError("cannot inspect Codex changes")
    return {line.strip().replace("\\", "/") for line in (diff.stdout + others.stdout).splitlines() if line.strip()}


def _generate_test(
    repo: Path,
    function: Any,
    args: argparse.Namespace,
    track_dir: Path,
    task_id: str,
) -> str:
    """调用 Codex 生成测试，并强制其只修改 ``TEST_PATH``。"""
    _, code = run_agent(
        workspace=repo,
        agent=args.codex_cli,
        model=args.model,
        task_id=f"{task_id}_tests",
        output_dir=track_dir,
        prompt=_test_prompt(function),
        extra_args=["--sandbox", args.codex_sandbox],
    )
    if code != 0:
        raise RuntimeError("Codex test generation failed")
    changed_paths = _changed_paths(repo)
    # The masked function's own source file is off-limits: editing it would
    # collide with the gold patch at eval time (both rewrite the masked body) and
    # risks leaking the solution through a partial stub. Beyond that restriction
    # codex is free to add helper files (placed under tests/) so it can run the
    # harness without being artificially limited to a single test file.
    if function.path in changed_paths:
        raise RuntimeError(
            f"Codex modified the masked function's source file: {function.path}"
        )
    patch = capture_patch(repo)
    if not patch.strip():
        raise RuntimeError("Codex produced an empty test patch")
    return patch


def _apply_patch(repo: Path, patch: str) -> None:
    """先校验、再应用标准答案补丁；无论结果如何都删除临时补丁文件。"""
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
    """验证测试在掩码代码上失败、在恢复标准答案后通过。"""
    masked = _run([sys.executable, "-m", "pytest", "-q", TEST_PATH], repo)
    if masked.returncode == 0:
        raise RuntimeError("generated test passes against masked functions")
    _apply_patch(repo, gold_patch)
    restored = _run([sys.executable, "-m", "pytest", "-q", TEST_PATH], repo)
    if restored.returncode:
        raise RuntimeError((restored.stdout + restored.stderr).strip() or "test failed after restoration")


def _shell_patch(patch: str) -> str:
    """生成在 Harbor 容器内应用标准答案补丁的 shell 脚本。"""
    return "#!/bin/bash\nset -euo pipefail\ncd \"$(git rev-parse --show-toplevel)\"\npatch -p1 <<'__ARKTS_GOLD_PATCH__'\n" + patch + "__ARKTS_GOLD_PATCH__\n"


def _dockerfile(repo_url: str, commit: str) -> str:
    """生成包含掩码仓库和运行测试依赖的双阶段 Dockerfile。"""
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
    """生成 Harbor 所需的最小 Compose 服务定义。"""
    return """services:
  main: {}
"""


def _key_words(
    function: Any,
    graph: dict[str, set[str]],
    catalog: dict[str, Any],
) -> list[str]:
    """返回目标函数的直接调用方和被调用方名称，供实例元数据检索使用。"""
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


def _write_instance(
    destination: Path,
    *,
    repo_url: str,
    commit: str,
    metadata_repo: str,
    function: Any,
    key_words: list[str],
    artifacts: InstanceArtifacts,
) -> None:
    """将任务说明、补丁、测试、环境定义和元数据写入实例目录。"""
    destination.mkdir(parents=True)
    instruction = (
        "Restore the complete implementation of the following ArkTS function. Preserve its public declaration and surrounding code, and make all tests pass:\n\n"
        + f"- `{function.qualified_name}` in `{function.path}`"
        + "\n\n"
        + "## Restrictions\n\n"
        + "- Do not use external network or remote Git resources to search for the solution. This includes git clone, git fetch, git pull, git remote, GitHub/GitLab APIs, web search, curl, wget, Python/Node HTTP requests, and any equivalent tool or command.\n"
        + "- Do not inspect benchmark answer artifacts or agent/session logs outside the repository working tree, including error.patch, gold.patch, /tmp, or Codex session files. Recover the implementation only from the checked-out repository, its local history, and the task context.\n"
    )
    (destination / "instruction.md").write_text(instruction, encoding="utf-8")
    (destination / "error.patch").write_text(artifacts.error_patch, encoding="utf-8", newline="")
    (destination / "tests").mkdir()
    (destination / "tests" / "test_outputs.py").write_text(artifacts.test_source, encoding="utf-8")
    (destination / "tests" / "test.patch").write_text(artifacts.test_patch, encoding="utf-8", newline="")
    (destination / "tests" / "f2p_patch.diff").write_text(artifacts.test_patch, encoding="utf-8", newline="")
    (destination / "solution").mkdir()
    (destination / "solution" / "gold.patch").write_text(artifacts.gold_patch, encoding="utf-8", newline="")
    (destination / "solution" / "gold_patch.diff").write_text(artifacts.gold_patch, encoding="utf-8", newline="")
    (destination / "solution" / "solve.sh").write_text(_shell_patch(artifacts.gold_patch), encoding="utf-8", newline="")
    (destination / "solution" / "solve.sh").chmod(0o755)
    (destination / "environment").mkdir()
    (destination / "environment" / "Dockerfile").write_text(_dockerfile(repo_url, commit), encoding="utf-8")
    (destination / "environment" / "docker-compose.yaml").write_text(_docker_compose(), encoding="utf-8")
    (destination / "environment" / "error.patch").write_text(artifacts.error_patch, encoding="utf-8", newline="")
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
        "functions": [function.to_dict()],
        "key_words": key_words,
        "patches": {"error": "error.patch", "gold": "solution/gold_patch.diff", "test": "tests/f2p_patch.diff"},
    }
    (destination / "instance.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _candidate_records(
    eligible: list[tuple[Any, int]],
    graph: dict[str, set[str]],
    catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    """序列化候选函数详情，作为 ``--list-candidates`` 的 JSON 输出。"""
    return [
        candidate.to_dict()
        | {
            "upstream_direct_call_count": dependency_count,
            "key_words": _key_words(candidate.function, graph, catalog),
        }
        for candidate, dependency_count in eligible
    ]


def _build_instance(
    repo: Path,
    *,
    candidate: Any,
    destination: Path,
    instance_id: str,
    commit: str,
    repo_url: str,
    metadata_repo: str,
    graph: dict[str, set[str]],
    catalog: dict[str, Any],
    args: argparse.Namespace,
    track_dir: Path,
) -> None:
    """掩码一个函数、生成并验证其测试，再写入完整 Harbor 实例。"""
    function = candidate.function
    _reset_checkout(repo, commit)

    target_path = repo / function.path
    original_source = _read_source(target_path)
    masked_source = _mask_function(original_source, function)
    error_patch = _make_patch(original_source, masked_source, function.path)
    gold_patch = _make_patch(masked_source, original_source, function.path)
    target_path.write_text(masked_source, encoding="utf-8", newline="")
    _checkpoint_mask(repo)

    test_patch = _generate_test(repo, function, args, track_dir, instance_id)
    _verify_test(repo, gold_patch)
    generated_test = repo / TEST_PATH
    if not generated_test.is_file():
        raise RuntimeError(f"Codex did not create {TEST_PATH}")

    _write_instance(
        destination,
        repo_url=repo_url,
        commit=commit,
        metadata_repo=metadata_repo,
        function=function,
        key_words=_key_words(function, graph, catalog),
        artifacts=InstanceArtifacts(
            error_patch=error_patch,
            gold_patch=gold_patch,
            test_patch=test_patch,
            test_source=generated_test.read_text(encoding="utf-8"),
        ),
    )


# ---------------------------------------------------------------------------
# Top-level orchestration: repository -> candidates -> Harbor instances
# ---------------------------------------------------------------------------

def _prepare_repository(
    args: argparse.Namespace,
    *,
    repo_url: str,
    repo_name: str,
    checkout: Path,
) -> RepositoryContext:
    """克隆仓库、生成或读取语法树，并记录本次构建使用的提交号。"""
    syntax_tree = (
        args.syntax_tree
        or args.checkout_root.resolve() / f"{repo_name}_syntax_tree.jsonl"
    ).resolve()
    repo, metadata_repo = _clone(args.repo, checkout)
    if args.syntax_tree is None:
        syntax_tree.unlink(missing_ok=True)
    _ensure_syntax_tree(repo, syntax_tree)

    head = _run(["git", "rev-parse", "HEAD"], repo)
    if head.returncode:
        raise RuntimeError(head.stderr or head.stdout or "cannot read repository HEAD")
    return RepositoryContext(
        repo=repo,
        checkout=checkout,
        repo_url=repo_url,
        repo_name=repo_name,
        metadata_repo=metadata_repo,
        commit=head.stdout.strip(),
        syntax_tree=syntax_tree,
    )


def _select_candidates(
    context: RepositoryContext,
    args: argparse.Namespace,
) -> tuple[list[tuple[Any, int]], dict[str, set[str]], dict[str, Any]]:
    """按 CLI 阈值筛选候选函数，并返回它们的调用图和函数目录。"""
    candidates, graph, catalog = find_complex_function_candidates(
        context.repo,
        context.syntax_tree,
        min_upstream_direct_call_count=args.min_upstream_direct_call_count,
    )
    return [
        (candidate, candidate.upstream_direct_call_count)
        for candidate in candidates
    ], graph, catalog


def _build_instances(
    context: RepositoryContext,
    eligible: list[tuple[Any, int]],
    graph: dict[str, set[str]],
    catalog: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    """使用排序靠前的两个候选函数，分别构造独立的 Harbor 实例。"""
    if len(eligible) < INSTANCE_COUNT:
        raise ValueError(
            f"expected at least {INSTANCE_COUNT} eligible functions, found {len(eligible)}"
        )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    base_id = args.instance_id or f"{context.repo_name}_arkts_masked_functions"
    with tempfile.TemporaryDirectory(prefix="arkts-harbor-", dir=output_dir) as temp:
        track_dir = Path(temp) / "tracks"
        for index, (candidate, _) in enumerate(eligible[:INSTANCE_COUNT]):
            instance_id = f"{base_id}_{index}"
            destination = output_dir / instance_id
            if destination.exists():
                raise FileExistsError(f"instance already exists: {destination}")
            _build_instance(
                context.repo,
                candidate=candidate,
                destination=destination,
                instance_id=instance_id,
                commit=context.commit,
                repo_url=context.repo_url,
                metadata_repo=context.metadata_repo,
                graph=graph,
                catalog=catalog,
                args=args,
                track_dir=track_dir,
            )
            print(f"instance={instance_id}\npath={destination}")


def build_parser() -> argparse.ArgumentParser:
    """定义 Harbor 单仓库实例构造器的命令行参数。"""
    parser = argparse.ArgumentParser(description="Build two Harbor ArkTS function-restoration tasks")
    parser.add_argument("--repo", required=True, help="GitHub owner/name, URL, or local Git repository")
    parser.add_argument("--syntax-tree", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--checkout-root", type=Path, default=DEFAULT_CHECKOUT_ROOT)
    parser.add_argument("--instance-id", help="base instance id; _0 and _1 are appended")
    parser.add_argument("--codex-cli", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--codex-sandbox", choices=["read-only", "workspace-write", "danger-full-access"], default="workspace-write")
    parser.add_argument("--min-upstream-direct-call-count", type=positive_int, default=5)
    parser.add_argument("--list-candidates", action="store_true", help="print the ordered eligible functions and exit")
    parser.add_argument("--keep-checkout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """按“准备仓库 → 筛选候选 → 构建实例”的顺序执行整个命令。"""
    args = build_parser().parse_args(argv)
    repo_url, repo_name, _ = _repo_details(args.repo)
    checkout = args.checkout_root.resolve() / repo_name
    try:
        context = _prepare_repository(
            args,
            repo_url=repo_url,
            repo_name=repo_name,
            checkout=checkout,
        )
        eligible, graph, catalog = _select_candidates(context, args)
        if args.list_candidates:
            print(json.dumps(_candidate_records(eligible, graph, catalog), ensure_ascii=False, indent=2))
            return 0
        _build_instances(context, eligible, graph, catalog, args)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if checkout.exists() and not args.keep_checkout:
            _remove_tree(checkout)


if __name__ == "__main__":
    raise SystemExit(main())
