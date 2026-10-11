#!/usr/bin/env python3
"""
使用内置的 A-F 评分细则，对一份轨迹 JSON 进行量化评分:

python score_trajectories.py path/to/trajectory.json --model model --output-dir path/to/scores

1.参数说明
* trajectory.json 是输入轨迹
* --output-dir 是可选的输出目录，未指定时默认写入 ``<轨迹目录>/scores``
* --rubric 选择评分细则文件
    ** 默认 rubric/base_origin.yaml，二元 pass/fail condition 判别
    ** rubric/base_verified.yaml 对 base_origin.yaml 中的维度进行校验修改
    ** rubric/base.yaml 修改 origin 的二元判别，要求 LLM 对每个子维度进行连续型打分
    ** rubric/base_rules.yaml 增加 score_rules 字符串细则列表代替 base.yaml 中的 pass/fail condition，
    LLM 逐条判定子维度得分即细则通过率，维度分 = 通过率 × 子维度权重之和，总分仍为百分制
    ** iterated_rubric/base_0.yaml 属于无权重 rubric ，是 rubric self-evolving 的起点，配套模板为 iterated_prompt.j2：
    子维度由 LLM 打出 0~1 的细则通过率并附 reasoning，维度分为其子维度均值（0~1），总分为六个维度分的均值（0~1）
* --model、--base-url、--api-key 决定目标模型和请求地址。超时、重试次数和 API

2.执行与输出
* 先加载模板和细则，再并发启动 A-F 六个独立 worker
* 每个 worker 请求LLM、提取并校验一个 JSON 对象，然后写入 <轨迹名>.score.<维度>.json
* 所有 worker 结束后，主进程将这些文件合并为 <轨迹名>.score.json
* 单个维度失败不会丢弃其他分数，而会在合并文件的 evidence_gaps 中记录失败原因

3.断点续打
传入 --resume 后，启动 worker ，先检查每个维度已有的输出文件，对缺失或失败的维度重新请求 API，完成后合并结果
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SKILL_DIR = SCRIPT_DIR / "trajectory_scoring_skill"
DEFAULT_RUBRIC = DEFAULT_SKILL_DIR / "rubric" / "base_origin.yaml"
DIMENSIONS = tuple("ABCDEF")


def _criterion_scoring_mode(criterion: dict[str, Any]) -> str:
    """Detect a criterion's scoring format: ``score_rules``, ``iterated`` or legacy.

    A subcriterion is in ``score_rules`` format when ``score_rules`` is a
    non-empty list of non-empty strings and a numeric ``score`` weight is
    present, in ``iterated`` format when the same rule list appears without a
    weight, and in legacy format when the key is absent.  Mixing formats
    inside one criterion is an authoring error.
    """
    subcriteria = criterion.get("subcriteria")
    if not isinstance(subcriteria, list) or not subcriteria:
        raise ValueError(f"criterion {criterion.get('id')} has no subcriteria")
    modes = set()
    for item in subcriteria:
        rules = item.get("score_rules") if isinstance(item, dict) else None
        if rules is None:
            modes.add("legacy")
        elif not (isinstance(rules, list) and rules and all(
                isinstance(rule, str) and rule.strip() for rule in rules)):
            raise ValueError(f"subcriterion {item.get('id')}: score_rules must be "
                             "a non-empty list of non-empty strings")
        elif "score" in item:
            if isinstance(item.get("score"), bool) or not isinstance(
                    item.get("score"), (int, float)):
                raise ValueError(f"subcriterion {item.get('id')}: the score weight "
                                 "must be a number")
            modes.add("score_rules")
        else:
            modes.add("iterated")
    if len(modes) != 1:
        raise ValueError(f"criterion {criterion.get('id')} mixes score_rules, "
                         "iterated and pass/fail_condition subcriteria")
    return modes.pop()


def _load_rubric(
    skill_dir: Path,
    rubric_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to load the rubric YAML") from exc
    path = rubric_path if rubric_path is not None else skill_dir / "rubric" / "base_origin.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("rubric"), dict):
        raise ValueError(f"invalid rubric document: {path}")
    criteria = document["rubric"].get("criteria")
    if not isinstance(criteria, list):
        raise ValueError(f"rubric.criteria must be a list: {path}")
    result = {str(item.get("id")): item for item in criteria
              if isinstance(item, dict) and item.get("id") in DIMENSIONS}
    if set(result) != set(DIMENSIONS):
        raise ValueError("rubric must define exactly dimensions A-F")
    for criterion in result.values():
        criterion["scoring_mode"] = _criterion_scoring_mode(criterion)
    return result


def load_dimension_skill(
    skill_dir: Path = DEFAULT_SKILL_DIR,
    rubric_path: Path | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Load the runtime resources used by the scorer.

    Returns ``(templates, dimensions)``: one Jinja template per scoring format
    (``legacy``, ``score_rules`` and ``iterated``) and the A-F criteria with a
    ``scoring_mode`` annotation on each.
    """
    templates = {
        "legacy": (skill_dir / "scoring_prompt.j2").read_text(encoding="utf-8"),
        "score_rules": (skill_dir / "scoring_rules_prompt.j2").read_text(encoding="utf-8"),
        "iterated": (skill_dir / "iterated_prompt.j2").read_text(encoding="utf-8"),
    }
    return templates, _load_rubric(skill_dir, rubric_path)


