import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import score_trajectories as scorer


class SkillLoadingTest(unittest.TestCase):
    def test_loads_current_prompt_and_rubric(self):
        templates, dimensions = scorer.load_dimension_skill()
        self.assertEqual(set(templates), {"legacy", "score_rules", "iterated"})
        self.assertIn("{{ criterion.id }}", templates["legacy"])
        self.assertIn("{{ criterion.id }}", templates["score_rules"])
        self.assertIn("{{ criterion.id }}", templates["iterated"])
        self.assertEqual(tuple(dimensions), tuple("ABCDEF"))
        self.assertEqual([len(dimensions[d]["subcriteria"]) for d in "ABCDEF"], [3, 3, 4, 5, 4, 3])
        self.assertTrue(all(d["scoring_mode"] == "legacy" for d in dimensions.values()))

    def test_renders_one_dimension_with_inline_trajectory(self):
        templates, dimensions = scorer.load_dimension_skill()
        prompt = scorer.render_dimension_prompt(templates["legacy"], dimensions["A"], '{"steps": []}')
        self.assertIn("ID:\nA", prompt)
        self.assertIn("A1", prompt)
        self.assertIn('{"steps": []}', prompt)


class ValidationTest(unittest.TestCase):
    def setUp(self):
        _, self.dimensions = scorer.load_dimension_skill()

    def test_requires_exact_boolean_subcriteria(self):
        criterion = self.dimensions["A"]
        good = {
            "criterion_id": "A", "applicable": True,
            "subcriteria": [{"id": item["id"], "passed": True}
                            for item in criterion["subcriteria"]],
        }
        self.assertEqual(scorer.validate_dimension_result(good, criterion), (True, ""))
        bad = json.loads(json.dumps(good))
        bad["subcriteria"][0]["passed"] = "false"
        self.assertFalse(scorer.validate_dimension_result(bad, criterion)[0])

    def test_not_applicable_allows_null_subcriteria(self):
        criterion = self.dimensions["F"]
        self.assertEqual(
            scorer.validate_dimension_result(
                {"criterion_id": "F", "applicable": False, "subcriteria": None}, criterion
            ),
            (True, ""),
        )


class JsonExtractionTest(unittest.TestCase):
    def test_extracts_object_after_prose_or_a_markdown_fence(self):
        result = scorer._extract_json('Here is the result:\n```json\n{"criterion_id": "A"}\n```')
        self.assertEqual(result, {"criterion_id": "A"})

    def test_extracts_first_object_without_combining_multiple_objects(self):
        result = scorer._extract_json('{"criterion_id": "A"}\n{"criterion_id": "B"}')
        self.assertEqual(result, {"criterion_id": "A"})


class MergeTest(unittest.TestCase):
    def test_merges_six_worker_files_and_weights_scores(self):
        _, dimensions = scorer.load_dimension_skill()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            output = root / "scores"
            trajectory.write_text("{}", encoding="utf-8")
            output.mkdir()
            for dimension, criterion in dimensions.items():
                result = {
                    "criterion_id": dimension, "applicable": True,
                    "subcriteria": [{"id": item["id"], "passed": True, "evidence": []}
                                    for item in criterion["subcriteria"]],
                }
                (output / f"trajectory.score.{dimension}.json").write_text(
                    json.dumps(result), encoding="utf-8"
                )
            merged = scorer.merge_dimension_scores(trajectory, output, dimensions)
            self.assertEqual(merged["total_score"], 100)
            self.assertTrue((output / "trajectory.score.json").is_file())

    def test_missing_worker_is_reported_as_evidence_gap(self):
        _, dimensions = scorer.load_dimension_skill()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            output = root / "scores"
            trajectory.write_text("{}", encoding="utf-8")
            output.mkdir()
            merged = scorer.merge_dimension_scores(trajectory, output, dimensions)
            self.assertEqual(merged["total_score"], 0)
            self.assertEqual(merged["evidence_confidence"], "low")
            self.assertEqual(len(merged["evidence_gaps"]), 6)


