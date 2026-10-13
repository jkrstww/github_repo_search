#!/usr/bin/env python3
"""批量对 jobs 目录下的所有轨迹执行 A-F 量化评分（复用 score_trajectories.py）。

用法:
  # 单个 jobs 目录 (53 条轨迹约 1-2 小时)
  python3 tools/batch_score_trajectories.py <jobs_dir>

  # 多个 jobs 目录依次打分
  python3 tools/batch_score_trajectories.py jobs/glm-5.2/new jobs/kimi-k2.6/new

  # 只预检（探活网关 + 列出轨迹数量），不发真实打分请求
  python3 tools/batch_score_trajectories.py jobs/glm-5.2/new --dry-run

  # 指定打分模板区分结果：换 rubric 时用独立 scores 子目录与汇总文件名
  python3 tools/batch_score_trajectories.py jobs/glm-5.2/new \
      --rubric configs/rubrics/iterated_rubric/base_0.yaml \
      --scores-subdir scores_base0 --summary-filename scoring_summary_base0.json

输入的每个 jobs 目录会递归发现 <jobs>/<trial>/agent/trajectory.json 下的轨迹
评分结果写到每条轨迹自己的 <trial>/agent/<scores-subdir>/ 打分结果汇总到 <jobs_dir>/<summary-filename>

执行流程:
  1. 探活判分网关 (5 token 小请求)，失败立即退出
  2. 逐条轨迹评分
  3. 只对仍有失败维度的轨迹重打失败维度，默认 --rounds 上限 3 轮
  4. 兜底抢救，对收敛轮打不平的顽固维度，单独请求判分模型并解析
  5. 打印并写入每个 jobs 目录下的 summary-filename
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "trajectory_analyse"))

import score_trajectories as st  # noqa: E402

# 以下仅为 argparse 的默认值，均可通过命令行参数覆盖，便于区分
# 不同打分模板 / 判分模型的输出。
DEFAULT_MODEL = "Kimi-K2.6-SPOT"
DEFAULT_SUMMARY_FILENAME = "scoring_summary.json"
DEFAULT_SCORES_SUBDIR = "scores"


def discover_trajectories(jobs_dir: Path) -> list[Path]:
    """列出 jobs 目录下所有 ``<trial>/agent/trajectory.json``，按路径排序。"""
    return sorted(p for p in jobs_dir.rglob("agent/trajectory.json") if p.is_file())


def scores_dir(trajectory: Path, scores_subdir: str) -> Path:
    """每条轨迹的输出目录：``<trial>/agent/<scores_subdir>``。"""
    return trajectory.parent / scores_subdir


def probe_model(base_url: str, api_key: str, model: str, timeout: int = 60) -> None:
    """发一个 5 token 小请求确认网关与判分模型可用（spot 容量会间歇性下线）。

    异常时抛出 RuntimeError；调用方在真实打分前用它做 pre-flight。
    """
    body = {"model": model, "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5}
    request = urllib.request.Request(
        st._build_api_url(base_url, "chat/completions"),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"探活失败: HTTP {exc.code} ({exc.read().decode('utf-8', 'replace')[:200]})；"
            "判分模型可能未上线（spot 容量间歇下线），或网关/凭证配置有误") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"探活失败: 网关不可达: {exc}") from exc
    if not isinstance(parsed, dict) or not st._first_text(parsed):
        raise RuntimeError(f"探活失败: 网关返回异常: {str(parsed)[:200]}")


def _all_json_objects(text: str) -> list[dict[str, Any]]:
    """逐位置 ``raw_decode`` 解析响应中的所有 JSON 对象（不拼接首尾大括号）。"""
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def salvage_dimension(
    trajectory: Path,
    dimension: str,
    template: str,
    criterion: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    model: str,
    api_path: str,
    timeout: int,
    attempts: int,
    scores_subdir: str,
) -> bool:
    """对单个顽固维度做兜底抢救：解析响应中的全部对象，挑校验通过的落盘。

    判分模型偶发在响应里混入大量 ``criterion_id=None`` 的 JSON 碎片，
    ``st._extract_json`` 取到的第一个对象不一定是真结果，因此这里扫描全部
    对象，直到找到 ``criterion_id`` 匹配且通过 ``validate_dimension_result``
    的那一个，直接写入 worker 文件（由调用方重新合并）。
    """
    trajectory_text = trajectory.read_text(encoding="utf-8", errors="replace")
    prompt = st.render_dimension_prompt(
        template, criterion,
        f"# trajectory path: {trajectory}\n{trajectory_text}",
    )
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "response_format": {"type": "json_object"}}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    for attempt in range(attempts):
        try:
            # _http_json 内部 retries=1；重试节奏交由本层控制，便于逐次报告。
            text = st._http_json(
                st._build_api_url(base_url, api_path), headers, body, timeout,
            )
        except Exception as exc:
            print(f"    [salvage] 请求失败: {exc}")
            time.sleep(min(2 ** (attempt + 2), 60))
            continue
        for candidate in _all_json_objects(text):
            if candidate.get("criterion_id") != dimension:
                continue
            valid, reason = st.validate_dimension_result(candidate, criterion)
            if valid:
                output = scores_dir(trajectory, scores_subdir) / f"{trajectory.stem}.score.{dimension}.json"
                output.write_text(
                    json.dumps(candidate, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return True
            print(f"    [salvage] 候选对象未通过校验: {reason}")
        time.sleep(min(2 ** (attempt + 2), 60))
    return False


def score_jobs_dir(
    jobs_dir: Path,
    trajectories: list[Path],
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int,
    retries: int,
    rounds: int,
    salvage: bool,
    salvage_attempts: int,
    force: bool,
    rubric_path: Path | None = None,
    scores_subdir: str = DEFAULT_SCORES_SUBDIR,
    summary_filename: str = DEFAULT_SUMMARY_FILENAME,
) -> dict[str, Any]:
    """完成一个 jobs 目录的全部评分并返回汇总（写入 jobs_dir/<summary_filename>）。"""
    template, dimensions = st.load_dimension_skill(st.DEFAULT_SKILL_DIR, rubric_path)
    common = {"api_key": api_key, "base_url": base_url, "model": model,
              "timeout": timeout, "skill_dir": st.DEFAULT_SKILL_DIR,
              "api_path": "chat/completions", "rubric_path": rubric_path}

    def label(traj: Path) -> str:
        """轨迹在 jobs 目录内的相对路径（如 ``<trial>/agent``），用于日志与汇总。"""
        return str(traj.parent.relative_to(jobs_dir))

    def failed_of(traj: Path) -> list[str]:
        return st.find_failed_dimensions(traj, scores_dir(traj, scores_subdir), dimensions)

    def broken_map() -> dict[Path, list[str]]:
        return {t: dims for t in trajectories if (dims := failed_of(t))}

    print(f"\n=== round 1: {jobs_dir}（{len(trajectories)} 条轨迹） ===")
    for index, traj in enumerate(trajectories, 1):
        pending = list(st.DIMENSIONS) if force else failed_of(traj)
        if not pending:
            print(f"[{index}/{len(trajectories)}] {label(traj)}: 六维度已有效，跳过")
            continue
        print(f"[{index}/{len(trajectories)}] {label(traj)}: "
              f"打分维度 {','.join(pending)}")
        try:
            st.score_dimensions(traj, scores_dir(traj, scores_subdir), resume=not force,
                                retries=retries, **common)
        except Exception as exc:
            print(f"[{index}/{len(trajectories)}] {label(traj)}: 打分失败: {exc}")

    # 收敛轮：只碰仍有失败维度的轨迹，逐轮收敛直至为空或达到 --rounds 上限。
    for round_no in range(2, rounds + 1):
        broken = broken_map()
        if not broken:
            print("=== 收敛完成：全部轨迹无失败维度 ===")
            break
        print(f"=== round {round_no}/{rounds}: {len(broken)} 条轨迹共 "
              f"{sum(len(d) for d in broken.values())} 个失败维度待补打 ===")
        for traj, pending in broken.items():
            print(f"  {label(traj)}: 补打 {','.join(pending)}")
            try:
                st.score_dimensions(traj, scores_dir(traj, scores_subdir), resume=True,
                                    retries=retries, **common)
            except Exception as exc:
                print(f"  {label(traj)}: 补打失败: {exc}")

    # 兜底抢救：逐维度单点请求，成功后对涉及轨迹重新合并。
    broken = broken_map()
    if broken and salvage:
        total = sum(len(d) for d in broken.values())
        print(f"=== salvage: {len(broken)} 条轨迹共 {total} 个顽固维度，"
              "做全对象解析抢救 ===")
        for traj, pending in broken.items():
            for dimension in pending:
                print(f"  {label(traj)}/{dimension}: salvage 尝试")
                criterion = dimensions[dimension]
                ok = salvage_dimension(
                    traj, dimension, template[str(criterion.get("scoring_mode", "legacy"))],
                    criterion,
                    base_url=base_url, api_key=api_key, model=model,
                    api_path="chat/completions", timeout=timeout,
                    attempts=salvage_attempts, scores_subdir=scores_subdir,
                )
                print(f"  {label(traj)}/{dimension}: salvage "
                      f"{'成功' if ok else '失败'}")
        for traj in broken:
            st.merge_dimension_scores(traj, scores_dir(traj, scores_subdir), dimensions, model=model)

    # 终核 + 汇总。
    entries: list[dict[str, Any]] = []
    gaps_report: dict[str, list[str]] = {}
    scores: list[float] = []
    for traj in trajectories:
        merged_path = scores_dir(traj, scores_subdir) / f"{traj.stem}.score.json"
        try:
            merged = json.loads(merged_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            merged = {"total_score": 0.0,
                      "evidence_gaps": [{"location": "merged",
                                         "observation": f"merged file unreadable: {exc}"}]}
        gaps = [f"{g['location']}: {g['observation']}"
                for g in merged.get("evidence_gaps", [])]
        value = float(merged.get("total_score", 0.0))
        scores.append(value)
        if gaps:
            gaps_report[label(traj)] = gaps
        entries.append({"trial": label(traj), "total_score": value, "gaps": gaps})
    summary = {
        "jobs_dir": str(jobs_dir), "model": model,
        "rubric": str(rubric_path) if rubric_path else None,
        "scores_subdir": scores_subdir,
        "trajectory_count": len(trajectories),
        "mean_total_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "min_total_score": min(scores) if scores else 0.0,
        "max_total_score": max(scores) if scores else 0.0,
        "trajectories_with_gaps": gaps_report,
        "results": entries,
    }
    (jobs_dir / summary_filename).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("jobs_dirs", nargs="+", type=Path,
                        help="一个或多个 jobs 目录，各含 <trial>/agent/trajectory.json")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"判分模型（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--scores-subdir", "--scores_subdir",
                        default=DEFAULT_SCORES_SUBDIR,
                        help=f"每条轨迹的评分输出子目录名，位于 <trial>/agent/ 下"
                             f"（默认 {DEFAULT_SCORES_SUBDIR}）；切换打分模板/rubric 时"
                             f"可指定不同子目录，避免混用不同模板的维度结果")
    parser.add_argument("--summary-filename", "--summary_filename",
                        default=DEFAULT_SUMMARY_FILENAME,
                        help=f"每个 jobs 目录下汇总文件名"
                             f"（默认 {DEFAULT_SUMMARY_FILENAME}）；切换打分模板"
                             f"时可改名以区分多套结果")
    parser.add_argument("--rubric", type=Path, default=None,
                        help="评分细则文件，未指定时使用默认 base_origin.yaml；"
                             "支持 score_rules 与 iterated 格式")
    parser.add_argument("--base-url", "--base_url",
                        default=os.environ.get("THETA_BASE_URL_2"),
                        help="判分网关，默认取 THETA_BASE_URL_2（/v1；默认 THETA_BASE_URL "
                             "的 /api 网关会 404，本脚本不做静默回退）")
    parser.add_argument("--api-key", "--api_key",
                        default=os.environ.get("THETA_API_KEY_2"),
                        help="判分网关 key，默认取 THETA_API_KEY_2")
    parser.add_argument("--timeout", type=int, default=600,
                        help="单次维度请求超时秒数（默认 600；大轨迹可调大）")
    parser.add_argument("--retries", type=int, default=3,
                        help="score_trajectories 单维度传输/校验重试次数（默认 3）")
    parser.add_argument("--rounds", type=int, default=3,
                        help="resume 补打收敛轮数上限（round 1 全量打分不计入，默认 3）")
    parser.add_argument("--salvage-attempts", "--salvage_attempts", type=int, default=5,
                        help="单个顽固维度的 salvage 尝试次数（默认 5）")
    parser.add_argument("--no-salvage", "--no_salvage", action="store_true",
                        help="关闭顽固维度的全对象解析兜底抢救")
    parser.add_argument("--force", action="store_true",
                        help="忽略已完成的维度结果，全部重打（默认增量 resume，"
                             "已完成维度原样保留）")
    parser.add_argument("--dry-run", "--dry_run", action="store_true",
                        help="只探活并列出各目录轨迹数量，不发打分请求")
    args = parser.parse_args(argv)
    if args.timeout < 1 or args.retries < 1 or args.rounds < 1:
        parser.error("--timeout/--retries/--rounds must be positive")
    if not (args.base_url and args.api_key):
        parser.error(
            "缺少判分网关配置：请设置 THETA_BASE_URL_2 / THETA_API_KEY_2，"
            "或通过 --base-url/--api-key 显式传入 /v1 网关凭证")
    for jobs_dir in args.jobs_dirs:
        if not jobs_dir.is_dir():
            parser.error(f"jobs 目录不存在: {jobs_dir}")
    if Path(args.summary_filename).name != args.summary_filename:
        parser.error(f"--summary-filename 只能是文件名，不能含路径: {args.summary_filename}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"探活判分网关: {args.base_url} + {args.model}")
    probe_model(args.base_url, args.api_key, args.model)
    plans: list[tuple[Path, list[Path]]] = []
    for jobs_dir in args.jobs_dirs:
        resolved = jobs_dir.expanduser().resolve()
        trajectories = discover_trajectories(resolved)
        plans.append((resolved, trajectories))
        print(f"{resolved}: 发现 {len(trajectories)} 条轨迹")
        if not trajectories:
            print(f"  警告: 未发现任何 <trial>/agent/trajectory.json")
    if args.dry_run:
        print("dry-run 结束：未发起任何打分请求")
        return 0

    exit_code = 0
    for jobs_dir, trajectories in plans:
        if not trajectories:
            continue
        summary = score_jobs_dir(
            jobs_dir, trajectories,
            base_url=args.base_url, api_key=args.api_key, model=args.model,
            timeout=args.timeout, retries=args.retries, rounds=args.rounds,
            salvage=not args.no_salvage, salvage_attempts=args.salvage_attempts,
            force=args.force, rubric_path=args.rubric,
            scores_subdir=args.scores_subdir, summary_filename=args.summary_filename,
        )
        gaps = summary["trajectories_with_gaps"]
        print(f"\n=== {jobs_dir} 完成: "
              + ("全部轨迹打分成功" if not gaps
                 else f"{len(gaps)} 条轨迹仍有失败维度")
              + f"；总均分 {summary['mean_total_score']}，"
              f"范围 {summary['min_total_score']}~{summary['max_total_score']}，"
              f"明细见 {jobs_dir / args.summary_filename} ===")
        for trial, gap_list in gaps.items():
            for gap in gap_list:
                print(f"  {trial}: {gap}")
        if gaps:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())