def render_dimension_prompt(
    template: str,
    criterion: dict[str, Any],
    trajectory: str,
) -> str:
    try:
        from jinja2 import BaseLoader, Environment  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Jinja2 is required to render scoring_prompt.j2") from exc
    env = Environment(loader=BaseLoader(), autoescape=False, keep_trailing_newline=True)
    return env.from_string(template).render(criterion=criterion, trajectory=trajectory)


def _build_api_url(base_url: str, api_path: str) -> str:
    base = base_url.rstrip("/")
    path = api_path.lstrip("/")
    return base if base.endswith("/" + path) else f"{base}/{path}"


def _first_text(response: dict[str, Any]) -> str | None:
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            for key in ("content", "reasoning_content"):
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    content = response.get("content")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        value = content[0].get("text")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _http_json(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int,
    *,
    retries: int = 1,
) -> str:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    for attempt in range(retries):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            text = _first_text(parsed) if isinstance(parsed, dict) else None
            if not text:
                raise RuntimeError(f"empty/unparsable API response: {raw[:300]}")
            return text
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            transient = exc.code in {408, 425, 429, 500, 502, 503, 504}
            if not transient or attempt == retries - 1:
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"network error: {type(exc).__name__}: {exc}") from exc
        except (json.JSONDecodeError, RuntimeError) as exc:
            # A disconnected/healthy HTTP connection can still return a
            # malformed or empty response. Treat that as transient too.
            if attempt == retries - 1:
                raise RuntimeError(
                    f"invalid API response after {retries} attempts: {exc}"
                ) from exc
        time.sleep(min(2 ** (attempt + 1), 30))
    raise RuntimeError("API request failed")


def _extract_json(text: str) -> dict[str, Any] | None:
    """Return the first JSON object embedded in a model completion.

    JSON mode is requested from the API, but gateways and models can still add
    prose, Markdown fences, or a second object.  ``raw_decode`` consumes one
    complete value at a time, so it avoids joining the first ``{`` to the last
    ``}`` and thereby producing an invalid candidate when more than one object
    is present.
    """
    decoder = json.JSONDecoder()
    text = text.strip()
    for start, char in enumerate(text):
        # The scorer accepts an object only; skipping other characters also
        # naturally handles prose and Markdown code-fence prefixes.
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _validate_rule_verdicts(
    sub_id: str,
    rules: Any,
    rubric_rules: list[Any],
) -> str | None:
    """Return an error message when per-rule verdicts do not match the rubric.

    The verdicts must be complete, ordered, boolean and aligned with the
    rubric's own ``score_rules`` list.
    """
    if not isinstance(rules, list):
        return f"subcriterion {sub_id} rules must be an array"
    if len(rules) != len(rubric_rules):
        return (f"subcriterion {sub_id} must report exactly "
                f"{len(rubric_rules)} rule verdicts, got {len(rules)}")
    for position, rule in enumerate(rules):
        if not isinstance(rule, dict):
            return f"subcriterion {sub_id} rule {position} is malformed"
        if rule.get("index") != position:
            return (f"subcriterion {sub_id} rule {position} must carry "
                    f"index == {position}")
        if not isinstance(rule.get("passed"), bool):
            return (f"subcriterion {sub_id} rule {position} passed "
                    "must be boolean")
        if not isinstance(rule.get("evidence", []), list):
            return (f"subcriterion {sub_id} rule {position} evidence "
                    "must be an array")
    return None