class RunnerTest(unittest.TestCase):
    def test_starts_one_process_per_dimension(self):
        class InlineProcess:
            names = []

            def __init__(self, *, target, args, name):
                self.target, self.args, self.name = target, args, name

            def start(self):
                self.names.append(self.name)
                self.target(*self.args)

            def join(self):
                return None

        def completion(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            dimension = next(d for d in "ABCDEF" if f"ID:\n{d}\n" in prompt)
            _, dimensions = scorer.load_dimension_skill()
            return json.dumps({
                "criterion_id": dimension, "applicable": True,
                "subcriteria": [{"id": item["id"], "passed": True}
                                for item in dimensions[dimension]["subcriteria"]],
            })

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            trajectory.write_text('{"steps": []}', encoding="utf-8")
            with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
                 patch("score_trajectories._http_json", side_effect=completion):
                result = scorer.score_dimensions(
                    trajectory, root / "scores", api_key="key",
                    base_url="https://theta/v1", model="judge", timeout=5,
                )
            self.assertEqual(InlineProcess.names, [f"trajectory-score-{d}" for d in "ABCDEF"])
            self.assertEqual(result["total_score"], 100)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        _, self.dimensions = scorer.load_dimension_skill()

    def _valid_result(self, dimension):
        return {
            "criterion_id": dimension, "applicable": True,
            "subcriteria": [{"id": item["id"], "passed": True}
                            for item in self.dimensions[dimension]["subcriteria"]],
        }

    def _write(self, output, dimension, payload):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (output / f"trajectory.score.{dimension}.json").write_text(text, encoding="utf-8")

    def _run_resume(self, root):
        class InlineProcess:
            names = []

            def __init__(self, *, target, args, name):
                self.target, self.args, self.name = target, args, name

            def start(self):
                self.names.append(self.name)
                self.target(*self.args)

            def join(self):
                return None

        def completion(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            dimension = next(d for d in "ABCDEF" if f"ID:\n{d}\n" in prompt)
            return json.dumps(self._valid_result(dimension))

        with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
             patch("score_trajectories._http_json", side_effect=completion):
            merged = scorer.score_dimensions(
                root / "trajectory.json", root / "scores", api_key="key",
                base_url="https://theta/v1", model="judge", timeout=5, resume=True,
            )
        return InlineProcess.names, merged

    def _make_root(self, tmp):
        root = Path(tmp)
        (root / "trajectory.json").write_text('{"steps": []}', encoding="utf-8")
        (root / "scores").mkdir()
        return root

    def test_rescores_only_errored_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_root(tmp)
            output = root / "scores"
            for dimension in "ABCDF":
                self._write(output, dimension, self._valid_result(dimension))
            kept = (output / "trajectory.score.A.json").read_text(encoding="utf-8")
            self._write(output, "E", {
                "criterion_id": "E", "applicable": False, "subcriteria": None,
                "error": "RuntimeError: network error: TimeoutError: timed out",
            })
            names, merged = self._run_resume(root)
            self.assertEqual(names, ["trajectory-score-E"])
            self.assertEqual(merged["total_score"], 100)
            # 已完成的维度文件内容保持原样，不被重写
            self.assertEqual(
                (output / "trajectory.score.A.json").read_text(encoding="utf-8"), kept
            )

    def test_rescores_missing_and_unparsable_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_root(tmp)
            output = root / "scores"
            for dimension in "ABCD":
                self._write(output, dimension, self._valid_result(dimension))
            self._write(output, "F", "{not json")
            names, merged = self._run_resume(root)
            self.assertEqual(names, ["trajectory-score-E", "trajectory-score-F"])
            self.assertEqual(merged["total_score"], 100)

    def test_not_applicable_without_error_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_root(tmp)
            output = root / "scores"
            for dimension in "ABCDE":
                self._write(output, dimension, self._valid_result(dimension))
            self._write(output, "F", {
                "criterion_id": "F", "applicable": False, "subcriteria": None,
            })
            names, merged = self._run_resume(root)
            self.assertEqual(names, [])
            f_max = float(self.dimensions["F"].get("max_score", 0))
            self.assertEqual(merged["total_score"], 100 - f_max)
            self.assertEqual(merged["evidence_gaps"], [])


class CliTest(unittest.TestCase):
    def test_theta_environment_values_are_defaults(self):
        with patch.dict(os.environ, {"THETA_API_KEY": "key", "THETA_BASE_URL": "https://theta/v1"}):
            args = scorer.parse_args(["trajectory.json", "--model", "judge"])
        self.assertEqual(args.api_key, "key")
        self.assertEqual(args.base_url, "https://theta/v1")

    def test_rubric_defaults_to_base_origin_and_keeps_explicit_value(self):
        with patch.dict(os.environ, {"THETA_API_KEY": "key", "THETA_BASE_URL": "https://theta/v1"}):
            args = scorer.parse_args(["trajectory.json", "--model", "judge"])
        self.assertEqual(args.rubric, scorer.DEFAULT_RUBRIC)
        rules_rubric = scorer.DEFAULT_SKILL_DIR / "rubric" / "base_rules.yaml"
        with patch.dict(os.environ, {"THETA_API_KEY": "key", "THETA_BASE_URL": "https://theta/v1"}):
            args = scorer.parse_args(["trajectory.json", "--model", "judge",
                                       "--rubric", str(rules_rubric)])
        self.assertEqual(args.rubric, rules_rubric)

    def test_resume_flag_defaults_off(self):
        with patch.dict(os.environ, {"THETA_API_KEY": "key", "THETA_BASE_URL": "https://theta/v1"}):
            self.assertFalse(scorer.parse_args(["trajectory.json", "--model", "judge"]).resume)
            self.assertTrue(
                scorer.parse_args(["trajectory.json", "--model", "judge", "--resume"]).resume
            )

    def test_dry_run_does_not_call_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            trajectory = Path(tmp) / "trajectory.json"
            trajectory.write_text("{}", encoding="utf-8")
            with patch("score_trajectories.score_dimensions") as run:
                self.assertEqual(scorer.main([
                    str(trajectory), "--api-key", "key", "--base-url", "https://theta",
                    "--model", "judge", "--dry-run",
                ]), 0)
                run.assert_not_called()


RULES_RUBRIC = scorer.DEFAULT_SKILL_DIR / "rubric" / "base_rules.yaml"


def _rules_verdicts(criterion: dict, fail_first_rules: int = 0) -> dict:
    """Build a valid score_rules worker result for ``criterion``."""
    subcriteria = []
    for sub in criterion["subcriteria"]:
        rules = []
        for index in range(len(sub["score_rules"])):
            passed = not (index < fail_first_rules and sub["id"].endswith("1"))
            rules.append({"index": index, "passed": passed,
                          "evidence": [], "reason": "ok"})
        subcriteria.append({"id": sub["id"], "rules": rules})
    return {"criterion_id": criterion["id"], "applicable": True,
            "subcriteria": subcriteria}


class RulesSkillLoadingTest(unittest.TestCase):
    def test_loads_rules_rubric_with_score_rules_mode(self):
        templates, dimensions = scorer.load_dimension_skill(rubric_path=RULES_RUBRIC)
        self.assertEqual(tuple(dimensions), tuple("ABCDEF"))
        self.assertEqual([len(dimensions[d]["subcriteria"]) for d in "ABCDEF"], [3, 3, 4, 5, 4, 3])
        self.assertTrue(all(d["scoring_mode"] == "score_rules" for d in dimensions.values()))
        # 每个子维度都有非空 score_rules，且权重之和等于维度满分
        for dimension in dimensions.values():
            self.assertAlmostEqual(
                sum(float(s["score"]) for s in dimension["subcriteria"]),
                float(dimension["max_score"]), places=6)

    def test_renders_rules_template_without_pass_condition(self):
        templates, dimensions = scorer.load_dimension_skill(rubric_path=RULES_RUBRIC)
        prompt = scorer.render_dimension_prompt(
            templates["score_rules"], dimensions["A"], '{"steps": []}')
        self.assertIn("ID:\nA", prompt)
        self.assertIn("Scoring rules", prompt)
        self.assertIn("A1", prompt)
        self.assertNotIn("Pass condition", prompt)
        self.assertIn('{"steps": []}', prompt)

    def test_mixed_subcriterion_formats_are_rejected(self):
        criterion = {"id": "X", "subcriteria": [
            {"id": "X1", "score": 1, "score_rules": ["a"]},
            {"id": "X2", "score": 1, "pass_condition": "b"},
        ]}
        with self.assertRaises(ValueError):
            scorer._criterion_scoring_mode(criterion)

    def test_invalid_score_rules_and_weights_are_rejected(self):
        with self.assertRaises(ValueError):
            scorer._criterion_scoring_mode(
                {"id": "X", "subcriteria": [{"id": "X1", "score": 1, "score_rules": ["a", ""]}]})
        with self.assertRaises(ValueError):
            scorer._criterion_scoring_mode(
                {"id": "X", "subcriteria": [{"id": "X1", "score": "5", "score_rules": ["a"]}]})

    def test_rubric_without_weights_is_iterated(self):
        self.assertEqual(
            scorer._criterion_scoring_mode(
                {"id": "X", "subcriteria": [{"id": "X1", "score_rules": ["a"]}]}),
            "iterated")
        with self.assertRaises(ValueError):
            # 同一 criterion 内有 scoring 权重混入
            scorer._criterion_scoring_mode(
                {"id": "X", "subcriteria": [
                    {"id": "X1", "score_rules": ["a"]},
                    {"id": "X2", "score": 1, "score_rules": ["b"]}]})


class RulesValidationTest(unittest.TestCase):
    def setUp(self):
        _, self.dimensions = scorer.load_dimension_skill(rubric_path=RULES_RUBRIC)
        self.criterion = self.dimensions["A"]

    def test_complete_ordered_verdicts_are_valid(self):
        self.assertEqual(
            scorer.validate_dimension_result(_rules_verdicts(self.criterion), self.criterion),
            (True, ""))

    def test_not_applicable_allows_null_subcriteria(self):
        self.assertEqual(
            scorer.validate_dimension_result(
                {"criterion_id": "A", "applicable": False, "subcriteria": None},
                self.criterion)[0], True)

    def test_wrong_rule_count_is_rejected(self):
        bad = _rules_verdicts(self.criterion)
        bad["subcriteria"][0]["rules"] = bad["subcriteria"][0]["rules"][:-1]
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])

    def test_non_boolean_or_misindexed_verdicts_are_rejected(self):
        bad = _rules_verdicts(self.criterion)
        bad["subcriteria"][0]["rules"][0]["passed"] = "yes"
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])
        bad = _rules_verdicts(self.criterion)
        bad["subcriteria"][0]["rules"][0]["index"] = 1
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])

    def test_missing_or_extra_subcriterion_is_rejected(self):
        bad = _rules_verdicts(self.criterion)
        del bad["subcriteria"][0]
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])
        bad = _rules_verdicts(self.criterion)
        bad["subcriteria"].append({"id": "A9", "rules": []})
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])


