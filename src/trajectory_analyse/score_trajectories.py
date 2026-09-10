#!/usr/bin/env python3
"""Blindly score ATIF agent trajectories via Codex (CLI) or an external model API.

The rubric prompt is inlined as the constant ``PROMPT_TEMPLATE`` (kept identical to
the ```text fenced block of trajectory_scoring_prompt.md, guarded by
test_prompt_template_inlined_matches_md), so the script is self-contained and does
not read that .md at runtime. Pass ``--prompt-file <path>`` to use a different
template instead.

Two judge backends share one prompt, one strict-JSON output contract, one
resume-able ``<base>.score.json`` layout, and the blind-scoring discipline (the
judge is never told ``resolved``):

* ``codex`` (default) — copies the single ``trajectory.json`` into a fresh temp
  directory and runs ``codex exec --sandbox read-only`` so it cannot read sibling
  submissions or leftover ``analyse.md`` reports.
* ``api`` — calls an OpenAI-compatible ``POST {api_base}/{api_path}``
  (default ``chat/completions``) chat endpoint you supply (``--api-base`` + token
  ``--api-key`` + ``--api-model``). The trajectory JSON is inlined into the
  single user message (an API cannot read files); the response's
  ``choices[0].message.content`` is then parsed as the rubric JSON. Pure stdlib
  ``urllib`` — no third-party SDK, matching the repo's zero-dependency policy.

Labels are joined only later in ``build_scores.py``. Failures are written to
``scoring_failures.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_FILE = SCRIPT_DIR / "trajectory_scoring_prompt.md"
DEFAULT_COMPARE = SCRIPT_DIR / "experiments" / "diff_trajectories" / "verified" / "compare_verified.json"
DEFAULT_TRAJS_ROOT = DEFAULT_COMPARE.parent

DIMENSIONS = ("A", "B", "C", "D", "E", "F")

# Inlined rubric prompt. Source of truth is the ```text fenced block of
# trajectory_scoring_prompt.md; this constant is kept byte-for-byte identical to it
# (guarded by test_prompt_template_inlined_matches_md). The script is self-contained:
# it uses this constant by default and no longer reads the .md at runtime unless you
# pass --prompt-file pointing at a different template.
PROMPT_TEMPLATE = """你是 SWE-bench 软件工程轨迹评审员。请评估文件 `{{TRAJECTORY_PATH}}` 中的
一条 agent 轨迹，并依据下方规则输出多维度过程分。你的评分对象是最终交付物
及其形成过程，不是模型名称、token、成本、轨迹长度或步骤数量。

## 读取和证据原则

1. 先完整读取并解析 trajectory.json。按时间顺序检查初始任务/issue、每轮 agent
   消息、工具调用及其原始输出、文件修改、测试输出、最终状态和提交/patch 信息。
   不要只看最后一轮、摘要、文件名或模型的自述。
2. 从轨迹本身建立 issue-specific oracle：必须实现的正向行为、必须保留的旧行为、
   边界/反例和禁止修改项。轨迹没有提供的信息必须标为未知，不能用常识补齐。
   如果轨迹明确引用了仓库文件但文件内容不在可读上下文中，只能把它作为“尝试读取”
   的证据，不能臆测其内容。
3. 每个判断都要引用可定位证据，优先使用 step/response 序号、工具调用序号、
   JSON 字段路径、命令和短引文。不要把“命令返回 0”自动当成业务正确。
4. 区分代码故障、测试故障、依赖/配置故障和基线故障。测试被跳过、输出被截断、
   通过 `|| true`/吞异常/管道末端状态掩盖失败时，必须记录为证据缺口或风险。
5. 只依据轨迹可观察事实评分。没有修前基线、修后真实断言、最终 diff 或完整退出码
   时降低相应维度，并在 `evidence_gaps` 中说明。不要要求或暴露模型隐藏思维链。
6. 在评分前先形成事实表：issue oracle、最终交付物/patch、时间线、环境和证据缺口。
   事实表记录“观察到什么”，评分字段记录“据此给多少分”，不要混为一谈。只使用轨迹
   内可观察证据，不读取、推断或输出外部评测标签。

## 评分维度（原始分，共 100 分）

每个维度先给 0 到该维度上限的原始分，可用整数或一位小数。优秀约为权重的
100%，合格约 70%，有限约 40%，缺失为 0%；档位之间必须根据证据比例给分。

