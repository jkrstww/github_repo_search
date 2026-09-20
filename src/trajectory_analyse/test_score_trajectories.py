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
        template, dimensions = scorer.load_dimension_skill()
        self.assertIn("{{ criterion.id }}", template)
        self.assertEqual(tuple(dimensions), tuple("ABCDEF"))
        self.assertEqual([len(dimensions[d]["subcriteria"]) for d in "ABCDEF"], [3, 3, 4, 5, 4, 3])

    def test_renders_one_dimension_with_inline_trajectory(self):
        template, dimensions = scorer.load_dimension_skill()
        prompt = scorer.render_dimension_prompt(template, dimensions["A"], '{"steps": []}')
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


class CliTest(unittest.TestCase):
    def test_theta_environment_values_are_defaults(self):
        with patch.dict(os.environ, {"THETA_API_KEY": "key", "THETA_BASE_URL": "https://theta/v1"}):
            args = scorer.parse_args(["trajectory.json", "--model", "judge"])
        self.assertEqual(args.api_key, "key")
        self.assertEqual(args.base_url, "https://theta/v1")

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


if __name__ == "__main__":
    unittest.main()