def _validate_score_rules_dimension(
    items: list[Any],
    criterion: dict[str, Any],
) -> tuple[bool, str]:
    """Validate a ``score_rules`` worker result: one verdict per rubric rule."""
    rubric_items = {str(item["id"]): item for item in criterion.get("subcriteria", [])}
    seen: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return False, "subcriterion is malformed"
        sub_id = item["id"]
        error = _validate_rule_verdicts(
            sub_id, item.get("rules"), rubric_items.get(sub_id, {}).get("score_rules", []))
        if error:
            return False, error
        seen.append(sub_id)
    if len(seen) != len(set(seen)) or set(seen) != set(rubric_items):
        return False, "subcriteria ids do not match the rubric"
    return True, ""


# ``iterated`` 分数必须等于细则通过率；容差只放宽到四舍五入级别（0.5/1 规则 ≈ 0.06）
_ITERATED_SCORE_TOLERANCE = 0.06


def _validate_iterated_dimension(
    items: list[Any],
    criterion: dict[str, Any],
) -> tuple[bool, str]:
    """Validate an ``iterated`` worker result: 0..1 scores grounding the verdicts.

    Each subcriterion carries a ``score`` between 0 and 1, a non-empty
    ``reasoning`` and the per-rule verdicts; the score must equal the fraction
    of passed rules, up to rounding.
    """
    rubric_items = {str(item["id"]): item for item in criterion.get("subcriteria", [])}
    seen: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return False, "subcriterion is malformed"
        sub_id = item["id"]
        rubric_item = rubric_items.get(sub_id, {})
        error = _validate_rule_verdicts(
            sub_id, item.get("rules"), rubric_item.get("score_rules", []))
        if error:
            return False, error
        value = item.get("score")
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value):
            return False, f"subcriterion {sub_id} score must be a finite number"
        if not 0.0 <= value <= 1.0:
            return False, f"subcriterion {sub_id} score must be between 0 and 1"
        reasoning = item.get("reasoning")
        if not isinstance(reasoning, str) or not reasoning.strip():
            return False, f"subcriterion {sub_id} reasoning must be a non-empty string"
        passed_count = sum(1 for rule in item["rules"] if rule.get("passed") is True)
        pass_rate = passed_count / len(item["rules"])
        if abs(float(value) - pass_rate) > _ITERATED_SCORE_TOLERANCE:
            return False, (f"subcriterion {sub_id} score {value} must match "
                           f"its rule pass rate {pass_rate:.2f}")
        seen.append(sub_id)
    if len(seen) != len(set(seen)) or set(seen) != set(rubric_items):
        return False, "subcriteria ids do not match the rubric"
    return True, ""


