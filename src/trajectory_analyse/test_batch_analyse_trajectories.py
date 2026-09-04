import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import batch_analyse_trajectories


class BatchAnalyseTrajectoriesTest(unittest.TestCase):
    def test_find_sample_directories_is_sorted_and_ignores_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "z-sample").mkdir()
            (root / "a-sample").mkdir()
            (root / "compare.json").write_text("{}", encoding="utf-8")

            self.assertEqual(
                [path.name for path in batch_analyse_trajectories.find_sample_directories(root)],
                ["a-sample", "z-sample"],
            )

    @patch("batch_analyse_trajectories.analyse")
    def test_batch_continues_after_failure(self, analyse):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a-sample"
            second = root / "b-sample"
            first.mkdir()
            second.mkdir()
            label = root / "compare.json"
            label.write_text("{}", encoding="utf-8")
            analyse.side_effect = [RuntimeError("codex failed"), first / "analyse.md"]

            succeeded, failed = batch_analyse_trajectories.batch_analyse(root, label)

            self.assertEqual(succeeded, [first / "analyse.md"])
            self.assertEqual(
                [(path, str(exc)) for path, exc in failed],
                [(first.resolve(), "codex failed")],
            )
            self.assertEqual(analyse.call_count, 2)


if __name__ == "__main__":
    unittest.main()
