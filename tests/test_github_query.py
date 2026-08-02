from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from github_repo_filter import github
from github_repo_filter.github import build_created_ranges, build_search_queries, build_search_query, iter_search_repositories


class BuildSearchQueryTest(unittest.TestCase):
    def test_builds_github_repository_query_from_search_and_filters(self) -> None:
        query = build_search_query(
            {
                "keywords": ["HarmonyOS benchmark"],
                "in_fields": ["name", "description", "readme"],
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
        self.assertIn("in:name,description,readme", query)
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

    def test_build_search_queries_splits_created_by_month(self) -> None:
        planned = build_search_queries(
            {
                "any_keywords": ["ArkTS"],
                "in_fields": ["name", "description", "readme"],
                "include_forks": False,
                "include_archived": False,
                "created_split": {
                    "enabled": True,
                    "start": "2024-01",
                    "end": "2024-03-02",
                    "interval": "month",
                },
            },
            {"created_after": "2024-01-01"},
        )

        self.assertEqual(
            [plan.query for plan in planned],
            [
                "ArkTS in:name,description,readme fork:false archived:false created:2024-01-01..2024-01-31",
                "ArkTS in:name,description,readme fork:false archived:false created:2024-02-01..2024-02-29",
                "ArkTS in:name,description,readme fork:false archived:false created:2024-03-01..2024-03-02",
            ],
        )

    def test_build_search_queries_defaults_created_split_end_to_today(self) -> None:
        planned = build_search_queries(
            {
                "any_keywords": ["ArkTS"],
                "in_fields": ["name", "description", "readme"],
                "include_forks": False,
                "include_archived": False,
                "created_split": {
                    "enabled": True,
                    "start": "2024-01-15",
                    "end": "",
                    "interval": "month",
                },
            },
            {},
            today=date(2024, 2, 2),
        )

        self.assertEqual(
            [plan.created_range.qualifier() for plan in planned if plan.created_range],
            ["created:2024-01-15..2024-01-31", "created:2024-02-01..2024-02-02"],
        )

    def test_build_search_queries_expands_any_keywords_across_created_ranges(self) -> None:
        planned = build_search_queries(
            {
                "any_keywords": ["ArkTS", "Harmony", "鸿蒙"],
                "in_fields": ["name", "description", "readme"],
                "include_forks": False,
                "include_archived": False,
                "created_split": {
                    "enabled": True,
                    "start": "2024-01",
                    "end": "2024-01",
                    "interval": "month",
                },
            },
            {},
        )

        self.assertEqual(
            [plan.query for plan in planned],
            [
                "ArkTS in:name,description,readme fork:false archived:false created:2024-01-01..2024-01-31",
                "Harmony in:name,description,readme fork:false archived:false created:2024-01-01..2024-01-31",
                "鸿蒙 in:name,description,readme fork:false archived:false created:2024-01-01..2024-01-31",
            ],
        )

    def test_build_created_ranges_supports_day_interval(self) -> None:
        ranges = build_created_ranges("2024-01-30", "2024-02-01", interval="day")

        self.assertEqual(
            [created_range.qualifier() for created_range in ranges],
            [
                "created:2024-01-30..2024-01-30",
                "created:2024-01-31..2024-01-31",
                "created:2024-02-01..2024-02-01",
            ],
        )

    def test_iter_search_repositories_fetches_all_available_pages_by_default(self) -> None:
        def fake_request(params, *, token, timeout):
            page = int(params["page"])
            sizes = {1: 100, 2: 100, 3: 50}
            return {
                "total_count": 250,
                "incomplete_results": False,
                "items": [{"full_name": f"owner/repo-{page}-{idx}"} for idx in range(sizes[page])],
            }

        with patch.object(github, "_request_json", side_effect=fake_request):
            pages = list(iter_search_repositories("test", per_page=100, max_results=None))

        self.assertEqual([len(page.repositories) for page in pages], [100, 100, 50])
        self.assertEqual(pages[-1].fetched_count, 250)
        self.assertEqual(pages[-1].target_count, 250)

    def test_iter_search_repositories_respects_max_results(self) -> None:
        def fake_request(params, *, token, timeout):
            page = int(params["page"])
            return {
                "total_count": 250,
                "incomplete_results": False,
                "items": [{"full_name": f"owner/repo-{page}-{idx}"} for idx in range(100)],
            }

        with patch.object(github, "_request_json", side_effect=fake_request):
            pages = list(iter_search_repositories("test", per_page=100, max_results=150))

        self.assertEqual([len(page.repositories) for page in pages], [100, 50])
        self.assertEqual(pages[-1].fetched_count, 150)
        self.assertEqual(pages[-1].target_count, 150)


if __name__ == "__main__":
    unittest.main()
