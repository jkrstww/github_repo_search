import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analyse_trajectories


class AnalyseTrajectoriesTest(unittest.TestCase):
    def test_load_sample_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compare.json"
            path.write_text(json.dumps({"sample": {"model.json": True}}), encoding="utf-8")
            self.assertEqual(
                analyse_trajectories.load_sample_labels(path, "sample"),
                {"model.json": True},
            )

    @patch("analyse_trajectories.subprocess.run")
    def test_analyse_writes_codex_report(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "# 轨迹分析\n结论"
        run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "sample"
            sample.mkdir()
            (sample / "model.json").write_text("{}", encoding="utf-8")
            labels = root / "compare.json"
            labels.write_text(json.dumps({"sample": {"model.json": True}}), encoding="utf-8")

            output = analyse_trajectories.analyse(sample, labels, timeout=12)

            self.assertEqual(output, (sample / "analyse.md").resolve())
            self.assertEqual((sample / "analyse.md").read_text(), "# 轨迹分析\n结论\n")
            command = run.call_args.args[0]
            self.assertEqual(command[:4], ["codex", "exec", "--ephemeral", "--sandbox"])
            self.assertIn("model.json", run.call_args.args[0][-1])
            self.assertIn("true", run.call_args.args[0][-1])

    def test_missing_sample_label_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "sample"
            sample.mkdir()
            (sample / "model.json").write_text("{}", encoding="utf-8")
            labels = root / "compare.json"
            labels.write_text("{}", encoding="utf-8")
            with self.assertRaises(KeyError):
                analyse_trajectories.analyse(sample, labels)


if __name__ == "__main__":
    unittest.main()
