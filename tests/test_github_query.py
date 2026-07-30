from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from github_repo_filter.github import build_search_query


class BuildSearchQueryTest(unittest.TestCase):
    def test_builds_github_repository_query_from_search_and_filters(self) -> None:
        query = build_search_query(
            {
                "keywords": ["HarmonyOS benchmark"],
                "language": "Jupyter Notebook",
                "topics": ["benchmark"],
                "owner": "openai",
                "include_forks": False,
                "include_archived": False,
            },
            {
                "min_stars": 10,
                "max_stars": 100,
                "created_after": "2023-01-01",
                "created_before": "2024-01-01",
                "pushed_after": "2025-01-01",
            },
        )

        self.assertIn('"HarmonyOS benchmark"', query)
        self.assertIn('language:"Jupyter Notebook"', query)
        self.assertIn("topic:benchmark", query)
        self.assertIn("user:openai", query)
        self.assertIn("fork:false", query)
        self.assertIn("archived:false", query)
        self.assertIn("stars:10..100", query)
        self.assertIn("created:2023-01-01..2024-01-01", query)
        self.assertIn("pushed:>=2025-01-01", query)

    def test_raw_query_is_preserved(self) -> None:
        query = build_search_query({"raw_query": "stars:>100 language:Python"}, {})

        self.assertEqual(query, "stars:>100 language:Python fork:false archived:false")


if __name__ == "__main__":
    unittest.main()