def validate_dimension_result(
    result: Any,
    criterion: dict[str, Any],
) -> tuple[bool, str]:
    if not isinstance(result, dict):
        return False, "result is not an object"
    dimension = str(criterion["id"])
    if result.get("criterion_id") != dimension:
        return False, f"criterion_id must be {dimension}"
    if not isinstance(result.get("applicable"), bool):
        return False, "applicable must be boolean"
    items = result.get("subcriteria")
    if result["applicable"] is False:
        return (True, "") if items is None or isinstance(items, list) else (
            False, "subcriteria must be null or an array"
        )
    if not isinstance(items, list):
        return False, "subcriteria must be an array"
    if criterion.get("scoring_mode") == "score_rules":
        return _validate_score_rules_dimension(items, criterion)
    if criterion.get("scoring_mode") == "iterated":
        return _validate_iterated_dimension(items, criterion)
    expected = {str(item["id"]) for item in criterion.get("subcriteria", [])}
    actual: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            return False, "subcriterion is malformed"
        if not isinstance(item.get("passed"), bool):
            return False, f"subcriterion {item.get('id')} passed must be boolean"
        if "score" in item:
            value = item["score"]
            try:
                numeric_value = float(value)
            except (TypeError, ValueError, OverflowError):
                numeric_value = math.nan
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(numeric_value)):
                return False, f"subcriterion {item.get('id')} score must be a finite number"
            rubric_item = next((r for r in criterion.get("subcriteria", [])
                                if str(r["id"]) == item["id"]), None)
            maximum = float(rubric_item.get("score", 0)) if rubric_item else 0.0
            if numeric_value < 0 or numeric_value > maximum:
                return False, f"subcriterion {item.get('id')} score must be between 0 and {maximum:g}"
            if item["passed"] != (numeric_value == maximum):
                return False, f"subcriterion {item.get('id')} passed must indicate full credit"
        actual.append(item["id"])
    if len(actual) != len(set(actual)) or set(actual) != expected:
        return False, "subcriteria ids do not match the rubric"
    return True, ""


def _load_completed_dimension_result(
    path: Path,
    criterion: dict[str, Any],
) -> dict[str, Any] | None:
    """返回已完成的维度结果；缺失、损坏或带 error 时返回 ``None``。

    合法的 ``applicable: false``（无 ``error``）是真实完成的评判，正常返回，
    调用方不得对它重打。
    """
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict) or result.get("error"):
        return None
    valid, _ = validate_dimension_result(result, criterion)
    return result if valid else None


def find_failed_dimensions(
    trajectory: Path,
    output_dir: Path,
    dimensions: dict[str, dict[str, Any]],
) -> list[str]:
    """按 A-F 顺序列出需要重打的维度：输出缺失、损坏、带 error 或未通过校验。"""
    failed = []
    for dimension in DIMENSIONS:
        path = output_dir / f"{trajectory.stem}.score.{dimension}.json"
        if _load_completed_dimension_result(path, dimensions[dimension]) is None:
            failed.append(dimension)
    return failed


def _rules_audit_entry(
    worker_item: dict[str, Any],
    rubric_item: dict[str, Any],
) -> dict[str, Any]:
    """Build one audit entry for a ``score_rules`` subcriterion.

    ``score`` is the fraction of rubric rules that passed (0..1); the
    contribution to the criterion total is the fraction weighted by the
    subcriterion weight (``weighted_score``).
    """
    rules = worker_item.get("rules")
    rules = rules if isinstance(rules, list) else []
    passed_count = sum(
        1 for rule in rules if isinstance(rule, dict) and rule.get("passed") is True
    )
    rule_total = len(rubric_item.get("score_rules", []))
    pass_rate = passed_count / rule_total if rule_total else 0.0
    weight = float(rubric_item.get("score", 0))
    evidence = [e for rule in rules if isinstance(rule, dict)
                for e in rule.get("evidence", []) if isinstance(e, dict)]
    reasons = [str(rule["reason"]) for rule in rules
               if isinstance(rule, dict) and isinstance(rule.get("reason"), str)
               and rule["reason"].strip()]
    return {
        "id": str(rubric_item["id"]), "score": pass_rate, "max_score": weight,
        "weighted_score": pass_rate * weight, "pass_rate": pass_rate,
        "passed_rules": passed_count, "total_rules": rule_total,
        "evidence": evidence, "reason": "; ".join(reasons),
    }


