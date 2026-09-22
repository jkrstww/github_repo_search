#!/usr/bin/env python3
"""使用内置的 A-F 评分细则，对一份轨迹 JSON 进行量化评分。

    python score_trajectories.py path/to/trajectory.json \\
        --model model --output-dir path/to/scores

``trajectory.json`` 是输入轨迹；``--output-dir`` 是可选的输出目录，未指定时
默认写入 ``<轨迹目录>/scores``。``--api-key``、``--base-url`` 也可直接传入，
分别覆盖上述环境变量。可先追加 ``--dry-run``，仅检查参数和目标输出目录。

输入与配置：
* 位置参数 ``trajectory`` 是 UTF-8 轨迹文件；其完整文本会分别传给六个维度的
  提示词。评分细则与 Jinja 模板默认从 ``trajectory_scoring_skill`` 读取。
* ``--model``、``--base-url``、``--api-key`` 决定目标模型和请求地址。超时、重试次数和 API
  路径会原样传递到每一个维度 worker。

执行与输出：先加载模板和细则，再并发启动 A-F 六个独立 worker。每个 worker 请求
LLM、提取并校验一个 JSON 对象，然后写入
``<轨迹名>.score.<维度>.json``。所有 worker 结束后，主进程将这些文件合并为
``<轨迹名>.score.json``。单个维度失败不会丢弃其他分数，而会在合并文件的
``evidence_gaps`` 中记录失败原因。
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
DIMENSIONS = tuple("ABCDEF")


def _load_rubric(skill_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to load rubric/base.yaml") from exc
    path = skill_dir / "rubric" / "base.yaml"
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
    return result


def load_dimension_skill(
    skill_dir: Path = DEFAULT_SKILL_DIR,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Load the only two runtime resources used by the scorer."""
    template = (skill_dir / "scoring_prompt.j2").read_text(encoding="utf-8")
    return template, _load_rubric(skill_dir)


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


def _dimension_score(
    result: dict[str, Any],
    criterion: dict[str, Any],
) -> tuple[float, list[dict[str, Any]]]:
    if result.get("error") or result.get("applicable") is False:
        return 0.0, []
    items = result.get("subcriteria")
    if not isinstance(items, list):
        return 0.0, []
    by_id = {item["id"]: item for item in items if isinstance(item, dict)}
    score = 0.0
    audit = []
    for item in criterion.get("subcriteria", []):
        cid = str(item["id"])
        worker_item = by_id.get(cid, {})
        if "score" in worker_item:
            value = float(worker_item["score"])
        else:
            # Backward compatibility for worker responses produced before
            # partial-credit scores were added to the prompt.
            value = float(item.get("score", 0)) if worker_item.get("passed") is True else 0.0
        score += value
        audit.append({
            "id": cid, "score": value, "max_score": float(item.get("score", 0)),
            "evidence": by_id.get(cid, {}).get("evidence", []),
            "reason": by_id.get(cid, {}).get("reason", ""),
        })
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
        maximum = float(criterion.get("max_score", 0))
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
        "total_score": total, "evidence_confidence": "low" if gaps else "medium",
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
    template: str,
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
    api_path: str = "chat/completions",
    retries: int = 3,
) -> dict[str, Any]:
    """执行一次完整评分，并返回已写入合并 JSON 的内容。

    ``trajectory`` 和 ``output_dir`` 会先展开并转换为绝对路径。调用方提供的
    ``api_key``、``base_url``、``model``、``api_path``、``timeout`` 与 ``retries``
    不作隐式改写，随后被传给每个维度的 ``_dimension_worker``；仅 URL 会由
    ``_build_api_url`` 规范化为 ``base_url/api_path``。返回值同时保存在
    ``output_dir/<trajectory.stem>.score.json``。
    """
    if retries < 1:
        raise ValueError("retries must be positive")
    template, dimensions = load_dimension_skill(skill_dir)
    trajectory = trajectory.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    processes = []
    # Fan out independent A-F evaluations.  Worker files are the hand-off
    # boundary: only after every child exits do we validate and merge them.
    for dimension in DIMENSIONS:
        output = output_dir / f"{trajectory.stem}.score.{dimension}.json"
        process = multiprocessing.Process(
            target=_dimension_worker,
            args=(dimension, str(trajectory), template, dimensions[dimension], str(output),
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
    parser.add_argument("--dimension-api-path", default="chat/completions")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--retries", type=int, default=3,
                        help="retries for transport and invalid scoring responses (default: 3)")
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
    print(f"scoring 1 trajectory across A-F (model={args.model}, output_dir={output_dir})")
    try:
        result = score_dimensions(
            trajectory, output_dir, api_key=args.api_key, base_url=args.base_url,
            model=args.model, timeout=args.timeout, skill_dir=args.skill_dir,
            api_path=args.dimension_api_path, retries=args.retries,
        )
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}")
        return 1
    print(f"done: total_score={result['total_score']} output={output_dir / (trajectory.stem + '.score.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
