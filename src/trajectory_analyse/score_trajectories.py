#!/usr/bin/env python3
"""Score one trajectory with the bundled dimension-based scoring skill."""

from __future__ import annotations

import argparse
import json
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
    retries: int = 3,
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
        time.sleep(min(2 ** (attempt + 1), 30))
    raise RuntimeError("API request failed")


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    candidates = [text]
    if "```" in text:
        parts = text.split("```")
        candidates.extend(part.strip().removeprefix("json").strip() for part in parts[1::2])
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
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
        passed = by_id.get(cid, {}).get("passed") is True
        value = float(item.get("score", 0)) if passed else 0.0
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
) -> None:
    output = Path(output_path)
    try:
        trajectory_text = Path(trajectory).read_text(encoding="utf-8", errors="replace")
        prompt = render_dimension_prompt(
            template, criterion, f"# trajectory path: {trajectory}\n{trajectory_text}"
        )
        text = _http_json(
            _build_api_url(base_url, api_path),
            {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            {"model": model, "messages": [{"role": "user", "content": prompt}],
             "temperature": 0, "response_format": {"type": "json_object"}},
            timeout,
        )
        result = _extract_json(text)
        valid, reason = validate_dimension_result(result, criterion)
        if not valid:
            raise RuntimeError(f"invalid dimension result: {reason}")
    except Exception as exc:
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
) -> dict[str, Any]:
    template, dimensions = load_dimension_skill(skill_dir)
    trajectory = trajectory.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    processes = []
    for dimension in DIMENSIONS:
        output = output_dir / f"{trajectory.stem}.score.{dimension}.json"
        process = multiprocessing.Process(
            target=_dimension_worker,
            args=(dimension, str(trajectory), template, dimensions[dimension], str(output),
                  base_url, api_key, model, api_path, timeout),
            name=f"trajectory-score-{dimension}",
        )
        process.start()
        processes.append(process)
    for process in processes:
        process.join()
    return merge_dimension_scores(trajectory, output_dir, dimensions, model=model)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trajectory", type=Path)
    parser.add_argument("--api-key", "--api_key", default=None)
    parser.add_argument("--base-url", "--base_url", default=os.environ.get("THETA_BASE_URL"))
    parser.add_argument("--model", required=False)
    parser.add_argument("--output-dir", "--output_dir", type=Path, default=None)
    parser.add_argument("--skill-dir", "--skill_dir", type=Path, default=DEFAULT_SKILL_DIR)
    parser.add_argument("--dimension-api-path", default="chat/completions")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--dry-run", "--dry_run", action="store_true")
    args = parser.parse_args(argv)
    args.api_key = args.api_key or os.environ.get("THETA_API_KEY")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if not args.model:
        parser.error("--model is required")
    if not args.base_url:
        parser.error("--base-url (or env $THETA_BASE_URL) is required")
    if not args.api_key:
        parser.error("--api-key (or env $THETA_API_KEY) is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    trajectory = args.trajectory.expanduser().resolve()
    output_dir = (args.output_dir or trajectory.parent / "scores").expanduser().resolve()
    if args.dry_run:
        print(f"dry-run: 6 dimensions -> {output_dir}")
        return 0
    print(f"scoring 1 trajectory across A-F (model={args.model}, output_dir={output_dir})")
    try:
        result = score_dimensions(
            trajectory, output_dir, api_key=args.api_key, base_url=args.base_url,
            model=args.model, timeout=args.timeout, skill_dir=args.skill_dir,
            api_path=args.dimension_api_path,
        )
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}")
        return 1
    print(f"done: total_score={result['total_score']} output={output_dir / (trajectory.stem + '.score.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