- A 任务理解与验收标准（15）：准确复述失败机制、预期结果、非目标和兼容约束；
  给出可执行的正/反例矩阵。只复述 traceback/PR，或把“不报错”当成功，不能高分。
- B 根因定位与假设验证（15）：读取实现、调用者、相关测试/历史；建立数据/控制/
  状态因果链；在责任层验证并排除替代解释。关键词命中后盲改、只修表象要扣分。
- C 实现正确性、泛化与范围（25）：修复真实责任层并覆盖主场景和合法边界；保持
  API/状态/异常契约；改动最小且风格一致。只支持单一配置/父类、过宽异常捕获、
  改测试或环境代替改源码属于风险。
- D 测试设计与验证证据（25）：修前失败基线、同一路径修后 red→green、反例/关键边界、
  目标及合理相关回归、完整 stdout/stderr/返回码，并确认测试对应最终补丁。只有
  smoke/mock/静态检查/py_compile/打印成功不能高分。
- E 执行纪律与错误恢复（10）：命令窄且有目的；保留返回码和原始输出；正确区分故障；
  失败后更新假设并复测；临时改动可回退。重复无效命令、无 pipefail 管道、宽泛替换、
  无必要全局安装要扣分。
- F 收尾、补丁卫生与可审计性（10）：清理临时文件和测试改动；检查 git status、
  git diff --check；patch 非空、可应用且仅含授权源文件；提交和总结与证据一致。

各维度内部按以下子项分配上限，先分别判断子项再相加；子项之间不能重复计算同一
事实，必要时在证据中说明它影响了哪些不同子项：

- A：问题和成功标准 5；需保留的语义/约束 5；边界与复现计划 5。
- B：调用链和上下文 5；因果实验 5；责任层/替代解释判断 5。
- C：核心语义 10；边界与兼容性 7；通用性/抽象层 5；最小性与代码质量 3。
- D：修前基线 5；修后同测 5；反例与边界 5；目标/相关回归 5；证据完整性和最终状态一致性 5。
- E：命令与编辑纪律 3；错误分类 3；反馈驱动迭代 3；效率和风险控制 1。
- F：清理和状态检查 3；补丁范围/可应用性 4；提交协议和诚实总结 3。

测试数量不等于验证质量；能区分候选方案的反例和行为断言比重复跑大套件更有价值。
对每个维度同时记录 `score`、`max_score`、`level`（excellent/adequate/limited/
missing）和最多 3 条证据。证据必须说明它支持得分还是导致扣分。

## 封顶和标记

先计算 A-F 原始分，再应用所有适用封顶；取适用封顶中的最低总分限制。封顶只影响
对应维度和总分，不得用其他维度补回。输出的 `scores.*.score` 必须是封顶后的最终
分数；如果总分封顶仍低于六维度之和，从该封顶涉及的维度（优先 C/D/F）扣除超出
部分，并在 `key_risks` 或 `evidence_gaps` 说明调整原因，使六个最终维度分之和等于
`total_score`：

- 最终 patch 为空、不可应用，或遗漏必要 producer/consumer：F ≤ 2，C ≤ 3，总分 ≤ 49。
- 最终代码有确定性语法/导入/运行错误，或原始复现仍失败：C ≤ 6，D ≤ 8，总分 ≤ 59。
- 最终修改后没有任何可执行验证：D ≤ 5，总分 ≤ 74。
- 只有静态检查、编译或 mock，没有真实业务路径断言：D ≤ 10。
- 测试明确失败却因管道、`|| true`、捕获异常或只看末端返回码声称成功：D ≤ 10，E ≤ 4。
- 测试/环境/配置等无关修改进入最终补丁，或验证对象与提交物不一致：C ≤ 18，F ≤ 4。
- 已发现确定性反例却不解释、不修复仍收尾：C ≤ 18，D ≤ 14。

可使用的 `caps_or_flags` 标记只有：
`NO_BASELINE`, `WRONG_LAYER`, `MAIN_CASE_ONLY`, `NO_POST_FIX_TEST`, `PIPE_MASKED`,
`ENV_CONTAMINATION`, `KNOWN_COUNTEREXAMPLE`, `PATCH_MISMATCH`, `EMPTY_PATCH`,
`UNRELATED_CHANGES`。没有适用项时返回空数组。不要为了凑标记而猜测。