def _iterated_audit_entry(
    worker_item: dict[str, Any],
    rubric_item: dict[str, Any],
) -> dict[str, Any]:
    """Build one audit entry for an ``iterated`` subcriterion.

    ``score`` is the worker-reported 0..1 fraction of rubric rules that
    passed, grounded by the per-subcriterion ``reasoning``.  The criterion
    score is the mean of these scores rather than a weighted sum.
    """
    rules = worker_item.get("rules")
    rules = rules if isinstance(rules, list) else []
    value = worker_item.get("score")
    score = float(value) if not isinstance(value, bool) and isinstance(
        value, (int, float)) else 0.0
    passed_count = sum(
        1 for rule in rules if isinstance(rule, dict) and rule.get("passed") is True
    )
    rule_total = len(rubric_item.get("score_rules", []))
    evidence = [e for rule in rules if isinstance(rule, dict)
                for e in rule.get("evidence", []) if isinstance(e, dict)]
    return {
        "id": str(rubric_item["id"]), "score": score, "max_score": 1.0,
        "pass_rate": score, "weighted_score": score,
        "passed_rules": passed_count, "total_rules": rule_total,
        "evidence": evidence, "reason": str(worker_item.get("reasoning", "")),
    }


def _dimension_score(
    result: dict[str, Any],
    criterion: dict[str, Any],
) -> tuple[float, list[dict[str, Any]]]:
    if result.get("error") or result.get("applicable") is False:
        return 0.0, []
    items = result.get("subcriteria")
    if not isinstance(items, list):
        return 0.0, []
    mode = criterion.get("scoring_mode")
    by_id = {item["id"]: item for item in items if isinstance(item, dict)}
    score = 0.0
    audit = []
    for item in criterion.get("subcriteria", []):
        cid = str(item["id"])
        worker_item = by_id.get(cid, {})
        if mode == "score_rules":
            entry = _rules_audit_entry(worker_item, item)
            score += entry["weighted_score"]
        elif mode == "iterated":
            entry = _iterated_audit_entry(worker_item, item)
        else:
            if "score" in worker_item:
                value = float(worker_item["score"])
            else:
                # Backward compatibility for worker responses produced before
                # partial-credit scores were added to the prompt.
                value = float(item.get("score", 0)) if worker_item.get("passed") is True else 0.0
            score += value
            entry = {
                "id": cid, "score": value, "max_score": float(item.get("score", 0)),
                "evidence": worker_item.get("evidence", []),
                "reason": worker_item.get("reason", ""),
            }
        audit.append(entry)
    if mode == "iterated":
        # 维度分 = 子维度 0~1 分的均值，使维度得分不受子维度数量的影响
        score = (sum(entry["score"] for entry in audit) / len(audit)
                 if audit else 0.0)
    return score, audit


