#!/usr/bin/env python3
"""自动化迭代 score_rules 打分模板 v1，针对 rubric/base_rules.yaml 进行迭代，仅修改子维度中的 score_rules

python iterate_score_rules.py \\
    --train_dataset <root> --validate_dataset <root> \\
    --rollout_num 5 --model <judge> [--critic-model <critic>] \\
    [--rubric trajectory_scoring_skill/rubric/base_rules.yaml] \\
    [--seed 42] [--output-dir <dir>]

1.参数说明
* train_dataset 与 validate_dataset 数据集指向 harbor trajectory 格式的目录
* --model 评分 judge model，用于给轨迹打分
* --critic-model 评估打分结果，缺省时与 --model 相同
* --rollout_num 迭代轮数

2.执行流程
* 从 --train_dataset 取一条训练轨迹，用当前 rubric 跑一次 score_trajectories.score_dimensions
* 把 当前 rubric YAML + 合并打分结果 + 轨迹 JSON 交给 critic LLM (iterate_rules_prompt.j2)，
得到对 score_rules 的 add/delete/modify 提案
* 应用提案得到候选 rubric，每个子维度规则数保持在 1~5
* 从 --validate_dataset 随机取 1 条轨迹，用候选 rubric 和当前 rubric 各打一次分，逐子维度比较通过率
* 任一子维度通过率提升超过 0.05 即采纳候选 rubric，否则回退。

3.输出目录（默认 iterate_score_rules_runs/<UTC时间戳>)

* config.json                   本次运行的参数快照
* rubrics/rubric.iter<k>*.yaml   每轮的基线/候选/采纳后 rubric 快照；
* train/iter<k>/scores/          每轮训练轨迹的完整打分输出
* iterations/iter<k>.json       每轮决策明细
* report.json / final_rubric.yaml  汇总报告与最终采纳的模板。
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
from typing import Any

import score_trajectories as scorer

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SKILL_DIR = SCRIPT_DIR / "trajectory_scoring_skill"
DEFAULT_RUBRIC = DEFAULT_SKILL_DIR / "rubric" / "base_rules.yaml"
DEFAULT_RUNS_DIR = SCRIPT_DIR / "iterate_score_rules_runs"

MIN_RULES = 1
MAX_RULES = 5
IMPROVE_THRESHOLD = 0.05
EDIT_OPS = ("add", "delete", "modify")


# ---------------------------------------------------------------------------
# dataset / rubric plumbing


def list_trajectories(root: Path) -> list[Path]:
    """列出 ``<root>/<sample>/trajectory.json`` 形式的全部轨迹文件。"""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"dataset root is not a directory: {root}")
    trajs = [p / "trajectory.json" for p in sorted(root.iterdir())
             if p.is_dir() and (p / "trajectory.json").is_file()]
    if not trajs:
        raise SystemExit(f"no <sample>/trajectory.json found under: {root}")
    return trajs


def load_rubric_document(rubric_path: Path) -> dict[str, Any]:
    """读取完整 rubric YAML 文档（不注入 scoring_mode 等运行时注记）。"""
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to load the rubric YAML") from exc
    document = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("rubric"), dict):
        raise ValueError(f"invalid rubric document: {rubric_path}")
    criteria = document["rubric"].get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError(f"rubric.criteria must be a non-empty list: {rubric_path}")
    return document


def save_rubric_document(document: dict[str, Any], path: Path) -> Path:
    """把 rubric 文档写回 YAML，供 ``score_dimensions`` 重新加载。"""
    import yaml  # type: ignore

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return path


def _find_subcriterion(document: dict[str, Any], sub_id: str) -> dict[str, Any] | None:
    for criterion in document["rubric"]["criteria"]:
        for item in criterion.get("subcriteria", []):
            if str(item.get("id")) == sub_id:
                return item
    return None


# ---------------------------------------------------------------------------
# critic protocol


def _validate_critic_result(
    result: Any,
    document: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], None] | tuple[None, str]:
    """校验 critic 返回的 edits 协议，返回 ``(parsed, None)`` 或 ``(None, reason)``。"""
    if not isinstance(result, dict):
        return None, "critic result is not an object"
    analysis = result.get("analysis")
    if not isinstance(analysis, str) or not analysis.strip():
        return None, "analysis must be a non-empty string"
    edits = result.get("edits")
    if not isinstance(edits, list):
        return None, "edits must be an array"
    for position, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return None, f"edit {position} is malformed"
        sub_id = edit.get("subcriterion_id")
        if not isinstance(sub_id, str) or _find_subcriterion(document, sub_id) is None:
            return None, f"edit {position} has unknown subcriterion_id"
        op = edit.get("op")
        if op not in EDIT_OPS:
            return None, f"edit {position} has invalid op {op!r}"
        if op in ("add", "modify"):
            text = edit.get("rule_text")
            if not isinstance(text, str) or not text.strip():
                return None, f"edit {position} needs non-empty rule_text"
        if op in ("delete", "modify"):
            index = edit.get("rule_index")
            if isinstance(index, bool) or not isinstance(index, int):
                return None, f"edit {position} needs integer rule_index"
    return {"analysis": analysis, "edits": edits}, None


def apply_edits(
    document: dict[str, Any],
    edits: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """把 critic edits 应用到 rubric 文档的深拷贝上。

    逐条应用；任何会让某个子维度违反 1~5 条约束、目标不存在或参数非法的
    edit 被丢弃并记录在 dropped 中。返回 ``(candidate, applied, dropped)``。
    """
    candidate = copy.deepcopy(document)
    applied: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for edit in edits:
        edit = {k: v for k, v in edit.items()
                if k in ("subcriterion_id", "op", "rule_index", "rule_text")}
        item = _find_subcriterion(candidate, str(edit.get("subcriterion_id")))
        if item is None:
            dropped.append({**edit, "reason": "unknown subcriterion_id"})
            continue
        rules = item["score_rules"]
        op = edit["op"]
        if op == "add":
            text = str(edit["rule_text"]).strip()
            if len(rules) >= MAX_RULES:
                dropped.append({**edit, "reason": f"would exceed {MAX_RULES} rules"})
            elif text in rules:
                dropped.append({**edit, "reason": "rule already exists"})
            else:
                rules.append(text)
                applied.append(edit)
        elif op == "delete":
            index = edit.get("rule_index")
            if isinstance(index, bool) or not isinstance(index, int):
                dropped.append({**edit, "reason": "missing or non-integer rule_index"})
            elif not 0 <= index < len(rules):
                dropped.append({**edit, "reason": "rule_index out of range"})
            elif len(rules) <= MIN_RULES:
                dropped.append({**edit, "reason": f"would go below {MIN_RULES} rule"})
            else:
                rules.pop(index)
                applied.append(edit)
        elif op == "modify":
            index = edit.get("rule_index")
            text = str(edit["rule_text"]).strip()
            if isinstance(index, bool) or not isinstance(index, int):
                dropped.append({**edit, "reason": "missing or non-integer rule_index"})
            elif not 0 <= index < len(rules):
                dropped.append({**edit, "reason": "rule_index out of range"})
            elif text == rules[index]:
                dropped.append({**edit, "reason": "rule unchanged"})
            else:
                rules[index] = text
                applied.append(edit)
        else:  # pragma: no cover - _validate_critic_result already gates ops
            dropped.append({**edit, "reason": f"invalid op {op!r}"})
    return candidate, applied, dropped


def render_critic_prompt(
    template: str,
    rubric_yaml: str,
    score_result: str,
    trajectory: str,
) -> str:
    try:
        from jinja2 import BaseLoader, Environment  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Jinja2 is required to render iterate_rules_prompt.j2") from exc
    env = Environment(loader=BaseLoader(), autoescape=False, keep_trailing_newline=True)
    return env.from_string(template).render(
        rubric_yaml=rubric_yaml, score_result=score_result, trajectory=trajectory)


def request_critic_edits(
    prompt: str,
    document: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    model: str,
    api_path: str,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    """调用 critic LLM，重试 ``retries`` 次直到返回合法的 edits 协议。"""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    url = scorer._build_api_url(base_url, api_path)
    last_error = "no attempt made"
    for attempt in range(retries):
        try:
            text = scorer._http_json(url, headers, body, timeout)
            parsed, reason = _validate_critic_result(scorer._extract_json(text), document)
            if reason is not None:
                raise RuntimeError(f"invalid critic response: {reason}")
            return parsed  # type: ignore[return-value]
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries - 1:
                time.sleep(min(2 ** (attempt + 1), 30))
    raise RuntimeError(f"critic failed after {retries} attempts: {last_error}")


# ---------------------------------------------------------------------------
# adoption decision


def subcriterion_pass_rates(merged: dict[str, Any]) -> dict[str, float]:
    """从合并打分结果提取各子维度的通过率（score 即 0~1 通过率）。"""
    return {
        str(entry["id"]): float(entry["score"])
        for entry in merged.get("criterion_scores", [])
        if isinstance(entry, dict) and isinstance(entry.get("score"), (int, float))
    }


def errored_dimensions(merged: dict[str, Any]) -> set[str]:
    return {str(gap.get("location"))
            for gap in merged.get("evidence_gaps", [])
            if isinstance(gap, dict) and gap.get("location")}


def decide_adoption(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    threshold: float = IMPROVE_THRESHOLD,
) -> tuple[bool, dict[str, float], list[str]]:
    """比较同一验证轨迹上的两次打分；任一子维度提升超过 threshold 即采纳。"""
    errored = errored_dimensions(baseline) | errored_dimensions(candidate)
    rates_baseline = subcriterion_pass_rates(baseline)
    rates_candidate = subcriterion_pass_rates(candidate)
    deltas: dict[str, float] = {}
    for sub_id in sorted(set(rates_baseline) & set(rates_candidate)):
        if sub_id[:1] in errored:
            continue  # 任一侧该维度有 worker 失败，分数不可信，不参与比较
        deltas[sub_id] = rates_candidate[sub_id] - rates_baseline[sub_id]
    improved = sorted(k for k, v in deltas.items() if v > threshold)
    return bool(improved), deltas, improved


# ---------------------------------------------------------------------------
# main loop


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
) -> dict[str, Any]:
    return scorer.score_dimensions(
        trajectory, output_dir, api_key=api_key, base_url=base_url,
        model=model, timeout=timeout, rubric_path=rubric_path,
        api_path=api_path, retries=retries,
    )


def run_iteration(
    index: int,
    train_trajectory: Path,
    validate_trajectory: Path,
    document: dict[str, Any],
    workdir: Path,
    *,
    critic_template: str,
    api_key: str,
    base_url: str,
    judge_model: str,
    critic_model: str,
    api_path: str,
    timeout: int,
    retries: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """执行一轮迭代，返回 ``(iteration_report, current_document)``。"""
    record: dict[str, Any] = {
        "iteration": index,
        "train_sample": train_trajectory.parent.name,
        "train_total_score": None,
        "critic_analysis": None,
        "edits_applied": [], "edits_dropped": [],
        "candidate_rubric": None, "no_op": False,
        "validate_sample": validate_trajectory.parent.name,
        "deltas": {}, "improved_subcriteria": [], "adopted": False,
        "adopted_rubric": None, "error": None,
    }
    rubric_snapshot = save_rubric_document(document, workdir / f"rubrics/rubric.iter{index}.yaml")

    # 1. 训练轨迹用当前 rubric 打分。
    train_merged = _score_one(
        train_trajectory, workdir / f"train/iter{index}/scores", rubric_snapshot,
        api_key=api_key, base_url=base_url, model=judge_model,
        timeout=timeout, api_path=api_path, retries=retries)
    record["train_total_score"] = train_merged.get("total_score")

    # 2. critic 根据打分结果与轨迹提出规则修改。
    rubric_yaml = rubric_snapshot.read_text(encoding="utf-8")
    trajectory_text = train_trajectory.read_text(encoding="utf-8", errors="replace")
    prompt = render_critic_prompt(
        critic_template, rubric_yaml,
        json.dumps(train_merged, ensure_ascii=False, indent=2),
        f"# trajectory path: {train_trajectory}\n{trajectory_text}",
    )
    try:
        critic = request_critic_edits(
            prompt, document, base_url=base_url, api_key=api_key,
            model=critic_model, api_path=api_path, timeout=timeout, retries=retries)
    except Exception as exc:
        # critic 连续失败：本轮 no-op，保留当前 rubric 继续下一轮。
        record.update({"no_op": True, "error": f"critic: {type(exc).__name__}: {exc}"})
        return record, document
    record["critic_analysis"] = critic["analysis"]

    # 3. 应用 edits 得到候选 rubric。
    candidate, applied, dropped = apply_edits(document, critic["edits"])
    record["edits_applied"] = applied
    record["edits_dropped"] = dropped
    if not applied:
        record.update({"no_op": True, "adopted": False})
        return record, document
    candidate_snapshot = save_rubric_document(
        candidate, workdir / f"rubrics/rubric.iter{index}-candidate.yaml")
    record["candidate_rubric"] = str(candidate_snapshot)

    # 4. 同一验证轨迹用候选与当前 rubric 各打一次分。
    baseline_merged = _score_one(
        validate_trajectory, workdir / f"validate/iter{index}/baseline/scores",
        rubric_snapshot,
        api_key=api_key, base_url=base_url, model=judge_model,
        timeout=timeout, api_path=api_path, retries=retries)
    candidate_merged = _score_one(
        validate_trajectory, workdir / f"validate/iter{index}/candidate/scores",
        candidate_snapshot,
        api_key=api_key, base_url=base_url, model=judge_model,
        timeout=timeout, api_path=api_path, retries=retries)

    # 5. 通过率提升超过阈值即采纳。
    adopted, deltas, improved = decide_adoption(baseline_merged, candidate_merged)
    record.update({"deltas": deltas, "improved_subcriteria": improved,
                   "adopted": adopted})
    if adopted:
        adopted_snapshot = save_rubric_document(
            candidate, workdir / f"rubrics/rubric.iter{index}-adopted.yaml")
        record["adopted_rubric"] = str(adopted_snapshot)
        return record, candidate
    return record, document


def iterate(args: argparse.Namespace) -> dict[str, Any]:
    train_trajectories = list_trajectories(args.train_dataset)
    validate_trajectories = list_trajectories(args.validate_dataset)
    if len(train_trajectories) < args.rollout_num:
        raise SystemExit(
            f"train dataset has {len(train_trajectories)} trajectories, "
            f"but --rollout_num is {args.rollout_num}")
    rng = random.Random(args.seed)
    train_picks = rng.sample(train_trajectories, args.rollout_num)

    critic_template = (args.skill_dir / "iterate_rules_prompt.j2").read_text(encoding="utf-8")
    initial_document = load_rubric_document(args.rubric)

    if args.output_dir is not None:
        workdir = args.output_dir.expanduser().resolve()
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        workdir = DEFAULT_RUNS_DIR / timestamp
    workdir.mkdir(parents=True, exist_ok=True)

    config = {
        "train_dataset": str(args.train_dataset.resolve()),
        "validate_dataset": str(args.validate_dataset.resolve()),
        "rollout_num": args.rollout_num, "seed": args.seed,
        "judge_model": args.model, "critic_model": args.critic_model,
        "rubric": str(args.rubric), "workdir": str(workdir),
        "min_rules_per_subcriterion": MIN_RULES,
        "max_rules_per_subcriterion": MAX_RULES,
        "improve_threshold": IMPROVE_THRESHOLD,
    }
    (workdir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"iterating rubric for {args.rollout_num} rounds -> {workdir}")
    current = initial_document
    iterations_dir = workdir / "iterations"
    iterations_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, args.rollout_num + 1):
        validate_trajectory = rng.choice(validate_trajectories)
        print(f"iter {index}/{args.rollout_num}: "
              f"train={train_picks[index - 1].parent.name} "
              f"validate={validate_trajectory.parent.name}")
        try:
            record, current = run_iteration(
                index, train_picks[index - 1], validate_trajectory, current,
                workdir, critic_template=critic_template, api_key=args.api_key,
                base_url=args.base_url, judge_model=args.model,
                critic_model=args.critic_model, api_path=args.dimension_api_path,
                timeout=args.timeout, retries=args.retries)
        except Exception as exc:
            record = {"iteration": index,
                      "train_sample": train_picks[index - 1].parent.name,
                      "validate_sample": validate_trajectory.parent.name,
                      "adopted": False, "error": f"{type(exc).__name__}: {exc}"}
        (iterations_dir / f"iter{index}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        if record.get("error"):
            print(f"  error: {record['error']}")
        elif record.get("no_op"):
            print("  no-op: no effective edits")
        else:
            print(f"  edits applied: {len(record['edits_applied'])}"
                  f" (dropped: {len(record['edits_dropped'])}); "
                  f"adopted={record['adopted']}"
                  + (f"; improved: {','.join(record['improved_subcriteria'])}"
                     if record["adopted"] else ""))

    final_rubric = save_rubric_document(current, workdir / "final_rubric.yaml")
    report = {
        **config,
        "iterations": [
            json.loads((iterations_dir / f"iter{i}.json").read_text(encoding="utf-8"))
            for i in range(1, args.rollout_num + 1)
        ],
        "final_rubric": str(final_rubric),
        "rubric_change_summary": _change_summary(initial_document, current),
    }
    (workdir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done: adopted {len(report['rubric_change_summary']['changed_subcriteria'])} "
          f"subcriteria; final rubric -> {final_rubric}")
    return report


def _change_summary(
    initial: dict[str, Any],
    final: dict[str, Any],
) -> dict[str, Any]:
    changed = []
    for criterion in initial["rubric"]["criteria"]:
        for item in criterion.get("subcriteria", []):
            sub_id = str(item["id"])
            final_item = _find_subcriterion(final, sub_id)
            final_rules = [str(rule) for rule in (final_item or {}).get("score_rules", [])]
            if final_rules != [str(rule) for rule in item.get("score_rules", [])]:
                changed.append(sub_id)
    return {
        "changed_subcriteria": changed,
        "initial_total_rule_count": sum(
            len(item.get("score_rules", []))
            for c in initial["rubric"]["criteria"] for item in c.get("subcriteria", [])),
        "final_total_rule_count": sum(
            len(item.get("score_rules", []))
            for c in final["rubric"]["criteria"] for item in c.get("subcriteria", [])),
    }


# ---------------------------------------------------------------------------
# CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train_dataset", "--train-dataset", dest="train_dataset",
                        type=Path, required=True,
                        help="训练数据集根目录（<root>/<sample>/trajectory.json）")
    parser.add_argument("--validate_dataset", "--validate-dataset", dest="validate_dataset",
                        type=Path, required=True,
                        help="验证数据集根目录（<root>/<sample>/trajectory.json）")
    parser.add_argument("--rollout_num", "--rollout-num", dest="rollout_num",
                        type=int, required=True, help="迭代的轮数")
    parser.add_argument("--model", required=True, help="打分 judge 模型")
    parser.add_argument("--critic-model", "--critic_model", dest="critic_model",
                        default=None, help="规则 critic 模型（默认与 --model 相同）")
    parser.add_argument("--base-url", "--base_url", dest="base_url",
                        default=None, help="LLM 网关地址（默认 $THETA_BASE_URL）")
    parser.add_argument("--api-key", "--api_key", dest="api_key", default=None,
                        help="LLM 网关密钥（默认 $THETA_API_KEY）")
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC,
                        help="被迭代的初始 rubric（score_rules 格式）")
    parser.add_argument("--skill-dir", "--skill_dir", dest="skill_dir",
                        type=Path, default=DEFAULT_SKILL_DIR,
                        help="打分 skill 目录（模板所在位置）")
    parser.add_argument("--dimension-api-path", dest="dimension_api_path",
                        default="chat/completions")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--seed", type=int, default=None,
                        help="随机采样种子（复现实验）")
    parser.add_argument("--output-dir", "--output_dir", dest="output_dir",
                        type=Path, default=None,
                        help=r"输出目录（默认 iterate_score_rules_runs/<UTC时间戳>）")
    args = parser.parse_args(argv)
    args.api_key = args.api_key or os.environ.get("THETA_API_KEY")
    args.base_url = args.base_url or os.environ.get("THETA_BASE_URL")
    if not args.critic_model:
        args.critic_model = args.model
    if args.rollout_num < 1:
        parser.error("--rollout_num must be positive")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.retries < 1:
        parser.error("--retries must be positive")
    if not args.base_url:
        parser.error("--base_url is required")
    if not args.api_key:
        parser.error("--api_key is required")
    try:
        # 提前暴露格式错误：只有 score_rules rubric 才能编辑 score_rules。
        for criterion in load_rubric_document(args.rubric)["rubric"]["criteria"]:
            scorer._criterion_scoring_mode(criterion)
    except Exception as exc:
        parser.error(f"--rubric is not a valid score_rules rubric: {exc}")
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