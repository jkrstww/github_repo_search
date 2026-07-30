from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from github_repo_filter.jsonl import load_jsonl, write_jsonl


class JsonlPersistenceTest(unittest.TestCase):
    def test_dedupe_updates_existing_full_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "repos.jsonl"

            first = write_jsonl(path, [{"full_name": "owner/repo", "stars": 1}], dedupe=True)
            second = write_jsonl(path, [{"full_name": "owner/repo", "stars": 2}], dedupe=True)

            self.assertEqual(first.inserted, 1)
            self.assertEqual(second.inserted, 0)
            self.assertEqual(second.updated, 1)
            self.assertEqual(load_jsonl(path), [{"full_name": "owner/repo", "stars": 2}])

    def test_append_mode_keeps_duplicate_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "repos.jsonl"

            write_jsonl(path, [{"full_name": "owner/repo"}], dedupe=False)
            write_jsonl(path, [{"full_name": "owner/repo"}], dedupe=False)

            self.assertEqual(len(load_jsonl(path)), 2)


if __name__ == "__main__":
    unittest.main()
