import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import iterate_score_rules as iter_mod
import score_trajectories


def _mini_rubric_yaml() -> str:
    criteria = []
    for dim, rule_names in {
        "A": ["坏规则甲。", "规则A乙。", "规则A丙。"],
        "B": ["规则B甲。", "规则B乙。", "规则B丙。"],
        "C": ["规则C甲。", "规则C乙。", "规则C丙。"],
        "D": ["规则D甲。", "规则D乙。", "规则D丙。"],
        "E": ["规则E甲。", "规则E乙。", "规则E丙。"],
        "F": ["规则F甲。", "规则F乙。", "规则F丙。"],
    }.items():
        criteria.append(f"""
    - id: {dim}
      name: 维度{dim}
      description: 评估维度{dim}。
      max_score: 15
      subcriteria:
        - id: {dim}1
          name: 维度{dim}子项
          description: 评估维度{dim}子项。
          score: 15
          score_rules:
""")
        for name in rule_names:
            criteria.append(f'            - "{name}"\n')
    return ("rubric:\n"
            "  name: Mini Rule-based Rubric\n"
            "  version: \"3.0\"\n"
            "  total_score: 90\n"
            "  scoring_mode: score_rules\n"
            "  criteria:\n" + "".join(criteria))


class InlineProcess:
    """multiprocessing.Process 的同步替身，复用现有测试模式。"""

    names = []

    def __init__(self, *, target, args, name):
        self.target, self.args, self.name = target, args, name

    def start(self):
        self.names.append(self.name)
        self.target(*self.args)

    def join(self):
        return None


