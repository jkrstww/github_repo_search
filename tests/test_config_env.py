from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from github_repo_filter.config import load_env_value


class LoadEnvValueTest(unittest.TestCase):
    def test_reads_quoted_value_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text('GITHUB_TOKEN="from-file"\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                value, source = load_env_value("GITHUB_TOKEN", env_path)

        self.assertEqual(value, "from-file")
        self.assertEqual(source, str(env_path))

    def test_environment_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("GITHUB_TOKEN=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"GITHUB_TOKEN": "from-environment"}, clear=True):
                value, source = load_env_value("GITHUB_TOKEN", env_path)

        self.assertEqual(value, "from-environment")
        self.assertEqual(source, "environment")


if __name__ == "__main__":
    unittest.main()
