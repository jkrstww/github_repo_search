import json
import tempfile
import unittest
from pathlib import Path

import summarize_scores


RUBRIC_YAML = """rubric:
  name: Test Rubric
  total_score: 100

  criteria:

    - id: A
      name: 任务理解
      description: >
        评估任务理解。
      max_score: 15
      subcriteria:

        - id: A1
          name: 理解问题并定义成功标准
          description: >
            评估成功标准。
          score: 5
          positive_examples:
            - >
              示例：score: 5 不应被误解析。

        - id: A2
          name: 保留必需的语义与约束
          score: 5

    - id: D
      name: 测试与验证
      max_score: 25
      subcriteria:

        - id: D1
          name: 修改前失败复现
          score: 5
"""


def _merged_score(a: float, d: float, total: float, rules_based: bool = False) -> dict:
    if rules_based:
        # score_rules rubric: subcriterion score == pass_rate (0..1), plus
        # rule-level detail written by the new-format scorer.
        criterion_scores = [
            {"id": "A1", "score": 0.8, "max_score": 5.0, "weighted_score": 4.0,
             "pass_rate": 0.8, "passed_rules": 4, "total_rules": 5},
            {"id": "A2", "score": 0.4, "max_score": 5.0, "weighted_score": 2.0,
             "pass_rate": 0.4, "passed_rules": 2, "total_rules": 5},
            {"id": "D1", "score": 1.0, "max_score": 5.0, "weighted_score": 5.0,
             "pass_rate": 1.0, "passed_rules": 5, "total_rules": 5},
        ]
    else:
        criterion_scores = [
            {"id": "A1", "score": 4.0, "max_score": 5.0},
            {"id": "A2", "score": 3.0, "max_score": 5.0},
            {"id": "D1", "score": 1.0, "max_score": 5.0},
        ]
    return {
        "trajectory": "/x/trajectory.json",
        "scores": {
            "A": {"name": "任务理解", "score": a, "max_score": 15.0, "level": "adequate"},
            "B": {"name": "根因定位", "score": 10.0, "max_score": 15.0, "level": "adequate"},
            "C": {"name": "代码实现", "score": 20.0, "max_score": 25.0, "level": "excellent"},
            "D": {"name": "测试与验证", "score": d, "max_score": 25.0, "level": "missing"},
            "E": {"name": "错误恢复", "score": 8.0, "max_score": 10.0, "level": "excellent"},
            "F": {"name": "收尾工作", "score": 8.0, "max_score": 10.0, "level": "adequate"},
        },
        "criterion_scores": criterion_scores,
        "raw_total_score": total,
        "total_score": total,
        "model": "judge-model",
    }


def _write_sample(root: Path, sample: str, merged: dict, dim_errors: dict[str, str]):
    out = root / sample / "agent" / "scores-x"
    out.mkdir(parents=True)
    (out / "trajectory.score.json").write_text(json.dumps(merged), encoding="utf-8")
    for dim in "ABCDEF":
        if dim in dim_errors:
            payload = {"criterion_id": dim, "applicable": False, "subcriteria": None,
                       "error": dim_errors[dim]}
        else:
            payload = {"criterion_id": dim, "applicable": True, "subcriteria": []}
        (out / f"trajectory.score.{dim}.json").write_text(json.dumps(payload), encoding="utf-8")