class RulesMergeTest(unittest.TestCase):
    def test_subcriterion_score_is_pass_rate_and_dimension_is_weighted(self):
        _, dimensions = scorer.load_dimension_skill(rubric_path=RULES_RUBRIC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            output = root / "scores"
            trajectory.write_text("{}", encoding="utf-8")
            output.mkdir()
            for dimension, criterion in dimensions.items():
                result = _rules_verdicts(criterion)
                if dimension == "A":
                    # A1: 4 条细则通过 3 条 → 0.75 分，贡献 3.75
                    result["subcriteria"][0] = _rules_verdicts(
                        criterion, fail_first_rules=1)["subcriteria"][0]
                (output / f"trajectory.score.{dimension}.json").write_text(
                    json.dumps(result), encoding="utf-8"
                )
            merged = scorer.merge_dimension_scores(trajectory, output, dimensions)
            a1 = next(e for e in merged["criterion_scores"] if e["id"] == "A1")
            self.assertEqual(a1["score"], 0.75)
            self.assertEqual(a1["pass_rate"], 0.75)
            self.assertEqual(a1["weighted_score"], 3.75)
            self.assertEqual(a1["passed_rules"], 3)
            self.assertEqual(a1["total_rules"], 4)
            self.assertEqual(merged["scores"]["A"]["score"], 13.75)
            self.assertAlmostEqual(merged["total_score"], 98.75)

    def test_missing_worker_is_reported_as_evidence_gap(self):
        _, dimensions = scorer.load_dimension_skill(rubric_path=RULES_RUBRIC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            output = root / "scores"
            trajectory.write_text("{}", encoding="utf-8")
            output.mkdir()
            merged = scorer.merge_dimension_scores(trajectory, output, dimensions)
            self.assertEqual(merged["total_score"], 0)
            self.assertEqual(merged["evidence_confidence"], "low")
            self.assertEqual(len(merged["evidence_gaps"]), 6)


class RulesRunnerTest(unittest.TestCase):
    def test_scores_all_dimensions_with_rules_rubric(self):
        class InlineProcess:
            names = []

            def __init__(self, *, target, args, name):
                self.target, self.args, self.name = target, args, name

            def start(self):
                self.names.append(self.name)
                self.target(*self.args)

            def join(self):
                return None

        _, dimensions = scorer.load_dimension_skill(rubric_path=RULES_RUBRIC)

        def completion(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            dimension = next(d for d in "ABCDEF" if f"ID:\n{d}\n" in prompt)
            # worker 必须选了 score_rules 模板
            assert "Scoring rules" in prompt, "legacy template rendered for rules rubric"
            return json.dumps(_rules_verdicts(dimensions[dimension]))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            trajectory.write_text('{"steps": []}', encoding="utf-8")
            with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
                 patch("score_trajectories._http_json", side_effect=completion):
                result = scorer.score_dimensions(
                    trajectory, root / "scores", api_key="key",
                    base_url="https://theta/v1", model="judge", timeout=5,
                    rubric_path=RULES_RUBRIC,
                )
            self.assertEqual(InlineProcess.names, [f"trajectory-score-{d}" for d in "ABCDEF"])
            self.assertEqual(result["total_score"], 100)
            self.assertTrue(all(e["score"] == 1.0 for e in result["criterion_scores"]))


ITERATED_RUBRIC = scorer.DEFAULT_SKILL_DIR / "iterated_rubric" / "base_0.yaml"


def _iterated_verdicts(criterion: dict, fail_first_rules: int = 0) -> dict:
    """Build a valid ``iterated`` worker result for ``criterion``."""
    subcriteria = []
    for sub in criterion["subcriteria"]:
        rules = []
        total = len(sub["score_rules"])
        failed = fail_first_rules if sub["id"].endswith("1") else 0
        for index in range(total):
            rules.append({"index": index, "passed": index >= failed,
                          "evidence": [], "reason": "ok"})
        passed = total - failed
        subcriteria.append({
            "id": sub["id"], "score": round(passed / total, 2),
            "reasoning": f"{passed}/{total} rules passed", "rules": rules,
        })
    return {"criterion_id": criterion["id"], "applicable": True,
            "subcriteria": subcriteria}


class IteratedSkillLoadingTest(unittest.TestCase):
    def test_loads_base_0_rubric_in_iterated_mode(self):
        templates, dimensions = scorer.load_dimension_skill(rubric_path=ITERATED_RUBRIC)
        self.assertEqual(tuple(dimensions), tuple("ABCDEF"))
        self.assertTrue(all(d["scoring_mode"] == "iterated" for d in dimensions.values()))
        # iterated rubric 无权重：子维度没有 score，维度也没有 max_score
        for dimension in dimensions.values():
            self.assertNotIn("max_score", dimension)
            for sub in dimension["subcriteria"]:
                self.assertNotIn("score", sub)

    def test_renders_iterated_template_with_rule_list(self):
        templates, dimensions = scorer.load_dimension_skill(rubric_path=ITERATED_RUBRIC)
        prompt = scorer.render_dimension_prompt(
            templates["iterated"], dimensions["A"], '{"steps": []}')
        self.assertIn("ID:\nA", prompt)
        self.assertIn("A1", prompt)
        self.assertIn("Scoring rules", prompt)
        self.assertIn("between 0 and 1", prompt)
        self.assertIn('{"steps": []}', prompt)
        # base_0 的滚动提示里没有 examples 和权重，模板也不应引用它们
        self.assertNotIn("Positive examples", prompt)
        self.assertNotIn("Maximum score", prompt)


class IteratedValidationTest(unittest.TestCase):
    def setUp(self):
        _, self.dimensions = scorer.load_dimension_skill(rubric_path=ITERATED_RUBRIC)
        self.criterion = self.dimensions["A"]

    def test_complete_verdicts_with_matching_scores_are_valid(self):
        self.assertEqual(
            scorer.validate_dimension_result(_iterated_verdicts(self.criterion), self.criterion),
            (True, ""))

    def test_wrong_rule_count_is_rejected(self):
        bad = _iterated_verdicts(self.criterion)
        bad["subcriteria"][0]["rules"] = bad["subcriteria"][0]["rules"][:-1]
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])

    def test_out_of_range_score_is_rejected(self):
        bad = _iterated_verdicts(self.criterion)
        bad["subcriteria"][0]["score"] = 1.5
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])
        bad["subcriteria"][0]["score"] = "0.75"
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])

    def test_score_inconsistent_with_pass_rate_is_rejected(self):
        bad = _iterated_verdicts(self.criterion, fail_first_rules=1)
        # 3/4 通过却报 0.9：分数没有跟随通过的 score_rules 数量
        bad["subcriteria"][0]["score"] = 0.9
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])

    def test_missing_reasoning_is_rejected(self):
        bad = _iterated_verdicts(self.criterion)
        bad["subcriteria"][0]["reasoning"] = " "
        self.assertFalse(scorer.validate_dimension_result(bad, self.criterion)[0])


