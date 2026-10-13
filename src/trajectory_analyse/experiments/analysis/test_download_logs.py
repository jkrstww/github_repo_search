import unittest
from unittest.mock import patch

from analysis import download_logs


class ArtifactS3LocationTest(unittest.TestCase):
    def test_uses_s3_location_from_metadata(self):
        metadata = {
            "assets": {
                "trajs": "s3://another-bucket/bash-only/submission/trajs/",
            }
        }

        location = download_logs._artifact_s3_location("verified/submission", "trajs", metadata)

        self.assertEqual(location, ("another-bucket", "bash-only/submission/trajs"))

    def test_explicit_null_marks_artifact_unavailable(self):
        metadata = {"assets": {"logs": None}}

        location = download_logs._artifact_s3_location("verified/submission", "logs", metadata)

        self.assertIsNone(location)

    def test_missing_asset_uses_legacy_location(self):
        location = download_logs._artifact_s3_location("verified/submission", "trajs", {})

        self.assertEqual(
            location,
            (download_logs.S3_BUCKET, "verified/submission/trajs"),
        )

    @patch("analysis.download_logs._list_s3_folder_content")
    @patch("analysis.download_logs._submission_metadata")
    def test_check_uses_metadata_location(self, metadata_mock, list_mock):
        metadata_mock.return_value = {
            "assets": {
                "trajs": "s3://swe-bench-submissions/bash-only/submission/trajs",
            }
        }

        download_logs._check_submission("verified/submission", ["trajs"])

        list_mock.assert_called_once_with(
            "swe-bench-submissions", "bash-only/submission/trajs"
        )

    @patch("analysis.download_logs.download_s3_folder")
    @patch("analysis.download_logs._submission_metadata")
    @patch("analysis.download_logs.os.path.exists", return_value=True)
    def test_download_uses_metadata_location(self, _exists_mock, metadata_mock, download_mock):
        metadata_mock.return_value = {
            "assets": {
                "trajs": "s3://swe-bench-submissions/bash-only/submission/trajs",
            }
        }

        download_logs.download_submission("verified/submission", False, ["trajs"])

        download_mock.assert_called_once_with(
            "swe-bench-submissions",
            "bash-only/submission/trajs",
            "evaluation/verified/submission/trajs",
        )


if __name__ == "__main__":
    unittest.main()
