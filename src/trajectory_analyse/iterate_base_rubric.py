#!/usr/bin/env python3
"""自动化迭代 iterated_rubric/base_0.yaml v2，基于 base_0.yaml 进行迭代

编辑协议在 iterate_score_rules 的规则级 add/delete/modify 之上扩展了 subcriteria 级：
* add_subcriterion: 新行为 → 增指标
* delete_subcriterion: reasoning 不清晰 → 改 score_rules
* modify_subcriterion: 历史证实的反复极低分特例 → 删指标
* 每个维度保持 1~5 个 subcriteria、每个 subcriteria 保持 1~5 条规则，非法单条被丢弃并记录

python iterate_base_rubric.py \\
    --train_dataset <verified-job> --verified_dataset <verified-job> \\
    --train_num 30 --iteration_num 3 --model <judge> [--critic-model <m>] \\
    [--rubric trajectory_scoring_skill/iterated_rubric/base_0.yaml] \\
    [--seed 42] [--output-dir <dir>]

1.参数说明
* train_dataset 与 verified_dataset 数据集指向 harbor trajectory 格式的目录
* --model 评分 judge model，用于给轨迹打分
* --critic-model 评估打分结果，缺省时与 --model 相同
* --iteration_num 迭代轮数

2.执行流程
* 用当前 rubric 打分（维度失败时最多补打 --score-attempts 轮）；全维度失败的轨迹跳过决策，直接处理下一条
* 当前 rubric + 该轨迹打分结果 + 原始 trajectory + 本轮已处理轨迹的子指标历史分数 
  → 是否需要修改 rubric + 最终 edit 集。
* 判断不需要修改、或 edits 全部非法 → 本条轨迹结束，处理下一条
* 需要修改 → 应用 edits 后过软质量审查（iterate_base_quality_prompt.j2），
   候选 rubric 必须清晰、原子、可判定、无重复矛盾，不合格则沿用旧版
* 质量合格 → 从 ``--verified_dataset`` 随机抽 1 条 trajectory.json，用迭代
   前后的 rubric 各打一次分，比较调用（``iterate_base_verify_prompt.j2``）分析
   两版打分结果的 reasoning 是否新版更合理；更合理才采纳候选 rubric
* 采纳后本轮后续轨迹用新 rubric 继续打分（增量迭代）；未采纳则沿用旧版。
   单条轨迹的决策/质量/验证失败都不阻塞整轮，记录后继续下一条

3.输出目录 (默认 iterate_base_rubric_runs/<UTC时间戳>)

* config.json                         本次运行的参数快照
* rubrics/base.iter<k>*.yaml          每轮基线与各次采纳的 rubric 快照
* train/iter<k>/<sample>/scores/      每轮每条训练轨迹的完整打分结果
* verified/iter<k>/pos<n>/{baseline,candidate}/<sample>/scores/  每次触发的对照打分结果
* iterations/iter<k>.json             每轮逐轨迹决策明细
* report.json / final_rubric.yaml     汇总报告与最终采纳的 rubric
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import iterate_score_rules as iter_mod
import score_trajectories as scorer

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SKILL_DIR = SCRIPT_DIR / "trajectory_scoring_skill"
DEFAULT_RUBRIC = DEFAULT_SKILL_DIR / "iterated_rubric" / "base_0.yaml"
DEFAULT_TRAIN_DATASET = (SCRIPT_DIR / "experiments/evaluation/verified"
                         / "20260217_mini-v2.0.0_gpt-5-2-high")
DEFAULT_VERIFIED_DATASET = (SCRIPT_DIR / "experiments/evaluation/verified"
                            / "20260217_mini-v2.0.0_claude-4-5-opus-high")
DEFAULT_RUNS_DIR = SCRIPT_DIR / "iterate_base_rubric_runs"

TEMPLATES = {
    # 迭代 LLM 各阶段的提示词模板，全部放在 skill 目录下。
    "train": "iterate_base_train_prompt.j2",   # 单轨迹打分后的立即决策
    "quality": "iterate_base_quality_prompt.j2",
    "verify": "iterate_base_verify_prompt.j2",
}

MIN_SUBCRITERIA = 1
MAX_SUBCRITERIA = 5
# 连续多少个单轨迹决策失败即中止整轮（网络级故障），配合 --resume 恢复
DECISION_FAILURE_LIMIT = 5


class RoundAborted(RuntimeError):
    """整轮被中止（如网络级故障）；``record`` 携带中止前的部分结果。"""

    def __init__(self, message: str, record: dict[str, Any]):
        super().__init__(message)
        self.record = record
# 规则级 edit 沿用 iterate_score_rules 协议；本脚本在其上扩展 subcriteria 级增删。
SUB_EDIT_OPS = ("add_subcriterion", "delete_subcriterion")
EDIT_OPS = tuple(iter_mod.EDIT_OPS) + SUB_EDIT_OPS


def _criteria(document: dict[str, Any]) -> list[dict[str, Any]]:
    return list(document["rubric"]["criteria"])


def _criterion_by_id(document: dict[str, Any], criterion_id: Any) -> dict[str, Any] | None:
    if not isinstance(criterion_id, str):
        return None
    return next((c for c in _criteria(document) if str(c.get("id")) == criterion_id), None)


def _all_subcriterion_ids(document: dict[str, Any]) -> set[str]:
    return {str(item.get("id"))
            for criterion in _criteria(document)
            for item in criterion.get("subcriteria", [])
            if isinstance(item, dict) and item.get("id") is not None}


def list_trajectories(root: Path) -> list[Path]:
    """列出数据集里的全部轨迹文件。

    两种布局都接受：``<root>/trajs_harbor/<sample>/trajectory.json``（verified job
    目录，优先）与 ``<root>/<sample>/trajectory.json``（直接给出样本目录）。
    """
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"dataset root is not a directory: {root}")
    harbor = root / "trajs_harbor"
    return iter_mod.list_trajectories(harbor if harbor.is_dir() else root)


# ---------------------------------------------------------------------------
# LLM protocol helpers


def _validate_one_edit(edit: Any, position: int, document: dict[str, Any]) -> str | None:
    """校验单条 edit：规则级 add/delete/modify + subcriteria 级增删。"""
    if not isinstance(edit, dict):
        return f"edit {position} is malformed"
    op = edit.get("op")
    if op in SUB_EDIT_OPS:
        if op == "add_subcriterion":
            criterion = _criterion_by_id(document, edit.get("criterion_id"))
            if criterion is None:
                return f"edit {position} has unknown criterion_id"
            sub_id = edit.get("id")
            if not isinstance(sub_id, str) or not sub_id.strip():
                return f"edit {position} needs a non-empty subcriterion id"
            if sub_id.strip() in _all_subcriterion_ids(document):
                return f"edit {position} reuses existing subcriterion id {sub_id!r}"
            for field in ("name", "description"):
                if not isinstance(edit.get(field), str) or not edit[field].strip():
                    return f"edit {position} needs non-empty {field}"
            rules = edit.get("score_rules")
            if not isinstance(rules, list) or not (
                    iter_mod.MIN_RULES <= len(rules) <= iter_mod.MAX_RULES) or not all(
                    isinstance(rule, str) and rule.strip() for rule in rules):
                return (f"edit {position} score_rules must be a list of "
                        f"{iter_mod.MIN_RULES} to {iter_mod.MAX_RULES} non-empty strings")
        else:  # delete_subcriterion
            sub_id = edit.get("subcriterion_id")
            item = iter_mod._find_subcriterion(document, sub_id)
            if item is None or not isinstance(sub_id, str):
                return f"edit {position} has unknown subcriterion_id"
            criterion = next(c for c in _criteria(document)
                             if any(s is item for s in c.get("subcriteria", [])))
            if len(criterion.get("subcriteria", [])) <= MIN_SUBCRITERIA:
                return (f"edit {position} would empty criterion "
                        f"{criterion.get('id')}; keep at least {MIN_SUBCRITERIA}")
        return None
    if op not in iter_mod.EDIT_OPS:
        return f"edit {position} has invalid op {op!r}"
    if not isinstance(edit.get("subcriterion_id"), str) \
            or iter_mod._find_subcriterion(document, edit.get("subcriterion_id")) is None:
        return f"edit {position} has unknown subcriterion_id"
    if op in ("add", "modify") and (not isinstance(edit.get("rule_text"), str)
                                    or not edit["rule_text"].strip()):
        return f"edit {position} needs non-empty rule_text"
    if op in ("delete", "modify"):
        index = edit.get("rule_index")
        if isinstance(index, bool) or not isinstance(index, int):
            return f"edit {position} needs integer rule_index"
    return None


def _validate_critic_result(
    result: Any,
    document: dict[str, Any],
) -> tuple[dict[str, Any], None] | tuple[None, str]:
    """校验 review/聚合返回的 analysis + edits 协议（含 subcriteria 级增删）。"""
    if not isinstance(result, dict):
        return None, "critic result is not an object"
    analysis = result.get("analysis")
    if not isinstance(analysis, str) or not analysis.strip():
        return None, "analysis must be a non-empty string"
    edits = result.get("edits")
    if not isinstance(edits, list):
        return None, "edits must be an array"
    for position, edit in enumerate(edits):
        reason = _validate_one_edit(edit, position, document)
        if reason is not None:
            return None, reason
    return {"analysis": analysis, "edits": edits}, None


def apply_rubric_edits(
    document: dict[str, Any],
    edits: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """把 edits 应用到 rubric 文档的深拷贝上。

    规则级 edit 复用 ``iterate_score_rules.apply_edits`` 的约束（1~5 条规则、
    索引越界、重复文本等）；subcriteria 级 edit 由本函数直接应用：add 要求目标
    criterion 现有 subcriteria 少于 :data:`MAX_SUBCRITERIA` 且新 id 全局唯一，
    delete 要求 criterion 删除后仍保留至少 :data:`MIN_SUBCRITERIA` 个。逐条应用，
    任何违反约束的 edit 被丢弃并记录在 dropped 中；规则级 edit 可作用于本轮新增
    的 subcriteria。返回 ``(candidate, applied, dropped)``。
    """
    candidate = copy.deepcopy(document)
    applied: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for raw in edits:
        op = raw.get("op") if isinstance(raw, dict) else None
        if op == "add_subcriterion":
            edit = {k: v for k, v in raw.items()
                    if k in ("op", "criterion_id", "id", "name", "description", "score_rules")}
            criterion = _criterion_by_id(candidate, edit.get("criterion_id"))
            sub_id = edit.get("id") if isinstance(edit.get("id"), str) else ""
            if criterion is None:
                dropped.append({**edit, "reason": "unknown criterion_id"})
            elif not sub_id.strip():
                dropped.append({**edit, "reason": "missing subcriterion id"})
            elif sub_id.strip() in _all_subcriterion_ids(candidate):
                dropped.append({**edit, "reason": "subcriterion id already exists"})
            elif len(criterion.get("subcriteria", [])) >= MAX_SUBCRITERIA:
                dropped.append({**edit, "reason": f"would exceed {MAX_SUBCRITERIA} subcriteria"})
            elif not (isinstance(edit.get("name"), str) and isinstance(edit.get("description"), str)
                      and isinstance(edit.get("score_rules"), list)
                      and all(isinstance(rule, str) for rule in edit["score_rules"])):
                dropped.append({**edit, "reason": "malformed add_subcriterion"})
            else:
                criterion["subcriteria"].append({
                    "id": sub_id.strip(), "name": edit["name"].strip(),
                    "description": edit["description"].strip(),
                    "score_rules": [rule.strip() for rule in edit["score_rules"]],
                })
                applied.append(edit)
        elif op == "delete_subcriterion":
            edit = {k: v for k, v in raw.items() if k in ("op", "subcriterion_id")}
            item = iter_mod._find_subcriterion(candidate, edit.get("subcriterion_id"))
            if item is None:
                dropped.append({**edit, "reason": "unknown subcriterion_id"})
            else:
                criterion = next(c for c in _criteria(candidate)
                                 if any(s is item for s in c.get("subcriteria", [])))
                if len(criterion.get("subcriteria", [])) <= MIN_SUBCRITERIA:
                    dropped.append({**edit, "reason": f"would go below {MIN_SUBCRITERIA} subcriterion"})
                else:
                    criterion["subcriteria"].remove(item)
                    applied.append(edit)
        else:
            # 规则级 edit 一次一条地委托给既有的应用逻辑，保证约束一致；
            # 每次都基于 candidate 增量更新，故可编辑本轮新增的 subcriteria。
            candidate, rule_applied, rule_dropped = iter_mod.apply_edits(candidate, [raw])
            applied.extend(rule_applied)
            dropped.extend(rule_dropped)
    return candidate, applied, dropped


def _precheck_candidate(document: dict[str, Any], candidate: dict[str, Any]) -> None:
    """确认应用 edits 后的 rubric 仍能被 score_trajectories 正确加载。"""
    for criterion in _criteria(candidate):
        if not criterion.get("subcriteria"):
            raise ValueError(f"criterion {criterion.get('id')} has no subcriteria")
        if scorer._criterion_scoring_mode(criterion) != "iterated":
            raise ValueError(f"criterion {criterion.get('id')} left iterated mode")


def _change_summary(initial: dict[str, Any], final: dict[str, Any]) -> dict[str, Any]:
    """对比两版 rubric：哪些子维度的规则变了，哪些子维度被增/删。"""

    def subs(document):
        return {str(item["id"]): (item.get("name"), item.get("description"),
                                  [str(r) for r in item.get("score_rules", [])])
                for criterion in _criteria(document)
                for item in criterion.get("subcriteria", [])}

    before, after = subs(initial), subs(final)
    return {
        "changed_subcriteria": sorted(
            sub_id for sub_id in set(before) & set(after) if before[sub_id] != after[sub_id]),
        "added_subcriteria": sorted(set(after) - set(before)),
        "deleted_subcriteria": sorted(set(before) - set(after)),
        "initial_subcriterion_count": len(before),
        "final_subcriterion_count": len(after),
        "initial_total_rule_count": sum(len(v[2]) for v in before.values()),
        "final_total_rule_count": sum(len(v[2]) for v in after.values()),
    }


def render_prompt(template: str, **variables: Any) -> str:
    try:
        from jinja2 import BaseLoader, Environment  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Jinja2 is required to render the iteration prompts") from exc
    env = Environment(loader=BaseLoader(), autoescape=False, keep_trailing_newline=True)
    return env.from_string(template).render(**variables)


def _validate_aggregate_result(
    result: Any,
    document: dict[str, Any],
) -> tuple[dict[str, Any], None] | tuple[None, str]:
    """校验聚合调用返回：是否修改 + 最终 edit 集。"""
    if not isinstance(result, dict):
        return None, "aggregate result is not an object"
    needs = result.get("needs_modification")
    if not isinstance(needs, bool):
        return None, "needs_modification must be boolean"
    parsed, reason = _validate_critic_result(result, document)
    if reason is not None:
        return None, reason
    if needs is False and parsed["edits"]:
        return None, "edits must be empty when needs_modification is false"
    return {"needs_modification": needs, **parsed}, None


def _validate_quality_result(result: Any) -> tuple[dict[str, Any], None] | tuple[None, str]:
    """校验软质量审查返回。"""
    if not isinstance(result, dict):
        return None, "quality result is not an object"
    passed = result.get("quality_pass")
    if not isinstance(passed, bool):
        return None, "quality_pass must be boolean"
    analysis = result.get("analysis")
    if not isinstance(analysis, str) or not analysis.strip():
        return None, "analysis must be a non-empty string"
    issues = result.get("issues", [])
    if not isinstance(issues, list) or any(not isinstance(i, str) for i in issues):
        return None, "issues must be an array of strings"
    return {"quality_pass": passed, "analysis": analysis, "issues": issues}, None


def _validate_verify_result(result: Any) -> tuple[dict[str, Any], None] | tuple[None, str]:
    """校验验证比较返回。"""
    if not isinstance(result, dict):
        return None, "verify result is not an object"
    better = result.get("more_reasonable")
    if not isinstance(better, bool):
        return None, "more_reasonable must be boolean"
    analysis = result.get("analysis")
    if not isinstance(analysis, str) or not analysis.strip():
        return None, "analysis must be a non-empty string"
    notes = result.get("criterion_notes", [])
    if not isinstance(notes, list) or any(not isinstance(n, dict) for n in notes):
        return None, "criterion_notes must be an array of objects"
    return {"more_reasonable": better, "analysis": analysis,
            "criterion_notes": notes}, None


def request_llm_json(
    prompt: str,
    validate: Callable[[Any], tuple[Any, str | None]],
    *,
    base_url: str,
    api_key: str,
    model: str,
    api_path: str = "chat/completions",
    timeout: int = 600,
    retries: int = 3,
    invalid_retries: int | None = None,
) -> dict[str, Any]:
    """调用 LLM 直到 ``validate`` 通过。

    两类失败走不同通道：网络/传输失败按 ``retries`` 预算退避重发同一 prompt；
    模型**返回了内容但 JSON 或协议校验失败**走修复通道——把上次的原始返回
    （截断至 2KB）与具体校验错误拼回对话再问，另给 ``invalid_retries`` 预算
    （默认与 ``retries`` 相同）。纠正式重试对"反复返回非法 JSON"的失败比盲目
    重发有效得多。
    """
    if invalid_retries is None:
        invalid_retries = retries
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    url = scorer._build_api_url(base_url, api_path)
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    transport_failures = 0
    invalid_failures = 0
    last_error = "no attempt made"
    while True:
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            text = scorer._http_json(url, headers, body, timeout)
        except Exception as exc:
            transport_failures += 1
            last_error = f"network error: {type(exc).__name__}: {exc}"
            if transport_failures >= retries:
                raise RuntimeError(
                    f"llm call failed after {retries} attempts: {last_error}") from None
            time.sleep(min(2 ** transport_failures, 30))
            continue
        parsed, reason = validate(scorer._extract_json(text))
        if reason is None:
            return parsed  # type: ignore[return-value]
        invalid_failures += 1
        if invalid_failures >= invalid_retries:
            raise RuntimeError(
                f"invalid llm response after {invalid_failures} attempts: {reason}")
        # 修复通道：带上上次的原始返回与校验错误，让模型自行修正
        messages = messages + [
            {"role": "assistant", "content": text[:2000]},
            {"role": "user", "content":
                f"Your previous reply above is invalid: {reason}. "
                 "Re-read the Output schema in the task and return corrected JSON only."},
        ]


# ---------------------------------------------------------------------------
# single-iteration stages


def _score_one(
    trajectory: Path,
    output_dir: Path,
    rubric_path: Path,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    api_path: str,
    retries: int,
    resume: bool = False,
) -> dict[str, Any]:
    return scorer.score_dimensions(
        trajectory, output_dir, api_key=api_key, base_url=base_url,
        model=model, timeout=timeout, rubric_path=rubric_path,
        api_path=api_path, retries=retries, resume=resume,
    )


def _update_score_history(
    history: dict[str, list[float]],
    merged: dict[str, Any],
) -> None:
    """记录本轮中每个子指标在各轨迹上的 0~1 分，作为后续决策的历史证据。"""
    for entry in merged.get("criterion_scores", []):
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            value = entry.get("score")
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                history.setdefault(entry["id"], []).append(float(value))


def _decide_on_trajectory(
    templates: dict[str, str],
    trajectory: Path,
    merged: dict[str, Any],
    rubric_yaml: str,
    history: dict[str, list[float]],
    document: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    model: str,
    api_path: str,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    """打分完成后立即决策：当前 rubric 是否需要修改 + 最终 edit 集。"""
    prompt = render_prompt(
        templates["train"], rubric_yaml=rubric_yaml,
        score_result=json.dumps(merged, ensure_ascii=False, indent=1),
        trajectory=(f"# trajectory path: {trajectory}\n"
                    + trajectory.read_text(encoding="utf-8", errors="replace")),
        history=json.dumps(history, ensure_ascii=False))
    return request_llm_json(
        prompt,
        lambda result: _validate_aggregate_result(result, document),
        base_url=base_url, api_key=api_key, model=model,
        api_path=api_path, timeout=timeout, retries=retries)


def _usable_score(merged: dict[str, Any]) -> bool:
    """判断一次合并打分是否还有可 review 的内容：至少一个维度成功。

    judge 网关偶发 RemoteDisconnected 时（Kimi spot 容量抖动），整条轨迹的
    6 个维度可能全部带 error；这种结果没有 reasoning 可分析，进 review 只会
    制造误导证据。部分维度成功的合并结果仍然可用。
    """
    gaps = merged.get("evidence_gaps", [])
    return len({g.get("location") for g in gaps if isinstance(g, dict)}) < len(scorer.DIMENSIONS)


def _score_with_attempts(
    trajectory: Path,
    scores_dir: Path,
    rubric_path: Path,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int,
    api_path: str,
    retries: int,
    resume: bool,
    score_attempts: int,
    score_attempt_wait: int,
) -> dict[str, Any]:
    """打分并在维度失败时反复补打：judge 网关抖动的收敛路径。

    每次补打都带 ``resume=True``，只重新请求缺失或带 error 的维度，已完成的
    维度文件原样保留。维度全部成功或达到 ``score_attempts`` 上限后返回最后
    一次合并结果；两次之间等待 ``score_attempt_wait`` 秒，给 spot 容量留恢复
    窗口。
    """
    merged: dict[str, Any] = {}
    for attempt in range(1, score_attempts + 1):
        merged = _score_one(
            trajectory, scores_dir, rubric_path, api_key=api_key,
            base_url=base_url, model=model, timeout=timeout,
            api_path=api_path, retries=retries,
            resume=(resume or attempt > 1))
        gap_dims = {str(gap.get("location"))
                    for gap in merged.get("evidence_gaps", [])
                    if isinstance(gap, dict)}
        if not gap_dims or attempt == score_attempts:
            break
        print(f"  re-scoring {trajectory.parent.name}: dims {','.join(sorted(gap_dims))} "
              f"(attempt {attempt + 1}/{score_attempts})")
        time.sleep(score_attempt_wait)
    return merged


def run_iteration(
    index: int,
    train_batch: list[Path],
    verified_trajectories: list[Path],
    rng: random.Random,
    document: dict[str, Any],
    workdir: Path,
    *,
    templates: dict[str, str],
    api_key: str,
    base_url: str,
    judge_model: str,
    critic_model: str,
    api_path: str = "chat/completions",
    timeout: int = 600,
    retries: int = 3,
    resume: bool = False,
    score_attempts: int = 3,
    score_attempt_wait: int = 120,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """逐轨迹迭代一轮，返回 ``(iteration_report, current_document)``。

    依次处理每条训练轨迹：打分（``resume=True`` 只补失败维度，最多 ``score_attempts``
    轮补打）→ 立即决策当前 rubric 是否需要修改（带本轮已处理轨迹的子指标历史分数）
    → 需要修改则应用 edits 过软质量审查 → 从 ``verified_trajectories`` 随机抽一条
    轨迹做迭代前后对照打分，reasoning 更合理才采纳。采纳后本轮后续轨迹用新 rubric
    继续；未采纳或被拒则沿用旧版。全维度失败的轨迹跳过决策直接处理下一条。
    """
    record: dict[str, Any] = {
        "iteration": index,
        "train_samples": [p.parent.name for p in train_batch],
        "train_scores_dir": str(workdir / f"train/iter{index}"),
        "train_steps": [],
        "train_usable_count": 0, "train_skipped": [],
        "decisions_to_modify": 0, "adopted_count": 0,
        "adopted": False, "adopted_rubric": None,
        "error": None,
    }
    current_document = document
    current_snapshot = iter_mod.save_rubric_document(
        current_document, workdir / f"rubrics/base.iter{index}.yaml")
    # 本轮已处理轨迹的子指标 0~1 分历史，为"反复极低分"提供跨轨迹证据
    history: dict[str, list[float]] = {}
    consecutive_failures = 0
    for position, trajectory in enumerate(train_batch, start=1):
        sample = trajectory.parent.name
        scores_dir = workdir / f"train/iter{index}/{sample}/scores"
        print(f"  scoring train {position}/{len(train_batch)}: {sample}")
        merged = _score_with_attempts(
            trajectory, scores_dir, current_snapshot, api_key=api_key,
            base_url=base_url, model=judge_model, timeout=timeout,
            api_path=api_path, retries=retries, resume=resume,
            score_attempts=score_attempts, score_attempt_wait=score_attempt_wait)
        step: dict[str, Any] = {
            "position": position, "sample": sample, "scores_dir": str(scores_dir),
            "rubric_snapshot": str(current_snapshot),
            "needs_modification": False, "analysis": None,
            "edits_applied": [], "edits_dropped": [],
            "candidate_rubric": None, "quality": None,
            "verified_sample": None, "verification": None,
            "adopted": False, "adopted_rubric": None, "error": None,
        }
        record["train_steps"].append(step)
        if not _usable_score(merged):
            # 全维度失败的打分没有 reasoning 可决策，跳过并记录
            record["train_skipped"].append(sample)
            print(f"  skipped {sample}: all dimensions errored")
            continue
        record["train_usable_count"] += 1
        _update_score_history(history, merged)
        try:
            # 打分完成后立即决策：rubric + 打分结果 + 轨迹 + 历史分数
            decision = _decide_on_trajectory(
                templates, trajectory, merged,
                current_snapshot.read_text(encoding="utf-8"), history,
                current_document,
                api_key=api_key, base_url=base_url, model=critic_model,
                api_path=api_path, timeout=timeout, retries=retries)
        except Exception as exc:
            # 单条决策失败记录后继续；连续多届失败说明是网络级故障
            # （如 DNS 断连），中止整轮以便 --resume 廉价恢复，不烧完整轮。
            consecutive_failures += 1
            step["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  decision failed on {sample}: {step['error']}")
            if consecutive_failures >= DECISION_FAILURE_LIMIT:
                record["error"] = (
                    f"{consecutive_failures} consecutive decision failures "
                    f"(ending at {sample}); judge gateway unreachable, "
                    "rerun with --resume when the network recovers")
                raise RoundAborted(record["error"], record) from exc
            continue
        consecutive_failures = 0
        step["analysis"] = decision["analysis"]
        step["needs_modification"] = decision["needs_modification"]
        if not decision["needs_modification"]:
            print(f"  train {position}: rubric left unchanged")
            continue
        candidate, applied, dropped = apply_rubric_edits(
            current_document, decision["edits"])
        step["edits_applied"], step["edits_dropped"] = applied, dropped
        if not applied:
            print(f"  train {position}: modification proposed but no valid edits")
            continue
        record["decisions_to_modify"] += 1
        _precheck_candidate(current_document, candidate)
        candidate_snapshot = iter_mod.save_rubric_document(
            candidate, workdir / f"rubrics/base.iter{index}-cand{position}.yaml")
        step["candidate_rubric"] = str(candidate_snapshot)
        edits_text = json.dumps(applied, ensure_ascii=False, indent=1)
        try:
            # 软质量审查：候选 rubric 必须清晰、原子、可判定、结构完好。
            quality = request_llm_json(
                render_prompt(templates["quality"], edits=edits_text,
                              change_analysis=decision["analysis"],
                              baseline_yaml=current_snapshot.read_text(encoding="utf-8"),
                              candidate_yaml=candidate_snapshot.read_text(encoding="utf-8")),
                _validate_quality_result,
                base_url=base_url, api_key=api_key, model=critic_model,
                api_path=api_path, timeout=timeout, retries=retries)
            step["quality"] = quality
            if not quality["quality_pass"]:
                print(f"  train {position}: candidate rejected by quality gate")
                continue
            verified_pick = rng.choice(verified_trajectories)
            step["verified_sample"] = verified_pick.parent.name
            step["verified_baseline_scores_dir"] = str(
                workdir / f"verified/iter{index}/pos{position}/baseline"
                / verified_pick.parent.name / "scores")
            step["verified_candidate_scores_dir"] = str(
                workdir / f"verified/iter{index}/pos{position}/candidate"
                / verified_pick.parent.name / "scores")
            baseline_merged = _score_with_attempts(
                verified_pick, Path(step["verified_baseline_scores_dir"]),
                current_snapshot, api_key=api_key, base_url=base_url,
                model=judge_model, timeout=timeout, api_path=api_path,
                retries=retries, resume=resume, score_attempts=score_attempts,
                score_attempt_wait=score_attempt_wait)
            candidate_merged = _score_with_attempts(
                verified_pick, Path(step["verified_candidate_scores_dir"]),
                candidate_snapshot, api_key=api_key, base_url=base_url,
                model=judge_model, timeout=timeout, api_path=api_path,
                retries=retries, resume=resume, score_attempts=score_attempts,
                score_attempt_wait=score_attempt_wait)
            verification = request_llm_json(
                render_prompt(templates["verify"], edits=edits_text,
                              trajectory=(f"# trajectory path: {verified_pick}\n"
                                          + verified_pick.read_text(
                                              encoding="utf-8", errors="replace")),
                              baseline_result=json.dumps(baseline_merged, ensure_ascii=False, indent=1),
                              candidate_result=json.dumps(candidate_merged, ensure_ascii=False, indent=1)),
                _validate_verify_result,
                base_url=base_url, api_key=api_key, model=critic_model,
                api_path=api_path, timeout=timeout, retries=retries)
            step["verification"] = verification
            if not verification["more_reasonable"]:
                print(f"  train {position}: verification rejected the candidate")
                continue
        except Exception as exc:
            step["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  verified flow failed on train {position}: {step['error']}")
            continue
        step["adopted"] = True
        # 采纳后本轮后续轨迹用新 rubric 继续打分
        current_document = candidate
        current_snapshot = iter_mod.save_rubric_document(
            current_document,
            workdir / f"rubrics/base.iter{index}-pos{position}-adopted.yaml")
        step["adopted_rubric"] = str(current_snapshot)
        record["adopted"] = True
        record["adopted_count"] += 1
        record["adopted_rubric"] = str(current_snapshot)
        print(f"  train {position}: rubric updated -> {current_snapshot.name}")
    return record, current_document


# ---------------------------------------------------------------------------
# main loop


def iterate(args: argparse.Namespace) -> dict[str, Any]:
    train_trajectories = list_trajectories(args.train_dataset)
    verified_trajectories = list_trajectories(args.verified_dataset)
    if len(train_trajectories) < args.train_num:
        raise SystemExit(
            f"train dataset has {len(train_trajectories)} trajectories, "
            f"but --train_num is {args.train_num}")
    if not verified_trajectories:
        raise SystemExit(f"no trajectories found under --verified_dataset")
    rng = random.Random(args.seed)
    templates = {key: (args.skill_dir / name).read_text(encoding="utf-8")
                 for key, name in TEMPLATES.items()}
    initial_document = iter_mod.load_rubric_document(args.rubric)

    if args.output_dir is not None:
        workdir = args.output_dir.expanduser().resolve()
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        workdir = DEFAULT_RUNS_DIR / timestamp
    workdir.mkdir(parents=True, exist_ok=True)

    config = {
        "train_dataset": str(args.train_dataset.resolve()),
        "verified_dataset": str(args.verified_dataset.resolve()),
        "train_num": args.train_num, "iteration_num": args.iteration_num,
        "seed": args.seed, "resume": args.resume,
        "score_attempts": args.score_attempts,
        "score_attempt_wait": args.score_attempt_wait,
        "judge_model": args.model,
        "critic_model": args.critic_model, "rubric": str(args.rubric),
        "workdir": str(workdir),
        "min_rules_per_subcriterion": iter_mod.MIN_RULES,
        "max_rules_per_subcriterion": iter_mod.MAX_RULES,
    }
    (workdir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"iterating rubric for {args.iteration_num} rounds "
          f"({args.train_num} train samples per round) -> {workdir}")
    current = initial_document
    iterations_dir = workdir / "iterations"
    iterations_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, args.iteration_num + 1):
        train_batch = rng.sample(train_trajectories, args.train_num)
        print(f"iter {index}/{args.iteration_num}: "
              f"train={','.join(p.parent.name for p in train_batch[:3])}"
              f"{'...' if len(train_batch) > 3 else ''} "
              f"(verified 轨迹在每次触发修改时随机抽取)")
        try:
            record, current = run_iteration(
                index, train_batch, verified_trajectories, rng, current, workdir,
                templates=templates, api_key=args.api_key,
                base_url=args.base_url, judge_model=args.model,
                critic_model=args.critic_model, api_path=args.dimension_api_path,
                timeout=args.timeout, retries=args.retries, resume=args.resume,
                score_attempts=args.score_attempts,
                score_attempt_wait=args.score_attempt_wait)
        except RoundAborted as exc:
            # 网络级故障等中止：保留部分结果，本轮结束（--resume 可廉价恢复）
            record = exc.record
        except Exception as exc:
            # 任意阶段崩溃：本轮放弃候选，保留当前 rubric 继续下一轮。
            record = {"iteration": index, "adopted": False,
                      "error": f"{type(exc).__name__}: {exc}"}
        (iterations_dir / f"iter{index}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if record.get("error"):
            print(f"  error: {record['error']}")
        else:
            adopted_steps = [s for s in record.get("train_steps", []) if s.get("adopted")]
            print(f"  round done: usable {record['train_usable_count']}"
                  f" skipped {len(record['train_skipped'])}"
                  f", proposed {record['decisions_to_modify']}"
                  f", adopted {len(adopted_steps)}")

    final_rubric = iter_mod.save_rubric_document(current, workdir / "final_rubric.yaml")
    report = {
        **config,
        "iterations": [
            json.loads((iterations_dir / f"iter{i}.json").read_text(encoding="utf-8"))
            for i in range(1, args.iteration_num + 1)
        ],
        "final_rubric": str(final_rubric),
        "rubric_change_summary": _change_summary(initial_document, current),
    }
    (workdir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = report["rubric_change_summary"]
    print(f"done: changed {len(summary['changed_subcriteria'])}"
          f"(+{len(summary['added_subcriteria'])}/-{len(summary['deleted_subcriteria'])}) "
          f"subcriteria; final rubric -> {final_rubric}")
    return report


# ---------------------------------------------------------------------------
# CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train_dataset", "--train-dataset", dest="train_dataset",
                        type=Path, default=DEFAULT_TRAIN_DATASET,
                        help="训练数据集根目录（<root>/<sample>/trajectory.json）")
    parser.add_argument("--verified_dataset", "--verified-dataset", dest="verified_dataset",
                        type=Path, default=DEFAULT_VERIFIED_DATASET,
                        help="验证数据集根目录（<root>/<sample>/trajectory.json）")
    parser.add_argument("--train_num", "--train-num", dest="train_num", type=int,
                        default=30, help="每轮随机抽取的训练轨迹数")
    parser.add_argument("--iteration_num", "--iteration-num", dest="iteration_num",
                        type=int, default=3, help="迭代轮数")
    parser.add_argument("--model", required=True, help="打分 judge 模型")
    parser.add_argument("--critic-model", "--critic_model", dest="critic_model",
                        default=None, help="迭代 critic 模型（默认与 --model 相同）")
    parser.add_argument("--base-url", "--base_url", dest="base_url", default=None,
                        help="LLM 网关地址（默认 $THETA_BASE_URL）")
    parser.add_argument("--api-key", "--api_key", dest="api_key", default=None,
                        help="LLM 网关密钥（默认 $THETA_API_KEY）")
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC,
                        help="被迭代的初始 rubric（iterated 无权重格式）")
    parser.add_argument("--skill-dir", "--skill_dir", dest="skill_dir",
                        type=Path, default=DEFAULT_SKILL_DIR,
                        help="打分 skill 目录（模板所在位置）")
    parser.add_argument("--dimension-api-path", dest="dimension_api_path",
                        default="chat/completions")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--resume", action="store_true",
                        help="重跑同一输出目录时只重打缺失或带 error 的打分维度，"
                             "已完成的维度结果原样保留（judge 网关抖动后的补打路径）")
    parser.add_argument("--score-attempts", "--score_attempts", dest="score_attempts",
                        type=int, default=3,
                        help="每条轨迹打分因维度失败时的补打轮数（每轮 resume 只补失败的维度；"
                             "默认 3）")
    parser.add_argument("--score-attempt-wait", "--score_attempt_wait",
                        dest="score_attempt_wait", type=int, default=120,
                        help="两轮补打之间的等待秒数，给 judge 容量留恢复窗口（默认 120）")
    parser.add_argument("--seed", type=int, default=None,
                        help="随机采样种子（复现实验）")
    parser.add_argument("--output-dir", "--output_dir", dest="output_dir", type=Path,
                        default=None,
                        help=r"输出目录（默认 iterate_base_rubric_runs/<UTC时间戳>）")
    args = parser.parse_args(argv)
    args.api_key = args.api_key or os.environ.get("THETA_API_KEY")
    args.base_url = args.base_url or os.environ.get("THETA_BASE_URL")
    if not args.critic_model:
        args.critic_model = args.model
    if args.train_num < 1:
        parser.error("--train_num must be positive")
    if args.iteration_num < 1:
        parser.error("--iteration_num must be positive")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.retries < 1:
        parser.error("--retries must be positive")
    if args.score_attempts < 1:
        parser.error("--score-attempts must be positive")
    if args.score_attempt_wait < 0:
        parser.error("--score-attempt-wait must not be negative")
    if not args.base_url:
        parser.error("--base-url is required")
    if not args.api_key:
        parser.error("--api-key is required")
    try:
        # 提前暴露格式错误：只有无权重 score_rules rubric 才能被本脚本迭代。
        for criterion in iter_mod.load_rubric_document(args.rubric)["rubric"]["criteria"]:
            if scorer._criterion_scoring_mode(criterion) != "iterated":
                raise ValueError(f"criterion {criterion.get('id')} is not iterated "
                                 "(subcriteria must carry score_rules without weights)")
    except Exception as exc:
        parser.error(f"--rubric is not a valid iterated rubric: {exc}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        iterate(args)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())