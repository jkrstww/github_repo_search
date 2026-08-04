from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arkts_syntax_tree import scan_repository_android_calls


class RepositoryAndroidScanTest(unittest.TestCase):
    def test_clones_scans_and_removes_temporary_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            clone_root = root / "clones"
            source.mkdir()
            self._git(source, "init", "--initial-branch=main")
            self._git(source, "config", "user.name", "Test User")
            self._git(source, "config", "user.email", "test@example.com")
            (source / "Index.ets").write_text(
                "export function migrate() { android.app.Activity.start() }\n",
                encoding="utf-8",
            )
            self._git(source, "add", "Index.ets")
            self._git(source, "commit", "-m", "initial")

            result = scan_repository_android_calls(
                {
                    "full_name": "example/repository",
                    "clone_url": str(source),
                    "default_branch": "main",
                },
                clone_root=clone_root,
                clone_timeout=30,
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["files_scanned"], 1)
            self.assertEqual(result["android_call_count"], 1)
            self.assertEqual(result["android_calls"][0]["callee"], "android.app.Activity.start")
            self.assertEqual(list(clone_root.iterdir()), [])

    def test_removes_temporary_directory_after_clone_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clone_root = Path(temp_dir) / "clones"
            result = scan_repository_android_calls(
                {
                    "full_name": "example/missing",
                    "clone_url": str(Path(temp_dir) / "does-not-exist"),
                    "default_branch": "main",
                },
                clone_root=clone_root,
                clone_timeout=30,
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"]["stage"], "clone")
            self.assertEqual(list(clone_root.iterdir()), [])

    def _git(self, repository: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repository), *args],
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
