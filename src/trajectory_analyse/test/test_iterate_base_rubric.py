import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import iterate_base_rubric as iter_base
import iterate_score_rules as iter_mod
import score_trajectories as scorer


def _mini_iterated_rubric_yaml() -> str:
    """与 iterated 模式兼容的迷你 rubric：A1 首条规则以“坏”开头会被 mock judge 判否。"""
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
      subcriteria:
        - id: {dim}1
          name: 维度{dim}子项
          description: 评估维度{dim}子项。
          score_rules:
""")
        for name in rule_names:
            criteria.append(f'            - "{name}"\n')
    return ("rubric:\n"
            "  name: Mini Iterated Rubric\n"
            "  version: \"0.1\"\n"
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
    path = directory / "mini_iterated.yaml"
    path.write_text(_mini_iterated_rubric_yaml(), encoding="utf-8")
    return path


def make_dataset(root: Path, samples, *, harbor_layout: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    sample_root = root / "trajs_harbor" if harbor_layout else root
    sample_root.mkdir(parents=True, exist_ok=True)
    for sample in samples:
        (sample_root / sample).mkdir(parents=True)
        (sample_root / sample / "trajectory.json").write_text(
            json.dumps({"steps": []}), encoding="utf-8")
    return root


def default_judge(rule_text: str) -> bool:
    """默认 judge：以“坏”开头的规则判不通过，其余通过。"""
    return not rule_text.startswith("坏")


def _parse_judge_prompt(prompt: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """从渲染后的 iterated 评分 prompt 中提取维度 ID 和各子维度的规则文本。"""
    dimension = next(d for d in "ABCDEF" if f"ID:\n{d}\n" in prompt)
    subs = []
    for block in re.split(r"^## ", prompt, flags=re.M):
        m = re.search(r"^ID:\n([A-F]\d+)$", block, re.M)
        if not m:
            continue
        rules = re.findall(r"^\d+\. (.+)$", block[m.end():], re.M)
        subs.append((m.group(1), rules))
    return dimension, subs


class ValidateProtocolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doc = iter_mod.load_rubric_document(
            write_mini_rubric(Path(self.tmp.name)))
        # C 维度扩到两个子维度，用于验证删除约束
        self.doc_two = copy.deepcopy(self.doc)
        criterion = next(c for c in self.doc_two["rubric"]["criteria"]
                         if c["id"] == "C")
        criterion["subcriteria"].append({
            "id": "C2", "name": "维度C备选", "description": "备选指标。",
            "score_rules": ["规则C备。"]})
        self.addCleanup(self.tmp.cleanup)

    def _add_sub(self, **overrides):
        edit = {"op": "add_subcriterion", "criterion_id": "C", "id": "C2",
                "name": "新指标", "description": "指标说明",
                "score_rules": ["规则一。", "规则二。"]}
        edit.update(overrides)
        return {"analysis": "需要新指标", "edits": [edit]}

    def test_aggregate_result_round_trip(self):
        good = {"needs_modification": True, "analysis": "规则反复失真",
                "edits": [{"subcriterion_id": "A1", "op": "add", "rule_text": "x"}]}
        parsed, reason = iter_base._validate_aggregate_result(good, self.doc)
        self.assertIsNone(reason)
        self.assertEqual(parsed["needs_modification"], True)
        self.assertEqual(len(parsed["edits"]), 1)
        self.assertIsNone(
            iter_base._validate_aggregate_result(
                {"needs_modification": False, "analysis": "无需修改", "edits": []},
                self.doc)[1])

    def test_aggregate_rejects_malformed(self):
        for bad in [
            {"needs_modification": "yes", "analysis": "a", "edits": []},
            {"analysis": "a", "edits": []},
            {"needs_modification": True, "analysis": " ", "edits": []},
            {"needs_modification": True, "edits": []},
            {"needs_modification": True, "analysis": "a",
             "edits": [{"subcriterion_id": "Z9", "op": "add", "rule_text": "x"}]},
            # 判断不需要修改时不得夹带 edits
            {"needs_modification": False, "analysis": "a",
             "edits": [{"subcriterion_id": "A1", "op": "add", "rule_text": "x"}]},
        ]:
            self.assertIsNotNone(
                iter_base._validate_aggregate_result(bad, self.doc)[1], bad)

    def test_subcriterion_edits_are_accepted(self):
        parsed, reason = iter_base._validate_critic_result(
            self._add_sub(), self.doc)
        self.assertIsNone(reason, reason)
        self.assertEqual(parsed["edits"][0]["id"], "C2")
        # C 已有两个子维度时删除 C1 合法
        delete = {"analysis": "特例指标反复零分",
                  "edits": [{"op": "delete_subcriterion", "subcriterion_id": "C1"}]}
        self.assertIsNone(iter_base._validate_critic_result(delete, self.doc_two)[1])

    def test_subcriterion_edits_are_rejected_when_malformed(self):
        cases = [
            self._add_sub(criterion_id="Z"),                    # 未知维度
            self._add_sub(id="A1"),                            # id 重复
            self._add_sub(id=" "),                             # id 为空
            self._add_sub(name="  "),                          # 名称为空
            self._add_sub(score_rules=[]),                      # 规则数下限
            self._add_sub(score_rules=["a"] * 6),               # 规则数上限
            self._add_sub(score_rules=[""]),                     # 规则文本为空
            # 每个 mini 维度只有一个子维度，删除即清空 → 拒绝
            {"analysis": "清空维度", "edits": [
                {"op": "delete_subcriterion", "subcriterion_id": "C1"}]},
            {"analysis": "未知目标", "edits": [
                {"op": "delete_subcriterion", "subcriterion_id": "Z9"}]},
        ]
        for case in cases:
            self.assertIsNotNone(
                iter_base._validate_critic_result(case, self.doc)[1], case)

    def test_usable_score_guard(self):
        # 至少一个维度成功即有 reasoning 可 review；全部 error 则跳过
        self.assertTrue(iter_base._usable_score({"evidence_gaps": []}))
        self.assertTrue(iter_base._usable_score(
            {"evidence_gaps": [{"location": "A", "observation": "boom"}]}))
        self.assertFalse(iter_base._usable_score({
            "evidence_gaps": [{"location": d, "observation": "down"} for d in "ABCDEF"]}))

    def test_quality_and_verify_results(self):
        self.assertIsNone(iter_base._validate_quality_result(
            {"quality_pass": True, "analysis": "合格", "issues": []})[1])
        for bad in [
            {"quality_pass": "yes", "analysis": "a"},
            {"quality_pass": True, "analysis": ""},
            {"quality_pass": True, "analysis": "a", "issues": "none"},
            {"quality_pass": True, "analysis": "a", "issues": [1]},
        ]:
            self.assertIsNotNone(iter_base._validate_quality_result(bad)[1], bad)
        self.assertIsNone(iter_base._validate_verify_result(
            {"more_reasonable": True, "analysis": "新版本更合理",
             "criterion_notes": [{"criterion_id": "A", "new_better": True,
                                  "comment": "ok"}]})[1])
        for bad in [
            {"more_reasonable": "yes", "analysis": "a"},
            {"more_reasonable": True, "analysis": "  "},
            {"more_reasonable": True, "analysis": "a", "criterion_notes": ["A"]},
        ]:
            self.assertIsNotNone(iter_base._validate_verify_result(bad)[1], bad)


class ApplyRubricEditsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.doc = iter_mod.load_rubric_document(
            write_mini_rubric(Path(self.tmp.name)))
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def _sub(doc, sub_id):
        return next(s for c in doc["rubric"]["criteria"]
                    for s in c["subcriteria"] if s["id"] == sub_id)

    @staticmethod
    def _criterion(doc, criterion_id):
        return next(c for c in doc["rubric"]["criteria"] if c["id"] == criterion_id)

    def test_add_and_delete_subcriterion(self):
        add = {"op": "add_subcriterion", "criterion_id": "C", "id": "C2",
               "name": "新指标", "description": "指标说明",
               "score_rules": ["规则一。", "规则二。"]}
        candidate, applied, dropped = iter_base.apply_rubric_edits(self.doc, [add])
        self.assertEqual(applied, [add])
        self.assertEqual(dropped, [])
        self.assertEqual([s["id"] for s in self._criterion(candidate, "C")["subcriteria"]],
                         ["C1", "C2"])
        self.assertEqual(self._sub(candidate, "C2")["score_rules"], ["规则一。", "规则二。"])
        # 源文档未被改动
        self.assertEqual([s["id"] for s in self._criterion(self.doc, "C")["subcriteria"]], ["C1"])
        # C 有两个子维度后可以删除旧指标
        delete = {"op": "delete_subcriterion", "subcriterion_id": "C1"}
        candidate2, applied2, dropped2 = iter_base.apply_rubric_edits(candidate, [delete])
        self.assertEqual(applied2, [delete])
        self.assertEqual([s["id"] for s in self._criterion(candidate2, "C")["subcriteria"]],
                         ["C2"])

    def test_rules_edit_can_target_subcriterion_added_in_same_batch(self):
        add = {"op": "add_subcriterion", "criterion_id": "D", "id": "D2",
               "name": "新指标", "description": "指标说明",
               "score_rules": ["规则一。", "规则二。"]}
        modify = {"subcriterion_id": "D2", "op": "modify", "rule_index": 0,
                  "rule_text": "改写规则一。"}
        candidate, applied, dropped = iter_base.apply_rubric_edits(self.doc, [add, modify])
        self.assertEqual([e["op"] for e in applied], ["add_subcriterion", "modify"])
        self.assertEqual(dropped, [])
        self.assertEqual(self._sub(candidate, "D2")["score_rules"],
                         ["改写规则一。", "规则二。"])

    def test_invalid_subcriterion_edits_are_dropped_not_fatal(self):
        # 把 B 扩到 5 个子维度，验证上限约束
        criterion = self._criterion(self.doc, "B")
        for i in range(2, 6):
            criterion["subcriteria"].append({
                "id": f"B{i}", "name": f"指标B{i}", "description": "说明",
                "score_rules": ["规则。"]})
        edits = [
            {"op": "add_subcriterion", "criterion_id": "B", "id": "B6",
             "name": "满员维度", "description": "说明", "score_rules": ["规则。"]},
            {"op": "add_subcriterion", "criterion_id": "C", "id": "A1",
             "name": "重复id", "description": "说明", "score_rules": ["规则。"]},
            {"op": "add_subcriterion", "criterion_id": "Z", "id": "Z1",
             "name": "未知维度", "description": "说明", "score_rules": ["规则。"]},
            {"op": "add_subcriterion", "criterion_id": "C"},          # 缺 id
            {"op": "delete_subcriterion", "subcriterion_id": "C1"},  # 仅剩 1 个
            {"op": "rename_subcriterion", "subcriterion_id": "C1"},
        ]
        candidate, applied, dropped = iter_base.apply_rubric_edits(self.doc, edits)
        self.assertEqual(applied, [])
        self.assertEqual([d["reason"] for d in dropped], [
            "would exceed 5 subcriteria", "subcriterion id already exists",
            "unknown criterion_id", "missing subcriterion id",
            "would go below 1 subcriterion", "invalid op 'rename_subcriterion'"])
        iter_base._precheck_candidate(self.doc, candidate)
        # 编辑后 rubric 仍全部处于 iterated 模式
        for c in candidate["rubric"]["criteria"]:
            self.assertEqual(scorer._criterion_scoring_mode(c), "iterated")

    def test_change_summary_tracks_add_delete_modify(self):
        add = {"op": "add_subcriterion", "criterion_id": "C", "id": "C2",
               "name": "新指标", "description": "指标说明",
               "score_rules": ["规则一。"]}
        candidate, _, _ = iter_base.apply_rubric_edits(self.doc, [add])
        delete = {"op": "delete_subcriterion", "subcriterion_id": "B1"}
        # 先给 B 补一个子维度再删，避免触碰下限约束
        candidate, _, _ = iter_base.apply_rubric_edits(candidate, [
            {**add, "criterion_id": "B", "id": "B2"}, delete])
        modify = {"subcriterion_id": "A1", "op": "modify", "rule_index": 0,
                  "rule_text": "规则甲改。"}
        candidate, _, _ = iter_base.apply_rubric_edits(candidate, [modify])
        summary = iter_base._change_summary(self.doc, candidate)
        self.assertEqual(summary["changed_subcriteria"], ["A1"])
        self.assertEqual(summary["added_subcriteria"], ["B2", "C2"])
        self.assertEqual(summary["deleted_subcriteria"], ["B1"])
        self.assertEqual(summary["initial_subcriterion_count"], 6)
        self.assertEqual(summary["final_subcriterion_count"], 7)
        self.assertEqual(summary["final_total_rule_count"] - summary["initial_total_rule_count"], -1)


class RequestLlmJsonRepairTest(unittest.TestCase):
    """request_llm_json 的两条重试通道：网络退避 vs 非法 JSON 纠正重试。"""

    def _call(self, responses, retries=1, invalid_retries=None):
        calls = []

        def respond(_url, _headers, body, _timeout):
            calls.append(body["messages"])
            reply, *rest = responses
            responses[:] = rest
            if isinstance(reply, Exception):
                raise reply
            return reply

        with patch("score_trajectories._http_json", side_effect=respond):
            parsed = iter_base.request_llm_json(
                "prompt", iter_base._validate_verify_result,
                base_url="https://theta/v1", api_key="k", model="m",
                retries=retries, invalid_retries=invalid_retries, timeout=1)
        return parsed, calls

    def test_invalid_json_triggers_corrective_retry(self):
        # 第 1 次返回非法 more_reasonable，第 2 次修复为合法
        parsed, calls = self._call([
            json.dumps({"more_reasonable": "yes", "analysis": "a"}),
            json.dumps({"more_reasonable": True, "analysis": "a"}),
        ], invalid_retries=2)
        self.assertTrue(parsed["more_reasonable"])
        self.assertEqual(len(calls), 2)
        # 第二次请求带上了 assistant 原始返回 + 校验错误修复指令
        self.assertEqual([m["role"] for m in calls[1]], ["user", "assistant", "user"])
        self.assertIn("is invalid: more_reasonable must be boolean",
                      calls[1][2]["content"])

    def test_transport_errors_do_not_grow_the_conversation(self):
        parsed, calls = self._call([
            ConnectionError("Remote end closed connection"),
            json.dumps({"more_reasonable": True, "analysis": "a"}),
        ], retries=2)
        self.assertTrue(parsed["more_reasonable"])
        # 传输失败重发的是同一个原始 prompt，不追加修复消息
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len(calls[0]), 1)

    def test_invalid_budget_exhaustion_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call([json.dumps({"more_reasonable": "yes", "analysis": "a"}),
                        json.dumps({"more_reasonable": "still bad", "analysis": "a"}),
                        json.dumps({"more_reasonable": True, "analysis": "a"})],
                       retries=3, invalid_retries=2)
        self.assertIn("invalid llm response after 2 attempts", str(ctx.exception))


class IterateBaseEndToEndTest(unittest.TestCase):
    """用 mock API 跑逐轨迹迭代循环：no-op、采纳、质量门禁、验证拒绝与采样复现。"""

    # 各阶段 mock 应答的常量
    NO_MODIFY = {"needs_modification": False, "analysis": "规则整体健康", "edits": []}
    QUALITY_OK = {"quality_pass": True, "analysis": "结构完好", "issues": []}
    VERIFY_BETTER = {"more_reasonable": True, "analysis": "新规则推理更贴轨迹",
                     "criterion_notes": []}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.train = make_dataset(root / "train", ["t1", "t2", "t3"])
        self.verified = make_dataset(root / "verified", ["v1", "v2"])
        self.skill = root / "skill"
        self.rubric = write_mini_rubric(self.skill / "iterated_rubric")
        real_skill = Path(scorer.DEFAULT_SKILL_DIR)
        for name in ("iterated_prompt.j2", *iter_base.TEMPLATES.values()):
            (self.skill / name).write_text(
                (real_skill / name).read_text(encoding="utf-8"), encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)

    def _completion(self, decisions, quality=None, verify=None, judge=None,
                    judge_log=None):
        """按提示词特征串把请求路由到决策/质量/验证/judge 四种应答。

        ``decisions`` 是 list 时按决策调用次数依次出队（每条轨迹触发一次）。
        """
        if judge is None:
            judge = default_judge
        queue = list(decisions) if isinstance(decisions, list) else [decisions]

        def respond(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            # 单轨迹打分后的立即决策模板头部
            if "The rubric improves incrementally" in prompt:
                return json.dumps(queue.pop(0))
            if "soft-quality gate" in prompt:
                return json.dumps(quality)
            if "scoring runs of the SAME trajectory" in prompt:
                return json.dumps(verify)
            assert "between 0 and 1" in prompt, "unexpected prompt"
            dimension, subs = _parse_judge_prompt(prompt)
            if judge_log is not None:
                judge_log.append((dimension, [(sub, list(rules)) for sub, rules in subs]))
            subcriteria = []
            for sub_id, rules in subs:
                verdicts = [{"index": i, "passed": judge(rule), "evidence": [],
                             "reason": "r"} for i, rule in enumerate(rules)]
                passed = sum(1 for v in verdicts if v["passed"])
                subcriteria.append({
                    "id": sub_id, "score": round(passed / len(rules), 2),
                    "reasoning": f"{passed}/{len(rules)}", "rules": verdicts})
            return json.dumps({"criterion_id": dimension, "applicable": True,
                               "subcriteria": subcriteria})

        return respond

    def _run(self, decisions, quality=None, verify=None, workname="work",
             train_num="2", iteration_num="1", judge=None, judge_log=None,
             extra_args=()):
        if quality is None:
            quality = self.QUALITY_OK
        if verify is None:
            verify = self.VERIFY_BETTER
        work = Path(self.tmp.name) / workname
        args = iter_base.parse_args([
            "--train_dataset", str(self.train),
            "--verified_dataset", str(self.verified),
            "--train_num", train_num,
            "--iteration_num", iteration_num,
            "--model", "judge", "--critic-model", "critic",
            "--api-key", "key", "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric), "--skill-dir", str(self.skill),
            "--seed", "11", "--output-dir", str(work), *extra_args,
        ])
        InlineProcess.names = []
        with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
             patch("score_trajectories._http_json",
                   side_effect=self._completion(decisions, quality, verify, judge,
                                                judge_log)):
            report = iter_base.iterate(args)
        return work, report

    def test_no_modification_leaves_rubric_untouched(self):
        work, report = self._run(self.NO_MODIFY)
        record = report["iterations"][0]
        self.assertEqual(record["train_usable_count"], 2)
        self.assertEqual(record["decisions_to_modify"], 0)
        for step in record["train_steps"]:
            self.assertFalse(step["needs_modification"])
            self.assertFalse(step["adopted"])
            self.assertIsNone(step["candidate_rubric"])
        # 2 条训练轨迹 × 6 维度 judge 调用；没有候选 rubric，也就没有对照打分
        self.assertEqual(len(InlineProcess.names), 12)
        self.assertFalse((work / "verified").exists())
        self.assertEqual(
            report["rubric_change_summary"]["changed_subcriteria"], [])

    def test_adopted_edit_is_used_by_following_trajectories(self):
        modify_a1 = {"needs_modification": True, "analysis": "A1 首条规则迫使 judge 武断",
                     "edits": [{"subcriterion_id": "A1", "op": "modify",
                                "rule_index": 0, "rule_text": "规则甲改。"}]}
        judge_log = []
        work, report = self._run([modify_a1, self.NO_MODIFY], judge_log=judge_log)
        first, second = report["iterations"][0]["train_steps"]
        self.assertTrue(first["adopted"])
        self.assertFalse(second["adopted"], second)
        self.assertTrue(Path(first["adopted_rubric"]).is_file())
        # 第二条轨迹确实是用采纳后的 rubric 打分：judge prompt 里 A1 首条规则已改写。
        # A 维共 4 次调用：轨迹1（旧 rubric）、对照 baseline（旧）、对照
        # candidate（新）、轨迹2（新）
        a1_rules = [rule_list for _, subs in judge_log
                    for sub_id, rule_list in subs if sub_id == "A1"]
        self.assertEqual(len(a1_rules), 4)
        old_rules = ["坏规则甲。", "规则A乙。", "规则A丙。"]
        new_rules = ["规则甲改。", "规则A乙。", "规则A丙。"]
        self.assertEqual(a1_rules[0], old_rules)
        self.assertEqual(a1_rules[1], old_rules)   # 迭代前对照打分仍用旧 rubric
        self.assertEqual(a1_rules[2], new_rules)
        self.assertEqual(a1_rules[3], new_rules)
        self.assertEqual(second["rubric_snapshot"], first["adopted_rubric"])
        final = iter_mod.load_rubric_document(work / "final_rubric.yaml")
        a1 = next(s for c in final["rubric"]["criteria"]
                  for s in c["subcriteria"] if s["id"] == "A1")
        self.assertEqual(a1["score_rules"], ["规则甲改。", "规则A乙。", "规则A丙。"])
        self.assertEqual(report["rubric_change_summary"]["changed_subcriteria"], ["A1"])
        self.assertEqual(report["iterations"][0]["adopted_count"], 1)
        # 训练 2×6 + 触发修改的对照打分 2×6 = 24 个 judge worker
        self.assertEqual(len(InlineProcess.names), 24)

    def test_add_subcriterion_candidate_is_adopted(self):
        # 决策提出为 C 增加一个新指标（新行为信号），质量门禁与验证都通过
        decision = {
            "needs_modification": True, "analysis": "缺少新行为指标",
            "edits": [{"op": "add_subcriterion", "criterion_id": "C", "id": "C2",
                       "name": "新指标", "description": "考察新行为",
                       "score_rules": ["规则一。", "规则二。"]}],
        }
        work, report = self._run([decision], train_num="1")
        step = report["iterations"][0]["train_steps"][0]
        self.assertTrue(step["adopted"])
        self.assertEqual(report["rubric_change_summary"]["added_subcriteria"], ["C2"])
        self.assertEqual(report["rubric_change_summary"]["changed_subcriteria"], [])
        final = iter_mod.load_rubric_document(work / "final_rubric.yaml")
        criterion = next(c for c in final["rubric"]["criteria"] if c["id"] == "C")
        self.assertEqual([s["id"] for s in criterion["subcriteria"]], ["C1", "C2"])
        # 候选 rubric 上的对照打分确实覆盖了新指标
        candidate_scores = json.loads(
            (Path(step["verified_candidate_scores_dir"]) / "trajectory.score.json")
            .read_text(encoding="utf-8"))
        self.assertIn("C2", [e["id"] for e in candidate_scores["criterion_scores"]])

    def test_quality_gate_failure_reverts_to_previous_rubric(self):
        decision = {"needs_modification": True, "analysis": "改一改",
                     "edits": [{"subcriterion_id": "A1", "op": "add",
                                "rule_text": "新增规则。"}]}
        quality = {"quality_pass": False, "analysis": "新规则不可判定",
                   "issues": ["新规则依赖外部知识"]}
        work, report = self._run([decision], quality=quality, train_num="1")
        step = report["iterations"][0]["train_steps"][0]
        self.assertFalse(step["adopted"])
        self.assertFalse(step["quality"]["quality_pass"])
        self.assertTrue(Path(step["candidate_rubric"]).is_file())
        # 质量门禁未过，不进入对照打分
        self.assertFalse((work / "verified").exists())
        self.assertEqual(report["rubric_change_summary"]["changed_subcriteria"], [])

    def test_verification_rejection_keeps_previous_rubric(self):
        decision = {"needs_modification": True, "analysis": "改一改",
                     "edits": [{"subcriterion_id": "A1", "op": "add",
                                "rule_text": "新增规则。"}]}
        verify = {"more_reasonable": False, "analysis": "推理同样武断",
                  "criterion_notes": []}
        work, report = self._run([decision], verify=verify, train_num="1")
        step = report["iterations"][0]["train_steps"][0]
        self.assertFalse(step["adopted"])
        self.assertFalse(step["verification"]["more_reasonable"])
        self.assertIn(step["verified_sample"], ("v1", "v2"))  # verified 轨迹随机抽取
        # 对照打分发生了，但 rubric 未变
        self.assertTrue((Path(step["verified_baseline_scores_dir"])
                         / "trajectory.score.json").is_file())
        self.assertTrue((Path(step["verified_candidate_scores_dir"])
                         / "trajectory.score.json").is_file())
        self.assertEqual(report["rubric_change_summary"]["changed_subcriteria"], [])

    def test_seed_reproduces_sampling(self):
        _, report1 = self._run(self.NO_MODIFY, workname="work-a")
        _, report2 = self._run(self.NO_MODIFY, workname="work-b")
        self.assertEqual([r["train_samples"] for r in report1["iterations"]],
                         [r["train_samples"] for r in report2["iterations"]])

    def test_iteration_loop_with_scripted_llm(self):
        """两条轨迹依次决策：第一条采纳修改，第二条基于新 rubric 判断不再修改。"""
        modify_a1 = {"needs_modification": True, "analysis": "修复 A1",
                     "edits": [{"subcriterion_id": "A1", "op": "modify",
                                "rule_index": 0, "rule_text": "规则甲改。"}]}
        scripted = {"decisions": [modify_a1, {"needs_modification": False,
                                               "analysis": "不再修改", "edits": []}],
                    "quality": {"quality_pass": True, "analysis": "合格", "issues": []},
                    "verify": {"more_reasonable": True, "analysis": "更好",
                               "criterion_notes": []}}
        calls = {"decision": 0}

        def fake_request_llm_json(prompt, validate, **_):
            # 复用真实的协议校验器，避免测试 mock 与实现脱节
            if "The rubric improves incrementally" in prompt:
                result = scripted["decisions"][min(calls["decision"],
                                                   len(scripted["decisions"]) - 1)]
                calls["decision"] += 1
            elif "soft-quality gate" in prompt:
                result = scripted["quality"]
            elif "scoring runs of the SAME trajectory" in prompt:
                result = scripted["verify"]
            else:
                raise AssertionError("unexpected prompt:\n" + prompt[:200])
            parsed, reason = validate(result)
            assert reason is None, reason
            return parsed

        work = Path(self.tmp.name) / "two-trajs"
        args = iter_base.parse_args([
            "--train_dataset", str(self.train),
            "--verified_dataset", str(self.verified),
            "--train_num", "2", "--iteration_num", "1",
            "--model", "judge", "--api-key", "key",
            "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric), "--skill-dir", str(self.skill),
            "--seed", "11", "--output-dir", str(work),
        ])
        with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
             patch("score_trajectories._http_json",
                   side_effect=self._completion(None, None, None)), \
             patch.object(iter_base, "request_llm_json", side_effect=fake_request_llm_json):
            report = iter_base.iterate(args)
        first, second = report["iterations"][0]["train_steps"]
        self.assertTrue(first["adopted"])
        self.assertFalse(second["needs_modification"])
        self.assertEqual(calls["decision"], 2)
        self.assertEqual(report["rubric_change_summary"]["changed_subcriteria"], ["A1"])
        # start 轮打分用初始 rubric，轨迹 2 用的是轨迹 1 采纳后的快照
        self.assertTrue(first["rubric_snapshot"].endswith("base.iter1.yaml"))
        self.assertEqual(second["rubric_snapshot"], first["adopted_rubric"])

    def test_job_root_dataset_resolves_trajs_harbor(self):
        # verified job 目录：轨迹在 <root>/trajs_harbor/<sample>/trajectory.json
        job = make_dataset(Path(self.tmp.name) / "job", ["j1", "j2", "j3", "j4"],
                            harbor_layout=True)
        aggregate = {"needs_modification": False, "analysis": "ok", "edits": []}
        args = iter_base.parse_args([
            "--train_dataset", str(job), "--verified_dataset", str(self.verified),
            "--train_num", "2", "--iteration_num", "1",
            "--model", "judge", "--api-key", "key",
            "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric), "--skill-dir", str(self.skill),
            "--seed", "5", "--output-dir", str(Path(self.tmp.name) / "jobwork"),
        ])
        InlineProcess.names = []
        with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
             patch("score_trajectories._http_json",
                   side_effect=self._completion(aggregate, None, None)):
            report = iter_base.iterate(args)
        record = report["iterations"][0]
        self.assertEqual(len(record["train_samples"]), 2)
        self.assertTrue(set(record["train_samples"]) <= {"j1", "j2", "j3", "j4"})
        # 采样确实来自 trajs_harbor 子目录
        self.assertTrue(
            (Path(record["train_scores_dir"]) / record["train_samples"][0] / "scores"
             / "trajectory.score.json").is_file())

    def test_resume_rescores_only_failed_dimensions(self):
        # 预置：seed 11 抽中的是 t2，其 6 个维度里 5 个成功、D 带 error；--resume 只应重打 D
        work = Path(self.tmp.name) / "resume-work"
        scores = work / "train/iter1/t2/scores"
        scores.mkdir(parents=True)

        def iter_result(dimension):
            rules = [{"index": i, "passed": True, "evidence": [], "reason": "r"}
                     for i in range(3)]
            return {"criterion_id": dimension, "applicable": True,
                    "subcriteria": [{"id": f"{dimension}1", "score": 1.0,
                                     "reasoning": "ok", "rules": rules}]}

        for dimension in "ABCEF":
            (scores / f"trajectory.score.{dimension}.json").write_text(
                json.dumps(iter_result(dimension)), encoding="utf-8")
        (scores / "trajectory.score.D.json").write_text(json.dumps({
            "criterion_id": "D", "applicable": False, "subcriteria": None,
            "error": "RuntimeError: network error: RemoteDisconnected"}), encoding="utf-8")

        aggregate = {"needs_modification": False, "analysis": "ok", "edits": []}
        args = iter_base.parse_args([
            "--train_dataset", str(self.train), "--verified_dataset", str(self.verified),
            "--train_num", "1", "--iteration_num", "1",
            "--model", "judge", "--api-key", "key",
            "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric), "--skill-dir", str(self.skill),
            "--seed", "11", "--output-dir", str(work), "--resume",
        ])
        InlineProcess.names = []
        with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
             patch("score_trajectories._http_json",
                   side_effect=self._completion(aggregate, None, None)):
            report = iter_base.iterate(args)
        # 只有 D 维度被重新打分，其余维度文件未被触碰
        self.assertEqual(InlineProcess.names, ["trajectory-score-D"])
        merged = json.loads((scores / "trajectory.score.json").read_text(encoding="utf-8"))
        self.assertEqual(merged["evidence_gaps"], [])
        record = report["iterations"][0]
        self.assertEqual(record["train_usable_count"], 1)
        self.assertEqual(record["train_skipped"], [])
        self.assertTrue(report["resume"])

    def test_score_attempts_converge_flaky_dimensions(self):
        """模拟 judge 网关抖动：D 维度耗尽 worker 重试失败，补打轮只重打 D 并收敛。"""
        aggregate = {"needs_modification": False, "analysis": "ok", "edits": []}
        flaky = {"d_requests": 0}

        def flaky_completion(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            if any(marker in prompt for marker in (
                    "The rubric improves incrementally",
                    "soft-quality gate", "scoring runs of the SAME trajectory")):
                return json.dumps(aggregate)
            assert "between 0 and 1" in prompt, prompt[:200]
            dimension, subs = _parse_judge_prompt(prompt)
            if dimension == "D":
                flaky["d_requests"] += 1
                if flaky["d_requests"] <= 2:  # 耗尽 --retries 2 的 worker 内重试
                    raise ConnectionError("Remote end closed connection")
            subcriteria = []
            for sub_id, rules in subs:
                verdicts = [{"index": i, "passed": True, "evidence": [],
                             "reason": "r"} for i, _ in enumerate(rules)]
                subcriteria.append({"id": sub_id, "score": 1.0, "reasoning": "ok",
                                    "rules": verdicts})
            return json.dumps({"criterion_id": dimension, "applicable": True,
                               "subcriteria": subcriteria})

        work = Path(self.tmp.name) / "flaky-work"
        args = iter_base.parse_args([
            "--train_dataset", str(self.train), "--verified_dataset", str(self.verified),
            "--train_num", "1", "--iteration_num", "1",
            "--model", "judge", "--api-key", "key",
            "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric), "--skill-dir", str(self.skill),
            "--seed", "11", "--output-dir", str(work), "--retries", "2",
            "--score-attempts", "3", "--score-attempt-wait", "0",
        ])
        InlineProcess.names = []
        with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
             patch("score_trajectories._http_json", side_effect=flaky_completion):
            report = iter_base.iterate(args)
        # 第一轮 6 个维度，D 在 worker 内重试耗尽后带 error；补打轮只重发了 D
        self.assertEqual(InlineProcess.names,
                         [f"trajectory-score-{d}" for d in "ABCDEF"]
                         + ["trajectory-score-D"])
        record = report["iterations"][0]
        self.assertEqual(record["train_usable_count"], 1)
        self.assertEqual(record["train_skipped"], [])
        merged = json.loads((work / "train/iter1/t2/scores/trajectory.score.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(merged["evidence_gaps"], [])

    def test_consecutive_decision_failures_abort_the_round(self):
        """DNS 断连等网络级故障：连续 5 个决策失败中止整轮，不烧完 30 条。"""

        def dns_down(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            if "The rubric improves incrementally" in prompt:
                raise OSError("nodename nor servname provided, or not known")
            assert "between 0 and 1" in prompt, "unexpected prompt"
            dimension, subs = _parse_judge_prompt(prompt)
            subcriteria = [{"id": sub_id, "score": 1.0, "reasoning": "ok",
                            "rules": [{"index": i, "passed": True, "evidence": [],
                                       "reason": "r"}
                                      for i, _ in enumerate(rules)]}
                           for sub_id, rules in subs]
            return json.dumps({"criterion_id": dimension, "applicable": True,
                               "subcriteria": subcriteria})

        train = make_dataset(Path(self.tmp.name) / "train-big",
                             [f"b{i}" for i in range(8)])
        work = Path(self.tmp.name) / "dns-down"
        args = iter_base.parse_args([
            "--train_dataset", str(train), "--verified_dataset", str(self.verified),
            "--train_num", "6", "--iteration_num", "1",
            "--model", "judge", "--api-key", "key",
            "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric), "--skill-dir", str(self.skill),
            "--seed", "11", "--output-dir", str(work), "--retries", "2",
        ])
        InlineProcess.names = []
        with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
             patch("score_trajectories._http_json", side_effect=dns_down):
            report = iter_base.iterate(args)
        record = report["iterations"][0]
        self.assertIn("consecutive decision failures", record["error"])
        # 中止而非烧完全部：只推进到第 5 次决策失败
        steps = record.get("train_steps", [])
        self.assertEqual(len(steps), 5)
        self.assertTrue(all(s.get("error") for s in steps), steps)

    def test_train_dataset_smaller_than_train_num_exits(self):
        args = iter_base.parse_args([
            "--train_dataset", str(self.train),
            "--verified_dataset", str(self.verified),
            "--train_num", "4", "--iteration_num", "1",
            "--model", "judge", "--api-key", "key",
            "--base-url", "https://theta/v1",
            "--rubric", str(self.rubric), "--skill-dir", str(self.skill),
            "--output-dir", str(Path(self.tmp.name) / "insufficient"),
        ])
        with self.assertRaises(SystemExit):
            iter_base.iterate(args)


class CliDefaultsTest(unittest.TestCase):
    def test_defaults_match_specified_paths(self):
        import os
        from unittest.mock import patch as _patch
        with _patch.dict(os.environ, {"THETA_API_KEY": "key",
                                      "THETA_BASE_URL": "https://theta/v1"}):
            args = iter_base.parse_args(["--model", "judge"])
        self.assertEqual(args.train_dataset, iter_base.DEFAULT_TRAIN_DATASET)
        self.assertEqual(args.verified_dataset, iter_base.DEFAULT_VERIFIED_DATASET)
        self.assertEqual(args.train_num, 30)
        self.assertEqual(args.iteration_num, 3)
        self.assertEqual(args.rubric, iter_base.DEFAULT_RUBRIC)
        self.assertEqual(args.critic_model, "judge")

    def test_rubric_must_be_iterated(self):
        rules_rubric = Path(scorer.DEFAULT_SKILL_DIR) / "rubric" / "base_rules.yaml"
        with self.assertRaises(SystemExit):
            iter_base.parse_args(["--model", "judge", "--api-key", "key",
                                  "--base-url", "https://theta/v1",
                                  "--rubric", str(rules_rubric)])


if __name__ == "__main__":
    unittest.main()