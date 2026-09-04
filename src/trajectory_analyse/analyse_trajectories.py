#!/usr/bin/env python3
"""Use Codex to analyze success/failure differences between trajectories."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


def load_sample_labels(label_path: Path, sample: str) -> dict[str, Any]:
    """Load the ``resolved`` mapping for one sample from ``compare.json``."""
    if not label_path.is_file():
        raise FileNotFoundError(f"Label file does not exist: {label_path}")
    payload = json.loads(label_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {label_path}")
    labels = payload.get(sample)
    if not isinstance(labels, dict):
        raise KeyError(f"Sample {sample!r} is not present in {label_path}")
    return labels


def find_trajectories(trajectories_dir: Path) -> list[Path]:
    """Find JSON trajectory files below the requested sample directory."""
    if not trajectories_dir.is_dir():
        raise ValueError(f"Trajectories directory does not exist: {trajectories_dir}")
    files = sorted(path for path in trajectories_dir.rglob("*.json") if path.is_file())
    if not files:
        raise ValueError(f"No JSON trajectories found under {trajectories_dir}")
    return files


def build_prompt(
    trajectories_dir: Path,
    trajectory_files: list[Path],
    labels: dict[str, Any],
) -> str:
    """Build a focused analysis prompt for Codex."""
    file_statuses = {
        path.relative_to(trajectories_dir).as_posix(): labels.get(path.name)
        for path in trajectory_files
    }
    missing_labels = sorted(set(labels) - {path.name for path in trajectory_files})
    return f"""请分析目录 `{trajectories_dir}` 中的所有轨迹，解释为什么不同模型设置下有的任务成功、有的失败，并形成一份严谨的中文评测报告。

当前 sample 的标签来自 compare.json，映射如下（文件名 -> resolved）：
```json
{json.dumps(file_statuses, ensure_ascii=False, indent=2)}
```

请逐个阅读目录中的 JSON 轨迹文件，而不是只根据文件名或 resolved 标签推测。轨迹是 ATIF/agent 执行记录，重点检查：任务理解、定位与假设、工具/命令选择、代码修改、测试反馈、错误恢复、最终收尾和验证证据。`resolved` 是外部评测结果，不等同于单一步骤的正确性。

报告必须包含：
1. 总体结论：成功与失败轨迹的共性差异，以及证据强弱和局限性。
2. 一套可复用的轨迹评测打分细则：总分 100 分，给出维度、权重、评分档位、扣分条件和可观察证据，并说明为什么这样设计。
3. 至少 2 个具体案例（优先选择成功和失败各一个；若样本中只有单一结果则说明这一点），引用对应轨迹文件名和关键事件，解释这些证据如何支持结论。
4. 对每条轨迹给出简短的诊断摘要，明确区分“过程质量”和最终 `resolved` 结果。
5. 可执行的改进建议，以及哪些结论不能仅凭这些轨迹确定。

不要修改目录中的任何文件。只输出 Markdown 报告正文，不要输出与分析无关的前言或 ```markdown 包裹。
""" + (f"\n注意：标签文件中还有当前目录不存在的条目：{missing_labels}\n" if missing_labels else "")


def run_codex(
    prompt: str,
    trajectories_dir: Path,
    *,
    codex_command: str = "codex",
    timeout: int = 1800,
) -> str:
    """Run Codex in read-only mode and return its final response."""
    command = [
        codex_command,
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--color",
        "never",
        "-C",
        str(trajectories_dir),
        prompt,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Codex command not found: {codex_command}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Codex analysis timed out after {timeout} seconds") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Codex exited with code {result.returncode}: {detail}")
    report = result.stdout.strip()
    if not report:
        raise RuntimeError("Codex returned an empty analysis")
    return report + "\n"


def analyse(
    trajectories_dir: Path,
    label_path: Path,
    *,
    codex_command: str = "codex",
    timeout: int = 1800,
) -> Path:
    """Analyze one sample directory and write ``analyse.md``."""
    trajectories_dir = trajectories_dir.expanduser().resolve()
    label_path = label_path.expanduser().resolve()
    files = find_trajectories(trajectories_dir)
    labels = load_sample_labels(label_path, trajectories_dir.name)
    prompt = build_prompt(trajectories_dir, files, labels)
    report = run_codex(
        prompt,
        trajectories_dir,
        codex_command=codex_command,
        timeout=timeout,
    )
    output_path = trajectories_dir / "analyse.md"
    output_path.write_text(report, encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trajectories_dir",
        "--trajectories-dir",
        type=Path,
        required=True,
        help="Sample directory containing trajectory JSON files",
    )
    parser.add_argument(
        "--trajectories_lable",
        "--trajectories-label",
        "--trajectories_label",
        type=Path,
        required=True,
        help="compare.json containing resolved labels",
    )
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    return args


if __name__ == "__main__":
    args = parse_args()
    try:
        output = analyse(
            args.trajectories_dir,
            args.trajectories_lable,
            codex_command=args.codex_command,
            timeout=args.timeout,
        )
    except (FileNotFoundError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Error: {exc}")
    print(f"Wrote {output}")