class IteratedMergeTest(unittest.TestCase):
    def test_dimension_score_is_subcriterion_mean_and_total_is_dimension_mean(self):
        _, dimensions = scorer.load_dimension_skill(rubric_path=ITERATED_RUBRIC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            output = root / "scores"
            trajectory.write_text("{}", encoding="utf-8")
            output.mkdir()
            for dimension, criterion in dimensions.items():
                result = _iterated_verdicts(criterion, fail_first_rules=1)
                (output / f"trajectory.score.{dimension}.json").write_text(
                    json.dumps(result), encoding="utf-8"
                )
            merged = scorer.merge_dimension_scores(trajectory, output, dimensions)
            # A1：4 条细则通过 3 条 → 0.75；每个维度末位为 1 的子维度各失败 1 条细则
            a1 = next(e for e in merged["criterion_scores"] if e["id"] == "A1")
            self.assertEqual(a1["score"], 0.75)
            self.assertEqual(a1["max_score"], 1.0)
            self.assertEqual(a1["passed_rules"], 3)
            self.assertEqual(a1["total_rules"], 4)
            self.assertEqual(merged["scores"]["A"]["max_score"], 1.0)
            # 按细则数直接推演：每个维度均值为（(n-1)/n 与全 1）的平均
            expected = [
                sum(round((len(s["score_rules"]) - 1) / len(s["score_rules"]), 2)
                    if s["id"].endswith("1") else 1.0
                    for s in criterion["subcriteria"]) / len(criterion["subcriteria"])
                for criterion in dimensions.values()]
            self.assertAlmostEqual(merged["scores"]["A"]["score"], expected[0])
            self.assertAlmostEqual(merged["raw_total_score"], sum(expected))
            self.assertAlmostEqual(merged["total_score"], sum(expected) / 6)

    def test_missing_worker_is_reported_as_evidence_gap(self):
        _, dimensions = scorer.load_dimension_skill(rubric_path=ITERATED_RUBRIC)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            output = root / "scores"
            trajectory.write_text("{}", encoding="utf-8")
            output.mkdir()
            merged = scorer.merge_dimension_scores(trajectory, output, dimensions)
            self.assertEqual(merged["total_score"], 0)
            self.assertEqual(merged["evidence_confidence"], "low")
            self.assertEqual(len(merged["evidence_gaps"]), 6)


class IteratedRunnerTest(unittest.TestCase):
    def test_scores_all_dimensions_with_iterated_rubric(self):
        class InlineProcess:
            names = []

            def __init__(self, *, target, args, name):
                self.target, self.args, self.name = target, args, name

            def start(self):
                self.names.append(self.name)
                self.target(*self.args)

            def join(self):
                return None

        _, dimensions = scorer.load_dimension_skill(rubric_path=ITERATED_RUBRIC)

        def completion(_url, _headers, body, _timeout):
            prompt = body["messages"][0]["content"]
            dimension = next(d for d in "ABCDEF" if f"ID:\n{d}\n" in prompt)
            # worker 必须选了 iterated 模板（0~1 分 + reasoning 输出）
            assert "between 0 and 1" in prompt, "wrong template rendered for iterated rubric"
            return json.dumps(_iterated_verdicts(dimensions[dimension]))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trajectory = root / "trajectory.json"
            trajectory.write_text('{"steps": []}', encoding="utf-8")
            with patch("score_trajectories.multiprocessing.Process", InlineProcess), \
                 patch("score_trajectories._http_json", side_effect=completion):
                result = scorer.score_dimensions(
                    trajectory, root / "scores", api_key="key",
                    base_url="https://theta/v1", model="judge", timeout=5,
                    rubric_path=ITERATED_RUBRIC,
                )
            self.assertEqual(InlineProcess.names, [f"trajectory-score-{d}" for d in "ABCDEF"])
            self.assertAlmostEqual(result["total_score"], 1.0)
            self.assertTrue(all(e["score"] == 1.0 for e in result["criterion_scores"]))


if __name__ == "__main__":
    unittest.main()