`evidence_confidence` 取 high/medium/low：high 需要最终 diff、真实调用路径断言、
完整测试输出和最终状态大体齐全；medium 表示有实质证据但缺一层；low 表示主要依赖
自述、截断输出、模拟或关键事实不可确认。

## 严格输出格式

只输出一个合法 JSON 对象，不要 Markdown、代码围栏、解释性前言或 JSON 外文字段。
所有分数必须是 number，且 `total_score` 等于应用封顶后的
`A.score + B.score + C.score + D.score + E.score + F.score`（允许 0.1 的舍入误差）。
使用以下结构：

{
  "trajectory": "{{TRAJECTORY_PATH}}",
  "scores": {
    "A": {"name": "任务理解与验收标准", "score": 0, "max_score": 15, "level": "missing", "evidence": []},
    "B": {"name": "根因定位与假设验证", "score": 0, "max_score": 15, "level": "missing", "evidence": []},
    "C": {"name": "实现正确性、泛化与范围", "score": 0, "max_score": 25, "level": "missing", "evidence": []},
    "D": {"name": "测试设计与验证证据", "score": 0, "max_score": 25, "level": "missing", "evidence": []},
    "E": {"name": "执行纪律与错误恢复", "score": 0, "max_score": 10, "level": "missing", "evidence": []},
    "F": {"name": "收尾、补丁卫生与可审计性", "score": 0, "max_score": 10, "level": "missing", "evidence": []}
  },
  "raw_total_score": 0,
  "total_score": 0,
  "evidence_confidence": "low",
  "caps_or_flags": [],
  "facts": {
    "final_artifact": {"modified_files": [], "patch_status": "unknown"},
    "timeline": [],
    "environment": [],
    "evidence_gaps": []
  },
  "oracle": {
    "positive_behavior": [],
    "negative_or_compatibility_behavior": [],
    "boundaries": [],
    "forbidden_changes": [],
    "unknowns": []
  },
  "evidence_gaps": [],
  "key_strengths": [],
  "key_risks": [],
  "conclusion": ""
}

