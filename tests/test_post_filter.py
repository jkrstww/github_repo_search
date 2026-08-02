from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from github_repo_filter.jsonl import load_jsonl, overwrite_jsonl
from github_repo_filter.post_filter import filter_steps_for_pipeline, run_filter_pipeline, suffixed_jsonl_path


class PostFilterTest(unittest.TestCase):
    def test_suffixed_jsonl_path_appends_condition_to_stem(self) -> None:
        self.assertEqual(
            suffixed_jsonl_path(Path("data/repositories.jsonl"), "stars_gt10"),
            Path("data/repositories_stars_gt10.jsonl"),
        )

    def test_default_pipeline_filters_sequentially(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "repositories.jsonl"
            overwrite_jsonl(
                input_path,
                [
                    _record("old-low", stars=5, updated_at="2026-02-01T00:00:00Z"),
                    _record("old-high", stars=11, updated_at="2025-12-31T23:59:59Z"),
                    _record("new-high", stars=12, updated_at="2026-01-01T00:00:00Z"),
                ],
            )

            summaries = run_filter_pipeline(input_path)

            self.assertEqual([summary.output_count for summary in summaries], [2, 1])
            first_output = Path(temp_dir) / "repositories_stars_gt10.jsonl"
            second_output = Path(temp_dir) / "repositories_stars_gt10_updated_after2026.jsonl"
            self.assertEqual(
                [record["full_name"] for record in load_jsonl(first_output)],
                ["old-high", "new-high"],
            )
            self.assertEqual(
                [record["full_name"] for record in load_jsonl(second_output)],
                ["new-high"],
            )

    def test_harmony_pipeline_filters_stars_then_typescript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "repositories_harmony.jsonl"
            overwrite_jsonl(
                input_path,
                [
                    _record("low-ts", stars=5, updated_at="2026-02-01T00:00:00Z", language="TypeScript"),
                    _record("high-python", stars=11, updated_at="2026-02-01T00:00:00Z", language="Python"),
                    _record("high-ts", stars=12, updated_at="2026-02-01T00:00:00Z", language="TypeScript"),
                ],
            )

            summaries = run_filter_pipeline(input_path, steps=filter_steps_for_pipeline("harmony"))

            self.assertEqual([summary.output_count for summary in summaries], [2, 1])
            first_output = Path(temp_dir) / "repositories_harmony_stars_gt10.jsonl"
            second_output = Path(temp_dir) / "repositories_harmony_stars_gt10_language_typescript.jsonl"
            self.assertEqual(
                [record["full_name"] for record in load_jsonl(first_output)],
                ["high-python", "high-ts"],
            )
            self.assertEqual(
                [record["full_name"] for record in load_jsonl(second_output)],
                ["high-ts"],
            )


def _record(full_name: str, *, stars: int, updated_at: str, language: str = "") -> dict:
    return {
        "full_name": full_name,
        "stargazers_count": stars,
        "updated_at": updated_at,
        "language": language,
    }


if __name__ == "__main__":
    unittest.main()
