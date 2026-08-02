from __future__ import annotations

import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from github_repo_filter.cli import ProgressBar, RunState, _handle_page
from github_repo_filter.github import SearchPage
from github_repo_filter.jsonl import load_jsonl


class CliIncrementalWriteTest(unittest.TestCase):
    def test_handle_page_writes_matches_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "repos.jsonl"
            args = argparse.Namespace(no_write=False, explain_filtered=0)
            filters = {"exclude_archived": True, "exclude_forks": True}
            output = {"path": str(output_path), "dedupe": True}
            state = RunState()
            progress = ProgressBar(disabled=True)

            with contextlib.redirect_stdout(io.StringIO()):
                _handle_page(
                    _page("owner/first", page=1, fetched_count=1, target_count=2),
                    filters=filters,
                    output=output,
                    args=args,
                    state=state,
                    progress=progress,
                )

            self.assertEqual([record["full_name"] for record in load_jsonl(output_path)], ["owner/first"])

            with contextlib.redirect_stdout(io.StringIO()):
                _handle_page(
                    _page("owner/second", page=2, fetched_count=2, target_count=2),
                    filters=filters,
                    output=output,
                    args=args,
                    state=state,
                    progress=progress,
                )

            self.assertEqual(
                [record["full_name"] for record in load_jsonl(output_path)],
                ["owner/first", "owner/second"],
            )


def _page(full_name: str, *, page: int, fetched_count: int, target_count: int) -> SearchPage:
    return SearchPage(
        repositories=[
            {
                "full_name": full_name,
                "name": full_name.split("/", 1)[1],
                "owner": {"login": full_name.split("/", 1)[0]},
                "html_url": f"https://github.com/{full_name}",
                "stargazers_count": 1,
                "forks_count": 0,
                "open_issues_count": 0,
                "topics": [],
                "archived": False,
                "fork": False,
            }
        ],
        total_count=target_count,
        incomplete_results=False,
        query="test",
        page=page,
        per_page=1,
        fetched_count=fetched_count,
        target_count=target_count,
    )


if __name__ == "__main__":
    unittest.main()
