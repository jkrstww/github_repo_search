from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from github_repo_filter.filters import filter_repositories, rejection_reasons


class FilterRepositoriesTest(unittest.TestCase):
    def test_keeps_repository_matching_configured_filters(self) -> None:
        repo = _repo(
            language="Python",
            stars=42,
            forks=8,
            created_at="2023-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            topics=["harmonyos", "benchmark"],
            license_key="mit",
        )
        filters = {
            "languages": ["Python"],
            "min_stars": 10,
            "max_stars": 100,
            "min_forks": 1,
            "created_after": "2022-01-01",
            "updated_after": "2025-01-01",
            "has_topics": ["harmonyos"],
            "license": ["MIT"],
            "exclude_archived": True,
            "exclude_forks": True,
        }

        self.assertEqual(filter_repositories([repo], filters), [repo])

    def test_rejects_repository_outside_date_and_star_bounds(self) -> None:
        repo = _repo(stars=3, updated_at="2023-01-01T00:00:00Z")
        filters = {
            "min_stars": 10,
            "updated_after": "2024-01-01",
            "exclude_archived": True,
            "exclude_forks": True,
        }

        self.assertEqual(
            rejection_reasons(repo, filters),
            ["stars_below_min", "updated_out_of_range"],
        )

    def test_rejects_archived_and_fork_by_default(self) -> None:
        repo = _repo(archived=True, fork=True)

        self.assertEqual(rejection_reasons(repo, {}), ["archived", "fork"])


def _repo(
    *,
    language: str = "Python",
    stars: int = 20,
    forks: int = 0,
    created_at: str = "2023-01-01T00:00:00Z",
    updated_at: str = "2025-01-01T00:00:00Z",
    topics: list[str] | None = None,
    license_key: str = "apache-2.0",
    archived: bool = False,
    fork: bool = False,
) -> dict:
    return {
        "full_name": "owner/repo",
        "owner": {"login": "owner"},
        "language": language,
        "stargazers_count": stars,
        "forks_count": forks,
        "created_at": created_at,
        "updated_at": updated_at,
        "topics": topics or [],
        "license": {"key": license_key, "spdx_id": license_key.upper(), "name": license_key},
        "archived": archived,
        "fork": fork,
    }


if __name__ == "__main__":
    unittest.main()