def merge_dimension_scores(
    trajectory: Path,
    output_dir: Path,
    dimensions: dict[str, dict[str, Any]],
    *,
    model: str = "",
) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    criterion_scores: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    total = 0.0
    # ``iterated`` rubric 的每个维度各为 0~1 分；六个维度到齐时总分取均值
    iterated_dimensions = sum(
        1 for criterion in dimensions.values()
        if criterion.get("scoring_mode") == "iterated")
    for dimension in DIMENSIONS:
        criterion = dimensions[dimension]
        path = output_dir / f"{trajectory.stem}.score.{dimension}.json"
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result = {"criterion_id": dimension, "applicable": False,
                      "subcriteria": None, "error": f"missing/invalid worker output: {exc}"}
        valid, reason = validate_dimension_result(result, criterion)
        if not valid:
            result["error"] = result.get("error") or f"invalid worker output: {reason}"
        if result.get("error"):
            gaps.append({"location": dimension, "observation": str(result["error"])})
        value, audit = _dimension_score(result, criterion)
        total += value
        criterion_scores.extend(audit)
        maximum = (1.0 if criterion.get("scoring_mode") == "iterated"
                   else float(criterion.get("max_score", 0)))
        scores[dimension] = {
            "name": criterion.get("name", dimension), "score": value,
            "max_score": maximum,
            "level": "excellent" if value >= maximum * .9 else
                      "adequate" if value >= maximum * .6 else
                      "limited" if value > 0 else "missing",
            "evidence": [e for item in audit for e in item.get("evidence", [])][:3],
        }
    result = {
        "trajectory": str(trajectory), "scores": scores,
        "criterion_scores": criterion_scores, "raw_total_score": total,
        "total_score": (total / len(DIMENSIONS)
                        if iterated_dimensions == len(DIMENSIONS) else total),
        "evidence_confidence": "low" if gaps else "medium",
        "caps_or_flags": [],
        "dimension_results": {d: str(output_dir / f"{trajectory.stem}.score.{d}.json")
                              for d in DIMENSIONS},
        "facts": {"final_artifact": {"modified_files": [], "patch_status": "unknown"},
                  "timeline": [], "environment": [], "evidence_gaps": gaps},
        "oracle": {"positive_behavior": [], "negative_or_compatibility_behavior": [],
                   "boundaries": [], "forbidden_changes": [], "unknowns": []},
        "evidence_gaps": gaps, "key_strengths": [], "key_risks": [],
        "conclusion": "Merged from independent A-F dimension evaluations.",
        "model": model, "output_dir": str(output_dir),
    }
    (output_dir / f"{trajectory.stem}.score.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _dimension_worker(
    dimension: str,
    trajectory: str,
    templates: dict[str, str],
    criterion: dict[str, Any],
    output_path: str,
    base_url: str,
    api_key: str,
    model: str,
    api_path: str,
    timeout: int,
    retries: int,
) -> None:
    output = Path(output_path)
    try:
        # Each process owns one rubric dimension.  Keeping the trajectory
        # loading and prompt rendering in the worker makes spawned processes
        # independent and lets all six API calls run concurrently.
        trajectory_text = Path(trajectory).read_text(encoding="utf-8", errors="replace")
        # Each criterion carries its own scoring format; pick the matching
        # template variant so legacy and score_rules rubrics can coexist.
        template = templates[str(criterion.get("scoring_mode", "legacy"))]
        prompt = render_dimension_prompt(
            template, criterion, f"# trajectory path: {trajectory}\n{trajectory_text}"
        )
        body = {
            "model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        for attempt in range(retries):
            try:
                # Transport retry lives in _http_json; this outer retry also
                # retries syntactically valid API replies whose score object
                # fails the rubric schema validation below.
                text = _http_json(
                    _build_api_url(base_url, api_path), headers, body, timeout,
                )
                result = _extract_json(text)
                valid, reason = validate_dimension_result(result, criterion)
                if not valid:
                    raise RuntimeError(f"invalid dimension result: {reason}")
                break
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(min(2 ** (attempt + 1), 30))
    except Exception as exc:
        # Always persist a dimension-shaped error result.  The merge phase can
        # then retain the other five scores and expose this failure as a gap.
        result = {"criterion_id": dimension, "applicable": False,
                  "subcriteria": None, "error": f"{type(exc).__name__}: {exc}"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def score_dimensions(
    trajectory: Path,
    output_dir: Path,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: int = 1800,
    skill_dir: Path = DEFAULT_SKILL_DIR,
    rubric_path: Path | None = None,
    api_path: str = "chat/completions",
    retries: int = 3,
    resume: bool = False,
) -> dict[str, Any]:
    """执行一次完整评分，并返回已写入合并 JSON 的内容。

    rubric_path 选择评分细则文件，未指定时使用 <skill_dir>/rubric/base_origin.yaml；
    <skill_dir>/rubric/base_rules.yaml 用 score_rules 打细则通过率；
    iterated_rubric (如 base_0.yaml) 改用 iterated_prompt.j2，由 LLM 对每个子维度打 0~1 分并给出 reasoning
    返回值保存在 output_dir/<trajectory.stem>.score.json

    resume=True 时只重打缺失或失败的维度，完成后合并结果
    """
    if retries < 1:
        raise ValueError("retries must be positive")
    templates, dimensions = load_dimension_skill(skill_dir, rubric_path)
    trajectory = trajectory.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pending = (
        tuple(find_failed_dimensions(trajectory, output_dir, dimensions))
        if resume else DIMENSIONS
    )
    processes = []
    # Fan out independent A-F evaluations.  Worker files are the hand-off
    # boundary: only after every child exits do we validate and merge them.
    for dimension in pending:
        output = output_dir / f"{trajectory.stem}.score.{dimension}.json"
        process = multiprocessing.Process(
            target=_dimension_worker,
            args=(dimension, str(trajectory), templates, dimensions[dimension], str(output),
                  base_url, api_key, model, api_path, timeout, retries),
            name=f"trajectory-score-{dimension}",
        )
        process.start()
        processes.append(process)
    for process in processes:
        process.join()
    # A crashed worker may leave no file; merge_dimension_scores represents it
    # as an evidence gap instead of failing the complete trajectory score.
    return merge_dimension_scores(trajectory, output_dir, dimensions, model=model)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 输入，并补全来自环境变量的连接配置。

    此函数只验证必填参数和正数边界，不读取轨迹、不创建目录也不发起网络请求，
    因而 ``--dry-run`` 可以安全地复用同一套参数校验。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trajectory", type=Path)
    parser.add_argument("--api-key", "--api_key", default=None)
    parser.add_argument("--base-url", "--base_url", default=os.environ.get("THETA_BASE_URL"))
    parser.add_argument("--model", required=False)
    parser.add_argument("--output-dir", "--output_dir", type=Path, default=None)
    parser.add_argument("--skill-dir", "--skill_dir", type=Path, default=DEFAULT_SKILL_DIR)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC,
                        help="评分细则文件；旧格式 pass/fail condition、子维度带 "
                             "score_rules 细则与权重的新格式，或 iterated_rubric 下"
                             "不带权重的 0~1 打分格式（默认 base_origin.yaml）")
    parser.add_argument("--dimension-api-path", default="chat/completions")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--retries", type=int, default=3,
                        help="retries for transport and invalid scoring responses (default: 3)")
    parser.add_argument("--resume", action="store_true",
                        help="只重打缺失或带 error 的维度，保留已完成的维度结果")
    parser.add_argument("--dry-run", "--dry_run", action="store_true")
    args = parser.parse_args(argv)
    args.api_key = args.api_key or os.environ.get("THETA_API_KEY")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.retries < 1:
        parser.error("--retries must be positive")
    if not args.model:
        parser.error("--model is required")
    if not args.base_url:
        parser.error("--base-url is required")
    if not args.api_key:
        parser.error("--api-key is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    trajectory = args.trajectory.expanduser().resolve()
    # 未指定输出目录时，与输入轨迹同级创建 scores；显式路径优先。
    output_dir = (args.output_dir or trajectory.parent / "scores").expanduser().resolve()
    if args.dry_run:
        print(f"dry-run: 6 dimensions -> {output_dir}")
        return 0
    print(f"scoring 1 trajectory across A-F (model={args.model}, output_dir={output_dir}, rubric={args.rubric})")
    if args.resume:
        _, dimensions = load_dimension_skill(args.skill_dir, args.rubric)
        pending = find_failed_dimensions(trajectory, output_dir, dimensions)
        if not pending:
            print("resume: all dimensions already valid; refreshing merged score only")
        else:
            kept = [d for d in DIMENSIONS if d not in pending]
            print(f"resume: re-scoring {','.join(pending)}"
                  + (f"; keeping {','.join(kept)}" if kept else ""))
    try:
        result = score_dimensions(
            trajectory, output_dir, api_key=args.api_key, base_url=args.base_url,
            model=args.model, timeout=args.timeout, skill_dir=args.skill_dir,
            rubric_path=args.rubric,
            api_path=args.dimension_api_path, retries=args.retries, resume=args.resume,
        )
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}")
        return 1
    print(f"done: total_score={result['total_score']} output={output_dir / (trajectory.stem + '.score.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
