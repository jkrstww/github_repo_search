import json
import tempfile
import unittest
from pathlib import Path

import diff_trajectories


class DiffTrajectoriesTest(unittest.TestCase):
    def _submission(self, root: Path, name: str, values: dict[str, bool]) -> Path:
        submission = root / "evaluation" / "verified" / name
        (submission / "trajs_harbor").mkdir(parents=True)
        (submission / "per_instance_details.json").write_text(
            json.dumps({sample: {"resolved": value} for sample, value in values.items()}),
            encoding="utf-8",
        )
        for sample in values:
            trajectory = submission / "trajs_harbor" / sample / "trajectory.json"
            trajectory.parent.mkdir()
            trajectory.write_text(json.dumps({"sample": sample}), encoding="utf-8")
        return submission

    def test_collects_only_samples_with_different_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._submission(root, "20260101_first", {"a": True, "b": False})
            second = self._submission(root, "20260102_second", {"a": False, "b": False})

            differences = diff_trajectories.collect_differences([first, second])

            self.assertEqual(differences, {"a": {"20260101_first.json": True, "20260102_second.json": False}})

    def test_diff_num_selects_the_minority_status_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submissions = [
                self._submission(root, "20260101_first", {"a": True, "b": True}),
                self._submission(root, "20260102_second", {"a": False, "b": True}),
                self._submission(root, "20260103_third", {"a": False, "b": False}),
                self._submission(root, "20260104_fourth", {"a": False, "b": False}),
            ]

            differences = diff_trajectories.collect_differences(submissions, diff_num=2)

            self.assertEqual(set(differences), {"b"})

    def test_submissions_without_harbor_trajectories_are_skipped_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            submission = Path(tmp) / "20260101_missing_harbor"
            submission.mkdir()
            (submission / "per_instance_details.json").write_text("invalid", encoding="utf-8")

            self.assertEqual(diff_trajectories.load_available_submissions([submission]), [])

    def test_copies_trajectories_and_writes_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._submission(root, "20260101_first", {"a": True})
            second = self._submission(root, "20260102_second", {"a": False})
            output = root / "diff"
            differences = diff_trajectories.collect_differences([first, second])

            missing = diff_trajectories.copy_differing_trajectories(
                [first, second], differences, output
            )
            diff_trajectories.write_compare(output, differences)

            self.assertEqual(missing, [])
            self.assertTrue((output / "a" / "20260101_first.json").is_file())
            self.assertEqual(json.loads((output / "compare.json").read_text()), differences)


if __name__ == "__main__":
    unittest.main()