class LoadRubricTest(unittest.TestCase):
    def test_extracts_names_and_max_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "base.yaml"
            path.write_text(RUBRIC_YAML, encoding="utf-8")
            rubric = summarize_scores.load_rubric(path)
        self.assertEqual(rubric["A"]["name"], "任务理解")
        self.assertEqual(rubric["A"]["max_score"], 15.0)
        self.assertEqual(rubric["A"]["subcriteria"]["A1"]["name"], "理解问题并定义成功标准")
        self.assertEqual(rubric["A"]["subcriteria"]["A1"]["max_score"], 5.0)
        # example block scalars must not overwrite the subcriterion max score
        self.assertEqual(rubric["D"]["subcriteria"]["D1"]["max_score"], 5.0)

    def test_missing_file_returns_empty(self):
        self.assertEqual(summarize_scores.load_rubric(Path("/nonexistent.yaml")), {})

    def test_parses_score_rules_rubric(self):
        # 新格式 rubric：score_rules 列表不应干扰 name/max_score 的缩进解析
        rules_rubric = summarize_scores.SCRIPT_DIR / "trajectory_scoring_skill" / "rubric" / "base_rules.yaml"
        rubric = summarize_scores.load_rubric(rules_rubric)
        self.assertEqual(tuple(rubric), tuple("ABCDEF"))
        self.assertEqual(rubric["A"]["name"], "任务理解与验收标准")
        self.assertEqual(rubric["A"]["max_score"], 15.0)
        self.assertEqual(rubric["C"]["subcriteria"]["C4"]["name"], "最小性与代码质量")
        self.assertEqual(rubric["C"]["subcriteria"]["C4"]["max_score"], 3.0)
        # 权重之和仍等于维度满分（summary 的 max_score 元数据保持百分制口径）
        self.assertEqual(
            sum(s["max_score"] for s in rubric["F"]["subcriteria"].values()), 10.0)