`evidence`、`evidence_gaps`、`key_strengths` 和 `key_risks` 应简洁；每项包含
`location` 和 `observation`，必要时再加 `impact`。结论必须明确最终补丁是否可信、
未验证边界，以及哪些过程结论仍缺乏证据。若 JSON 无法解析或轨迹损坏，仍返回
上述结构：分数为 0，`evidence_confidence` 为 low，在 `evidence_gaps` 记录解析错误。"""

_log_lock = threading.Lock()


def extract_prompt(prompt_file: Path) -> str:
    """Pull the fenced ``text`` prompt block out of trajectory_scoring_prompt.md."""
    text = prompt_file.read_text(encoding="utf-8")
    m = re.search(r"```text\s*\n(.*?)\n```", text, re.S)
    if not m:
        raise RuntimeError(f"No ```text fenced prompt found in {prompt_file}")
    return m.group(1)


def _submission_base(sub_key: str) -> str:
    return sub_key[:-5] if sub_key.endswith(".json") else sub_key


def score_path_for(trajs_root: Path, instance: str, sub_key: str, judge_id: str | None) -> Path:
    base = _submission_base(sub_key)
    tag = f".{judge_id}" if judge_id else ""
    return trajs_root / instance / f"{base}.score{tag}.json"


def enumerate_pairs(
    compare_path: Path, trajs_root: Path, judge_id: str | None
) -> list[dict[str, Any]]:
    """Join compare labels with on-disk trajectory files into a work list."""
    labels = json.loads(compare_path.read_text(encoding="utf-8"))
    pairs: list[dict[str, Any]] = []
    for instance in sorted(labels):
        for sub_key in sorted(labels[instance]):
            traj = trajs_root / instance / sub_key
            pairs.append(
                {
                    "instance": instance,
                    "submission": _submission_base(sub_key),
                    "traj_path": str(traj),
                    "score_path": str(score_path_for(trajs_root, instance, sub_key, judge_id)),
                    "resolved": bool(labels[instance][sub_key]),
                    "exists": traj.is_file(),
                }
            )
    return pairs


def _run_codex(prompt: str, cwd: Path, *, codex_command: str, timeout: int) -> str:
    """Run Codex in a read-only sandbox and return its stdout text."""
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
        str(cwd),
        prompt,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment specific
        raise RuntimeError(f"Codex command not found: {codex_command}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Codex timed out after {timeout}s") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise RuntimeError(f"Codex exited {result.returncode}: {detail}")
    out = (result.stdout or "").strip()
    if not out:
        raise RuntimeError("Codex returned empty stdout")
    return out


def _safe_model(name: str | None) -> str:
    """Filesystem-safe judge_id derived from a model name (e.g. 'gpt-5.6-sol')."""
    if not name:
        return "api"
    out = "".join(c if c.isalnum() or c in "._-" else "_" for c in name).strip("._")
    return out or "api"


def _build_api_url(api_base: str, api_path: str) -> str:
    return api_base.rstrip("/") + "/" + api_path.lstrip("/")


def _first_text(resp: dict[str, Any]) -> str | None:
    """Extract assistant text from an OpenAI- or Anthropic-shaped chat response.

    Some reasoning model backends (e.g. DeepSeek reasoning models) return the answer
    in ``message.reasoning_content`` with ``message.content`` empty, so fall back to
    ``reasoning_content`` when ``content`` is absent/blank.
    """
    try:
        choices = resp.get("choices")
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message") or {}
            if isinstance(msg, dict):
                for key in ("content", "reasoning_content"):
                    val = msg.get(key)
                    if isinstance(val, str) and val.strip():
                        return val
    except Exception:
        pass
    try:
        content = resp.get("content")
        if content and isinstance(content[0], dict) and content[0].get("text"):
            return content[0]["text"]
    except Exception:
        pass
    return None


def _inline_prompt(pair: dict[str, Any], prompt_template: str, truncate: int) -> str:
    """Prompt with the trajectory JSON inlined (an API judge cannot read files)."""
    src = pair["traj_path"]
    content = Path(src).read_text(encoding="utf-8", errors="replace")
    if truncate > 0 and len(content) > truncate:
        content = content[:truncate]
    header = (
        "\n\n# --- trajectory.json 内容（已内联，请按时间顺序通读所有 step 后再评分）---\n"
    )
    footer = "\n# --- trajectory.json 内容结束 ---\n"
    return prompt_template.replace("{{TRAJECTORY_PATH}}", str(src)) + header + content + footer


def _is_transient(exc: BaseException) -> bool:
    """True for network/transport errors and 5xx/429-class HTTP codes worth retrying.

    Order matters: ``URLError``/``OSError`` are parents of ``HTTPError``, so HTTPError is
    classified by status code first and must NOT be caught by the generic OSError branch.
    """
    import http.client as _h
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 425, 429, 500, 502, 503, 504)
    if isinstance(exc, _h.HTTPException):
        return True  # RemoteDisconnected / BadStatusLine / IncompleteRead
    if isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError,
                       ConnectionError, OSError)):
        return True
    return False


def _http_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: int,
               *, retries: int = 3, backoff: float = 2.0) -> str:
    """POST JSON, return the decoded assistant text. Raises RuntimeError on failure.

    Transient errors (transport resets, 429/402/5xx served as 429) are retried in-place
    with exponential backoff so a single trajectory holds one concurrency slot while
    the gateway churns, instead of giving up after one attempt. Non-transient HTTP
    errors (4xx other than the retry set) fail immediately.
    """
    import http.client as _http  # local import to avoid a hard module-level dependency
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_exc: BaseException | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            if _is_transient(exc) and attempt < retries:
                last_exc = exc
                time.sleep(min(backoff ** attempt, 30.0))
                continue
            raise RuntimeError(f"HTTP {exc.code}: {detail}")
        except (urllib.error.URLError, socket.timeout, TimeoutError,
               _http.HTTPException, ConnectionError, OSError) as exc:
            if attempt < retries:
                last_exc = exc
                time.sleep(min(backoff ** attempt, 30.0))
                continue
            raise RuntimeError(f"network error: {type(exc).__name__}: {exc}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # non-JSON is a model/contract problem, not transient -> fail fast
            raise RuntimeError(f"non-JSON response: {raw[:200]}")
        text = _first_text(parsed)
        if not text:
            raise RuntimeError(f"empty/unparsable content: {raw[:200]}")
        return text
    raise RuntimeError(f"network error: exhausted {retries} retries: {last_exc}")


def _run_api(prompt: str, args: argparse.Namespace) -> str:
    """Call an OpenAI-compatible chat-completions endpoint and return its text."""
    body: dict[str, Any] = {
        "model": args.api_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": args.api_temperature,
        "max_tokens": args.api_max_tokens,
    }
    if args.api_json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {args.api_key}",
    }
    return _http_json(_build_api_url(args.api_base, args.api_path), headers, body, args.timeout)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Parse judge output as a JSON object, tolerating fences and prose noise."""
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    if "```" in text:
        parts = text.split("```")
        for chunk in parts[1::2]:
            chunk = chunk.strip()
            if chunk[:4].lower() in ("json",):
                chunk = chunk[4:].lstrip()
            try:
                obj = json.loads(chunk)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    lo = text.find("{")
    hi = text.rfind("}")
    if lo != -1 and hi > lo:
        try:
            obj = json.loads(text[lo : hi + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def validate_score(obj: Any) -> tuple[bool, str]:
    """Check the rubric's own structural invariant: total == sum(A..F)."""
    if not isinstance(obj, dict):
        return False, "not an object"
    scores = obj.get("scores")
    if not isinstance(scores, dict):
        return False, "missing scores"
    total = 0.0
    for dim in DIMENSIONS:
        entry = scores.get(dim)
        if not isinstance(entry, dict) or "score" not in entry:
            return False, f"dimension {dim} malformed"
        raw = entry.get("score")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return False, f"dimension {dim} score not numeric"
        total += float(raw)
    ts = obj.get("total_score")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False, "total_score not numeric"
    if abs(float(ts) - total) > 0.1:
        return False, f"total_score {ts} != sum {round(total, 2)}"
    return True, ""


def _placeholder(pair: dict[str, Any], reason: str, retried: bool) -> dict[str, Any]:
    return {
        "scores": {d: {"name": "", "score": 0, "max_score": 0, "level": "missing",
                       "evidence": []} for d in DIMENSIONS},
        "raw_total_score": 0,
        "total_score": 0,
        "evidence_confidence": "low",
        "caps_or_flags": [],
        "facts": {
            "final_artifact": {"modified_files": [], "patch_status": "unknown"},
            "timeline": [], "environment": [], "evidence_gaps": [{"location": "parse",
                "observation": reason}],
        },
        "oracle": {"positive_behavior": [], "negative_or_compatibility_behavior": [],
                    "boundaries": [], "forbidden_changes": [], "unknowns": []},
        "evidence_gaps": [{"location": "parse", "observation": reason}],
        "key_strengths": [], "key_risks": [], "conclusion": "",
        "instance": pair["instance"], "submission": pair["submission"],
        "traj_path": pair["traj_path"],
        "_meta": {"judge_id": None, "scored_at_unix": int(time.time()),
                  "parse_ok": False, "valid": False, "retried": retried, "reason": reason},
    }


def _load_existing(path: Path) -> dict[str, Any] | None:
    """Return a previously written score if it is a *real* (non-placeholder) valid score.

    Structural validity (``total == sum(A..F)``) is necessary but not sufficient: a
    parse-failure placeholder also has six zero scores summing to 0, so it would
    otherwise be mistaken for a real score and skipped on resume. Require the
    ``_meta.valid`` flag (True) when present; files without ``_meta`` (e.g. an
    older/cached score) fall back to structural check only.
    """
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    ok, _ = validate_score(obj)
    if not ok:
        return None
    meta = obj.get("_meta")
    if isinstance(meta, dict) and "valid" in meta:
        return obj if bool(meta.get("valid")) else None
    return obj


def _score_one(pair: dict[str, Any], prompt_template: str, args: argparse.Namespace) -> str:
    """Score a single pair. Returns a short status string."""
    score_path = Path(pair["score_path"])
    if not args.force:
        if _load_existing(score_path) is not None:
            return "skipped"
    if not pair["exists"]:
        _log_failure(args, pair, "missing_trajectory", retried=False)
        return "missing_traj"
    out = _attempt(pair, prompt_template, args)
    retried = False
    if not (isinstance(out, dict) and validate_score(out)[0]):
        retried = True
        second = _attempt(pair, prompt_template, args)
        if isinstance(second, dict) and validate_score(second)[0]:
            out = second  # recovered on retry
    if isinstance(out, dict) and validate_score(out)[0]:
        rec = dict(out)
        parse_ok = True
        reason = ""
    else:
        parse_ok = False
        reason = out if (isinstance(out, str) and out) else "parse_failed"
        rec = _placeholder(pair, reason, retried)
    rec["instance"] = pair["instance"]
    rec["submission"] = pair["submission"]
    rec["traj_path"] = pair["traj_path"]
    rec["_meta"] = {
        "judge_id": args.judge_id,
        "scored_at_unix": int(time.time()),
        "parse_ok": parse_ok,
        "valid": parse_ok,
        "retried": retried,
        "reason": reason,
    }
    score_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    if parse_ok:
        return "scored"
    _log_failure(args, pair, reason, retried=retried)
    return "invalid"


def _attempt(pair: dict[str, Any], prompt_template: str, args: argparse.Namespace) -> Any:
    """Run one judge call for this pair. Returns a parsed dict or an error string."""
    if args.judge_backend == "codex":
        text = _attempt_codex(pair, prompt_template, args)
    elif args.judge_backend == "api":
        text = _attempt_api(pair, prompt_template, args)
    else:
        return f"bad_backend:{args.judge_backend}"
    if isinstance(text, str) and text.startswith("runtime:"):
        return text
    if not isinstance(text, str):
        return "parse_failed"
    obj = _extract_json(text)
    return obj if obj is not None else "parse_failed"


def _attempt_codex(pair: dict[str, Any], prompt_template: str, args: argparse.Namespace) -> str:
    """Run Codex once for this pair in an isolated temp dir; returns text or runtime:..."""
    with tempfile.TemporaryDirectory() as td:
        tmptraj = Path(td) / "trajectory.json"
        try:
            shutil.copy2(pair["traj_path"], tmptraj)
        except OSError:
            return "runtime:copy_failed"
        prompt = prompt_template.replace("{{TRAJECTORY_PATH}}", str(tmptraj))
        try:
            return _run_codex(prompt, Path(td), codex_command=args.codex_command, timeout=args.timeout)
        except RuntimeError as exc:
            return f"runtime:{exc}"


def _attempt_api(pair: dict[str, Any], prompt_template: str, args: argparse.Namespace) -> str:
    """Call the external model API once for this pair; returns text or runtime:..."""
    prompt = _inline_prompt(pair, prompt_template, args.api_truncate)
    try:
        return _run_api(prompt, args)
    except RuntimeError as exc:
        return f"runtime:{exc}"


def _log_failure(args: argparse.Namespace, pair: dict[str, Any], reason: str, *, retried: bool) -> None:
    record = {
        "instance": pair["instance"], "submission": pair["submission"],
        "traj_path": pair["traj_path"], "reason": reason, "retried": retried,
        "at_unix": int(time.time()),
    }
    log_path = args.trajs_root / "scoring_failures.jsonl"
    with _log_lock:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--compare", type=Path, default=DEFAULT_COMPARE,
                   help="compare_* json mapping instance -> {submission.json: resolved}")
    p.add_argument("--trajs-root", "--trajs_root", type=Path, default=DEFAULT_TRAJS_ROOT,
                   help="root of <instance>/<submission>.json trajectory files")
    p.add_argument("--prompt-file", "--prompt_file", type=Path, default=DEFAULT_PROMPT_FILE)
    p.add_argument(
        "--judge-backend", "--judge_backend", choices=("codex", "api"), default="codex",
        help="codex = local `codex exec` CLI; api = external OpenAI-compatible chat/completions",
    )
    p.add_argument("--codex-command", "--codex_command", default="codex")
    p.add_argument("--timeout", type=int, default=1800,
                   help="per-call timeout (s); applies to both backends")
    p.add_argument("--concurrency", type=int, default=4, help="parallel judge calls")
    p.add_argument("--max-pairs", "--max_pairs", type=int, default=0,
                   help="cap total pairs processed (0 = no cap)")
    p.add_argument("--instance", default=None, help="restrict to a single instance id")
    p.add_argument("--judge-id", "--judge_id", default=None,
                   help="tag judge run; writes <base>.score.<id>.json (api default = model name)")
    p.add_argument("--force", action="store_true", help="re-score even if a valid score exists")
    p.add_argument("--dry-run", "--dry_run", action="store_true",
                   help="enumerate work list and exit without calling the judge")
    # ---- external model API backend (judge-backend=api) ----
    api = p.add_argument_group("api backend", "OpenAI-compatible chat/completions endpoint")
    api.add_argument("--api-base", "--api_base", default=None,
                     help="API base URL, e.g. https://api.example.com/v1 (required for api)")
    api.add_argument("--api-model", "--api_model", default=None, help="model id (required for api)")
    api.add_argument("--api-key", "--api_key", default=None, help="bearer token; else read from --api-key-env")
    api.add_argument("--api-key-env", "--api_key_env", default="OPENAI_API_KEY",
                     help="env var name holding the token when --api-key is omitted")
    api.add_argument("--api-path", "--api_path", default="chat/completions",
                     help="endpoint path appended to --api-base")
    api.add_argument("--api-temperature", "--api_temperature", type=float, default=0.0)
    api.add_argument("--api-max-tokens", "--api_max_tokens", type=int, default=32768)
    api.set_defaults(api_json_mode=True)
    api.add_argument("--no-api-json-mode", "--no_api_json_mode", dest="api_json_mode",
                     action="store_false", help="do not request response_format=json_object")
    api.add_argument("--api-truncate", "--api_truncate", type=int, default=0,
                     help="truncate inlined trajectory JSON to N chars (0 = no truncation)")
    args = p.parse_args(argv)
    if args.timeout < 1:
        p.error("--timeout must be positive")
    if args.concurrency < 1:
        p.error("--concurrency must be positive")
    if args.judge_backend == "api":
        if not args.api_base:
            p.error("--api-base is required with --judge-backend api")
        if not args.api_model:
            p.error("--api-model is required with --judge-backend api")
        if not args.api_key:
            args.api_key = os.environ.get(args.api_key_env)
        if not args.api_key:
            p.error(f"--api-key (or env ${args.api_key_env}) is required with --judge-backend api")
        if args.api_max_tokens < 1:
            p.error("--api-max-tokens must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.compare = args.compare.expanduser().resolve()
    args.trajs_root = args.trajs_root.expanduser().resolve()
    if args.judge_id is None and args.judge_backend == "api":
        args.judge_id = _safe_model(args.api_model)
    prompt_file = args.prompt_file.expanduser().resolve()
    if prompt_file == DEFAULT_PROMPT_FILE:
        prompt_template = PROMPT_TEMPLATE  # self-contained: no .md read at runtime
    else:
        prompt_template = extract_prompt(prompt_file)  # user-supplied template
    pairs = enumerate_pairs(args.compare, args.trajs_root, args.judge_id)
    if args.instance:
        pairs = [p for p in pairs if p["instance"] == args.instance]
    if args.max_pairs:
        pairs = pairs[: args.max_pairs]
    missing = [p for p in pairs if not p["exists"]]
    if args.dry_run:
        extra = ""
        if args.judge_backend == "api":
            extra = (f" | api: {_build_api_url(args.api_base, args.api_path)} "
                     f"model={args.api_model} json_mode={args.api_json_mode}")
        print(f"dry-run: {len(pairs)} pairs ({len(missing)} missing trajectory on disk){extra}")
        for p in pairs[:8]:
            print(f"  {p['instance']}/{p['submission']} resolved={p['resolved']} exists={p['exists']}")
        return 0
    back = args.judge_backend
    detail = back
    if back == "api":
        detail = f"api {args.api_model} @ {_build_api_url(args.api_base, args.api_path)}"
    print(f"scoring {len(pairs)} pairs (judge_id={args.judge_id or 'default'}, "
          f"backend={detail}, concurrency={args.concurrency}, timeout={args.timeout}s)")
    counts = {"scored": 0, "skipped": 0, "missing_traj": 0, "invalid": 0}
    submitted = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = {ex.submit(_score_one, p, prompt_template, args): p for p in pairs}
        for fut in as_completed(futures):
            submitted += 1
            try:
                status = fut.result()
            except Exception as exc:  # pragma: no cover - defensive
                status = "invalid"
                _log_failure(args, futures[fut], f"unhandled:{exc}", retried=False)
            counts[status] = counts.get(status, 0) + 1
            if submitted % 25 == 0 or submitted == len(pairs):
                print(f"  [{submitted}/{len(pairs)}] {counts}")
    print("done:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
