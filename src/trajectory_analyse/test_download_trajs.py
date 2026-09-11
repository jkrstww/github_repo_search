import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import download_trajs


class DownloadTrajsTest(unittest.TestCase):
    def test_find_submissions_filters_by_year_and_preserves_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            evaluation = Path(tmp) / "evaluation"
            (evaluation / "verified" / "20260101_model").mkdir(parents=True)
            (evaluation / "verified" / "20250101_model").mkdir()
            (evaluation / "multilingual" / "20260202_model").mkdir(parents=True)

            found = download_trajs.find_submissions(evaluation, 2026)

            self.assertEqual(
                {path.name for path, _ in found}, {"20260101_model", "20260202_model"}
            )
            self.assertEqual(
                {download_path for _, download_path in found},
                {
                    "evaluation/multilingual/20260202_model",
                    "evaluation/verified/20260101_model",
                },
            )

    @patch("download_trajs.subprocess.run")
    def test_existing_trajs_directory_is_skipped_even_when_empty(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            experiments = Path(tmp)
            submission = experiments / "evaluation" / "verified" / "20260101_model"
            (submission / "trajs").mkdir(parents=True)

            result = download_trajs.download_submission(
                submission,
                "evaluation/verified/20260101_model",
                experiments_dir=experiments,
            )

            self.assertEqual(result, 0)
            run.assert_not_called()

    @patch("download_trajs.subprocess.run")
    def test_download_invokes_existing_module(self, run):
        run.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as tmp:
            experiments = Path(tmp)
            submission = experiments / "evaluation" / "verified" / "20260101_model"
            submission.mkdir(parents=True)

            result = download_trajs.download_submission(
                submission,
                "evaluation/verified/20260101_model",
                experiments_dir=experiments,
            )

            self.assertEqual(result, 0)
            run.assert_called_once_with(
                [
                    download_trajs.sys.executable,
                    "-m",
                    "analysis.download_logs",
                    "evaluation/verified/20260101_model",
                    "--only_trajs",
                ],
                cwd=experiments,
            )


if __name__ == "__main__":
    unittest.main()
