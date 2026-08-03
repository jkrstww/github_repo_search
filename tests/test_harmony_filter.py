from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from github_repo_filter.harmony_filter import classify_harmony_repository


class HarmonyFilterTest(unittest.TestCase):
    def test_accepts_ets_project_with_multiple_build_markers(self) -> None:
        evidence = classify_harmony_repository(
            _record("example/app", description="A mobile application"),
            [
                "build-profile.json5",
                "entry/hvigorfile.ts",
                "entry/src/main/ets/pages/Index.ets",
            ],
        )

        self.assertTrue(evidence.accepted)
        self.assertEqual(evidence.confidence, "high")
        self.assertEqual(evidence.ets_file_count, 1)

    def test_accepts_single_marker_when_metadata_is_specific(self) -> None:
        evidence = classify_harmony_repository(
            _record("example/router", description="ArkTS router for HarmonyOS"),
            ["hvigorfile.ts", "src/main/Router.ets"],
        )

        self.assertTrue(evidence.accepted)
        self.assertEqual(evidence.confidence, "medium")

    def test_accepts_marker_only_repository_with_specific_metadata(self) -> None:
        evidence = classify_harmony_repository(
            _record("example/app", description="HarmonyOS NEXT application"),
            ["build-profile.json5", "hvigorfile.ts", "oh-package.json5"],
        )

        self.assertTrue(evidence.accepted)
        self.assertEqual(evidence.confidence, "medium")

    def test_rejects_generic_typescript_repository(self) -> None:
        evidence = classify_harmony_repository(
            _record("example/harmony", description="Harmony for distributed services"),
            ["package.json", "src/index.ts", "README.md"],
        )

        self.assertFalse(evidence.accepted)
        self.assertEqual(evidence.confidence, "none")

    def test_source_only_repository_is_low_confidence(self) -> None:
        evidence = classify_harmony_repository(
            _record("example/samples", description="OpenHarmony ArkTS examples"),
            ["samples/Example.ets"],
        )

        self.assertFalse(evidence.accepted)
        self.assertEqual(evidence.confidence, "low")


def _record(full_name: str, *, description: str) -> dict:
    return {
        "full_name": full_name,
        "description": description,
        "topics": [],
    }


if __name__ == "__main__":
    unittest.main()