def write_mini_rubric(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "mini_rules.yaml"
    path.write_text(_mini_rubric_yaml(), encoding="utf-8")
    return path


def make_dataset(root: Path, samples) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        (root / sample).mkdir(parents=True)
        (root / sample / "trajectory.json").write_text(
            json.dumps({"steps": []}), encoding="utf-8")
    return root


class ApplyEditsTest(unittest.TestCase):
    def setUp(self):
        self.doc = iter_mod.load_rubric_document(
            write_mini_rubric(Path(tempfile.mkdtemp())))

    def _find(self, doc, sub_id):
        return next(s for c in doc["rubric"]["criteria"]
                    for s in c["subcriteria"] if s["id"] == sub_id)

    def test_add_modify_delete_are_applied(self):
        edits = [
            {"subcriterion_id": "A1", "op": "add", "rule_text": "新添加的规则"},
            {"subcriterion_id": "B1", "op": "modify", "rule_index": 1,
             "rule_text": "改写后的规则"},
            {"subcriterion_id": "A1", "op": "delete", "rule_index": 0},
        ]
        candidate, applied, dropped = iter_mod.apply_edits(self.doc, edits)
        self.assertEqual([e["op"] for e in applied], ["add", "modify", "delete"])
        self.assertEqual(dropped, [])
        self.assertEqual(self._find(candidate, "A1")["score_rules"],
                         ["规则A乙。", "规则A丙。", "新添加的规则"])
        self.assertEqual(self._find(candidate, "B1")["score_rules"],
                         ["规则B甲。", "改写后的规则", "规则B丙。"])

    def test_out_of_bounds_edits_are_dropped_not_fatal(self):
        edits = [
            {"subcriterion_id": "A1", "op": "add", "rule_text": "规则A乙。"},  # 重复
            {"subcriterion_id": "A1", "op": "add", "rule_text": "规则巳。"},
            {"subcriterion_id": "A1", "op": "add", "rule_text": "规则午。"},
            {"subcriterion_id": "A1", "op": "add", "rule_text": "规则未。"},  # 超 5 条
            {"subcriterion_id": "A1", "op": "delete", "rule_index": 9},        # 越界
            {"subcriterion_id": "A1", "op": "frobnicate"},                    # 非法 op
            {"subcriterion_id": "Z9", "op": "add", "rule_text": "x"},          # 未知子维度
            {"subcriterion_id": "A1", "op": "delete"},                        # 缺 rule_index
            {"subcriterion_id": "B1", "op": "delete", "rule_index": 0},
            {"subcriterion_id": "B1", "op": "delete", "rule_index": 0},
            {"subcriterion_id": "B1", "op": "delete", "rule_index": 0},        # 仅剩 1 条再删
        ]
        candidate, applied, dropped = iter_mod.apply_edits(self.doc, edits)
        self.assertEqual([e["op"] for e in applied],
                         ["add", "add", "delete", "delete"])
        self.assertEqual(len(self._find(candidate, "A1")["score_rules"]), 5)
        self.assertEqual(len(self._find(candidate, "B1")["score_rules"]), 1)
        self.assertEqual(
            [d["reason"] for d in dropped],
            ["rule already exists", "would exceed 5 rules", "rule_index out of range",
             "invalid op 'frobnicate'", "unknown subcriterion_id",
             "missing or non-integer rule_index", "would go below 1 rule"])
        # 编辑后的 rubric 仍能通过打分脚本的格式检测
        for criterion in candidate["rubric"]["criteria"]:
            self.assertEqual(
                score_trajectories._criterion_scoring_mode(criterion), "score_rules")

    def test_source_document_is_not_mutated(self):
        edits = [{"subcriterion_id": "A1", "op": "add", "rule_text": "新添加的规则"}]
        candidate, applied, dropped = iter_mod.apply_edits(self.doc, edits)
        self.assertEqual([e["op"] for e in applied], ["add"])
        self.assertEqual(len(self._find(candidate, "A1")["score_rules"]), 4)
        self.assertEqual(len(self._find(self.doc, "A1")["score_rules"]), 3)


class ValidateCriticResultTest(unittest.TestCase):
    def setUp(self):
        self.doc = iter_mod.load_rubric_document(
            write_mini_rubric(Path(tempfile.mkdtemp())))

    def test_accepts_well_formed_result(self):
        result = {"analysis": "规则过粗",
                  "edits": [{"subcriterion_id": "A1", "op": "add", "rule_text": "x"},
                            {"subcriterion_id": "B1", "op": "delete", "rule_index": 0},
                            {"subcriterion_id": "A1", "op": "modify",
                             "rule_index": 0, "rule_text": "y"}]}
        parsed, reason = iter_mod._validate_critic_result(result, self.doc)
        self.assertIsNone(reason)
        self.assertEqual(len(parsed["edits"]), 3)

    def test_rejects_malformed_results(self):
        for bad in [
            {"analysis": "", "edits": []},
            {"analysis": "  ", "edits": []},
            {"analysis": "ok", "edits": "none"},
            {"analysis": "ok"},
            {"analysis": "ok", "edits": [{"subcriterion_id": "A1", "op": "add"}]},
            {"analysis": "ok", "edits": [{"subcriterion_id": "A9", "op": "add",
                                          "rule_text": "x"}]},
            {"analysis": "ok", "edits": [{"subcriterion_id": "A1", "op": "delete"}]},
            {"analysis": "ok", "edits": [{"subcriterion_id": "A1", "op": "rename",
                                          "rule_text": "x"}]},
        ]:
            self.assertIsNotNone(
                iter_mod._validate_critic_result(bad, self.doc)[1], bad)


class DecideAdoptionTest(unittest.TestCase):
    def test_requires_improvement_strictly_above_threshold(self):
        baseline = {"evidence_gaps": [],
                    "criterion_scores": [{"id": "A1", "score": 0.4}]}
        for candidate_score, want in [(0.9, True), (0.450001, True),
                                      (0.45, False), (0.1, False)]:
            candidate = {"evidence_gaps": [],
                         "criterion_scores": [{"id": "A1", "score": candidate_score}]}
            adopted, deltas, improved = iter_mod.decide_adoption(baseline, candidate)
            self.assertIs(adopted, want, candidate_score)
            if want:
                self.assertEqual(improved, ["A1"])
            self.assertAlmostEqual(deltas["A1"], candidate_score - 0.4)

    def test_skips_subcriteria_of_errored_dimensions(self):
        baseline = {"evidence_gaps": [],
                    "criterion_scores": [{"id": "A1", "score": 0.4},
                                         {"id": "B1", "score": 0.4}]}
        candidate = {"evidence_gaps": [{"location": "A", "observation": "boom"}],
                     "criterion_scores": [{"id": "A1", "score": 1.0},
                                          {"id": "B1", "score": 0.5}]}
        adopted, deltas, improved = iter_mod.decide_adoption(baseline, candidate)
        # A 维度有 worker 失败，A1 的 +0.6 不算数；B1 的 +0.1 仍然达到阈值被采纳
        self.assertTrue(adopted)
        self.assertEqual(improved, ["B1"])
        self.assertEqual(sorted(deltas), ["B1"])
        self.assertAlmostEqual(deltas["B1"], 0.1)


def _parse_judge_prompt(prompt: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """从渲染后的 rules 评分 prompt 中提取维度 ID 和各子维度的规则文本。"""
    dimension = next(d for d in "ABCDEF" if f"ID:\n{d}\n" in prompt)
    subs = []
    for block in re.split(r"^## ", prompt, flags=re.M):
        m = re.search(r"^ID:\n([A-F]\d+)$", block, re.M)
        if not m:
            continue
        # 规则编号行出现在 "ID:<sub>" 之后；子维度标题行（“1. 名称”）不参与
        rules = re.findall(r"^\d+\. (.+)$", block[m.end():], re.M)
        subs.append((m.group(1), rules))
    return dimension, subs


class IterateEndToEndTest(unittest.TestCase):
    """用 mock API 跑完整 rollout 循环：采纳、拒绝与采样复现。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.train = make_dataset(root / "train", ["t1", "t2"])
        self.validate = make_dataset(root / "validate", ["v1", "v2"])
        self.skill = root / "skill"
        self.rubric = write_mini_rubric(self.skill / "rubric")
        real_skill = Path(score_trajectories.DEFAULT_SKILL_DIR)
        for name in ("scoring_prompt.j2", "scoring_rules_prompt.j2",
                     "iterate_rules_prompt.j2"):
            # critic 模板按标志串识别，避免与 judge prompt 混淆
            text = (real_skill / name).read_text(encoding="utf-8")
            (self.skill / name).write_text(text, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, critic_edits, rollout_num="2", judge=None):
        rollout_work = Path(self.tmp.name) / f"work{rollout_num}-{len(critic_edits)}"
        args = iter_mod.parse_args([
            "--train_dataset", str(self.train),
            "--validate_dataset", str(self.validate),
            "--rollout_num", rollout_num,
            "--model", "judge", "--critic-model", "critic",
            "--api-key", "key", "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric),
            "--skill-dir", str(self.skill),
            "--seed", "7", "--output-dir", str(rollout_work),
        ])
        if judge is None:
            judge = default_judge
        critic_result = {"analysis": "根据打分结果调整规则", "edits": critic_edits}

        def completion(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            if "You are the maintainer of a rule-based rubric" in prompt:
                return json.dumps(critic_result)
            dimension, subs = _parse_judge_prompt(prompt)
            subcriteria = [{"id": sub_id,
                            "rules": [{"index": i, "passed": judge(rule),
                                       "evidence": [], "reason": "r"}
                                      for i, rule in enumerate(rules)]}
                           for sub_id, rules in subs]
            return json.dumps({"criterion_id": dimension, "applicable": True,
                               "subcriteria": subcriteria})

        InlineProcess.names = []
        with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
             patch("score_trajectories._http_json", side_effect=completion):
            report = iter_mod.iterate(args)
        return rollout_work, report

    def test_no_effective_edits_is_a_no_op_round(self):
        _, report = self._run([])
        self.assertEqual(len(report["iterations"]), 2)
        for record in report["iterations"]:
            self.assertTrue(record["no_op"])
            self.assertFalse(record["adopted"])
            self.assertIsNone(record["candidate_rubric"])
        # 没有候选 rubric，也就没有验证打分
        first_dir = Path(report["workdir"])
        self.assertFalse((first_dir / "validate").exists())
        self.assertEqual(
            report["rubric_change_summary"]["changed_subcriteria"], [])

    def test_candidate_that_lowers_pass_rate_is_rejected(self):
        # 给 A1 追加一条 judge 会判 False 的规则：A1 通过率 2/3 → 2/4，回退
        edits = [{"subcriterion_id": "A1", "op": "add", "rule_text": "坏规则新。"}]
        work, report = self._run(edits)
        first, second = report["iterations"]
        self.assertFalse(first["no_op"])
        self.assertFalse(first["adopted"])
        # 基线 2/3，候选 2/4 → delta 为负
        self.assertAlmostEqual(first["deltas"]["A1"], 0.5 - 2 / 3)
        self.assertFalse(second["adopted"])
        self.assertTrue((work / "rubrics" / "rubric.iter1-candidate.yaml").is_file())
        summary = report["rubric_change_summary"]
        self.assertEqual(summary["changed_subcriteria"], [])
        self.assertEqual(summary["initial_total_rule_count"],
                         summary["final_total_rule_count"])
        # 两轮各有 1 次训练打分 + 2 次验证打分，每次 6 个维度 worker
        self.assertEqual(len(InlineProcess.names), 36)

    def test_candidate_that_improves_a_subcriterion_is_adopted(self):
        # 原规则首条是“坏规则甲。” judge 判 False（2/3）；critic 把它修改为
        # 判 True 的条款 “规则甲改。”，验证通过率升到 3/3 → +1/3 > 0.05 采纳
        edits = [{"subcriterion_id": "A1", "op": "modify", "rule_index": 0,
                  "rule_text": "规则甲改。"}]
        work, report = self._run(edits)
        record = report["iterations"][0]
        self.assertTrue(record["adopted"], record)
        self.assertEqual(record["improved_subcriteria"], ["A1"])
        self.assertAlmostEqual(record["deltas"]["A1"], 1 / 3)
        self.assertEqual(report["rubric_change_summary"]["changed_subcriteria"], ["A1"])
        final = iter_mod.load_rubric_document(work / "final_rubric.yaml")
        a1 = next(s for c in final["rubric"]["criteria"]
                  for s in c["subcriteria"] if s["id"] == "A1")
        self.assertEqual(a1["score_rules"],
                         ["规则甲改。", "规则A乙。", "规则A丙。"])
        self.assertTrue((work / "rubrics" / "rubric.iter1-adopted.yaml").is_file())
        # 第二轮 critic 重提同样的 modify → 候选与已采纳规则一致，no-op
        second = report["iterations"][1]
        self.assertTrue(second["no_op"])
        self.assertFalse(second["adopted"])
        # 第一轮 6 训练 + 12 验证，第二轮 no-op 只剩 6 次训练打分
        self.assertEqual(len(InlineProcess.names), 24)

    def test_seed_reproduces_sampling(self):
        _, report1 = self._run([])
        _, report2 = self._run([])
        self.assertEqual([r["train_sample"] for r in report1["iterations"]],
                         [r["train_sample"] for r in report2["iterations"]])
        self.assertEqual([r["validate_sample"] for r in report1["iterations"]],
                         [r["validate_sample"] for r in report2["iterations"]])

    def test_train_dataset_smaller_than_rollout_num_exits(self):
        args = iter_mod.parse_args([
            "--train_dataset", str(self.train),
            "--validate_dataset", str(self.validate),
            "--rollout_num", "3",
            "--model", "judge", "--api-key", "key",
            "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric), "--skill-dir", str(self.skill),
            "--output-dir", str(Path(self.tmp.name) / "insufficient"),
        ])
        with self.assertRaises(SystemExit):
            iter_mod.iterate(args)


def default_judge(rule_text: str) -> bool:
    """默认 judge：以“坏”开头的规则判不通过，其余通过。"""
    return not rule_text.startswith("坏")


if __name__ == "__main__":
    unittest.main()