class SummarizeTest(unittest.TestCase):
    def _tree(self, root: Path):
        _write_sample(root, "sample_ok", _merged_score(a=9.0, d=5.0, total=60.0), {})
        _write_sample(
            root, "sample_err", _merged_score(a=3.0, d=0.0, total=30.0),
            {"D": "RuntimeError: network error"},
        )
        # a sample directory without any score file
        (root / "sample_missing" / "agent").mkdir(parents=True)
        rubric = root / "base.yaml"
        rubric.write_text(RUBRIC_YAML, encoding="utf-8")
        return rubric

    def test_per_result_scores_and_means(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rubric = self._tree(root)
            summary = summarize_scores.summarize(root, rubric_path=rubric)

            self.assertEqual(summary["count"], 2)
            self.assertEqual(summary["successful_count"], 1)
            self.assertEqual(summary["error_count"], 1)
            self.assertEqual(summary["all_results_mean"], 45.0)
            self.assertEqual(summary["successful_results_mean"], 60.0)
            self.assertEqual(summary["scores_dir"], "scores-x")
            self.assertEqual(summary["samples_without_scores"], ["sample_missing"])

            # per-trajectory criterion/subcriterion detail is present
            ok = next(r for r in summary["results"] if r["sample"] == "sample_ok")
            err = next(r for r in summary["results"] if r["sample"] == "sample_err")
            self.assertEqual(ok["criteria"]["A"]["score"], 9.0)
            self.assertEqual(ok["subcriteria"]["A1"]["score"], 4.0)
            self.assertEqual(ok["subcriteria"]["A1"]["name"], "理解问题并定义成功标准")
            self.assertEqual(err["criteria"]["D"]["status"], "error")
            self.assertIsNone(err["criteria"]["D"]["score"])
            self.assertEqual(err["subcriteria"]["D1"]["status"], "error")
            self.assertEqual(err["errors"], ["D: RuntimeError: network error"])

            # criterion A mean covers both trajectories
            a = summary["criterion_means"]["A"]
            self.assertEqual(a["mean_score"], 6.0)
            self.assertEqual(a["sample_count"], 2)
            self.assertEqual(a["max_score"], 15.0)
            self.assertEqual(a["mean_ratio"], 0.4)
            self.assertEqual(a["error_count"], 0)

            # criterion D mean excludes the errored trajectory
            d = summary["criterion_means"]["D"]
            self.assertEqual(d["mean_score"], 5.0)
            self.assertEqual(d["sample_count"], 1)
            self.assertEqual(d["error_count"], 1)

            # subcriterion D1 mean also excludes the errored trajectory
            d1 = summary["subcriterion_means"]["D"]["D1"]
            self.assertEqual(d1["mean_score"], 1.0)
            self.assertEqual(d1["sample_count"], 1)
            self.assertEqual(d1["error_count"], 1)

            # subcriterion A2 name comes from the rubric
            a2 = summary["subcriterion_means"]["A"]["A2"]
            self.assertEqual(a2["name"], "保留必需的语义与约束")

    def test_missing_rubric_falls_back_to_score_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_sample(root, "s1", _merged_score(a=9.0, d=5.0, total=60.0), {})
            summary = summarize_scores.summarize(root, rubric_path=Path("/nonexistent.yaml"))
            a = summary["criterion_means"]["A"]
            self.assertEqual(a["name"], "任务理解")
            self.assertEqual(a["max_score"], 15.0)
            a1 = summary["subcriterion_means"]["A"]["A1"]
            self.assertIsNone(a1["name"])
            self.assertEqual(a1["max_score"], 5.0)

    def test_score_rules_subcriteria_summarize_against_pass_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_sample(root, "s1", _merged_score(a=7.0, d=5.0, total=50.0,
                                                    rules_based=True), {})
            rubric = root / "base.yaml"
            rubric.write_text(RUBRIC_YAML, encoding="utf-8")
            summary = summarize_scores.summarize(root, rubric_path=rubric)

            # 逐结果条目透传通过率与规则明细
            result = summary["results"][0]
            a1 = result["subcriteria"]["A1"]
            self.assertEqual(a1["scoring_format"], "score_rules")
            self.assertEqual(a1["score"], 0.8)
            self.assertEqual(a1["pass_rate"], 0.8)
            self.assertEqual(a1["passed_rules"], 4)
            self.assertEqual(a1["total_rules"], 5)
            self.assertEqual(a1["weighted_score"], 4.0)
            # 维度分仍是数值型百分制贡献
            self.assertEqual(result["criteria"]["A"]["score"], 7.0)

            # 子维度均值按通过率对 1.0 计算，权重仅作为元数据保留
            means = summary["subcriterion_means"]["A"]
            self.assertEqual(means["A1"]["scoring_format"], "score_rules")
            self.assertEqual(means["A1"]["mean_score"], 0.8)
            self.assertEqual(means["A1"]["max_score"], 1.0)
            self.assertEqual(means["A1"]["mean_ratio"], 0.8)
            self.assertEqual(means["A1"]["subcriterion_weight"], 5.0)
            self.assertEqual(means["A2"]["mean_score"], 0.4)

            # 维度层统计保持旧口径
            self.assertEqual(summary["criterion_means"]["A"]["mean_score"], 7.0)
            self.assertEqual(summary["criterion_means"]["A"]["mean_ratio"],
                             round(7.0 / 15.0, 4))


class CliTest(unittest.TestCase):
    def test_writes_summary_next_to_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_sample(root, "s1", _merged_score(a=9.0, d=5.0, total=60.0), {})
            rc = summarize_scores.main([str(root), "--rubric", "/nonexistent.yaml"])
            self.assertEqual(rc, 0)
            out = root / "scores-x-summary.json"
            self.assertTrue(out.is_file())
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["count"], 1)

    def test_ambiguous_scores_dir_requires_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_sample(root, "s1", _merged_score(a=9.0, d=5.0, total=60.0), {})
            other = root / "s2" / "agent" / "scores-y"
            other.mkdir(parents=True)
            (other / "trajectory.score.json").write_text(
                json.dumps(_merged_score(a=1.0, d=1.0, total=10.0)), encoding="utf-8"
            )
            with self.assertRaises(SystemExit):
                summarize_scores.main([str(root)])
            rc = summarize_scores.main(
                [str(root), "--scores-dir", "scores-y", "--rubric", "/nonexistent.yaml"]
            )
            self.assertEqual(rc, 0)
            written = json.loads((root / "scores-y-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(written["count"], 1)
            self.assertEqual(written["all_results_mean"], 10.0)


if __name__ == "__main__":
    unittest.main()
