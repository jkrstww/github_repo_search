from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arkts_syntax_tree import detect_android_calls, parse_source, write_syntax_tree_outputs


class AndroidCallDetectionTest(unittest.TestCase):
    def test_detects_android_calls_and_ignores_non_code_text(self) -> None:
        source = """\
export class Migrator {
  migrate() {
    android.app.Activity.start()
    android ?. content ?. open()
    // android.fake.Comment.call()
    const sample = "android.fake.String.call()"
    someandroid.app.run()
  }
}
"""
        parsed = parse_source(source, path="entry/src/main/ets/Migrator.ets")

        with tempfile.TemporaryDirectory() as temp_dir:
            syntax_tree = Path(temp_dir) / "tree.jsonl"
            write_syntax_tree_outputs([parsed], output_path=syntax_tree)
            report = detect_android_calls(syntax_tree)

        self.assertEqual(report["files_scanned"], 1)
        self.assertEqual(report["android_call_count"], 2)
        self.assertEqual(
            [call["callee"] for call in report["android_calls"]],
            ["android.app.Activity.start", "android.content.open"],
        )
        self.assertEqual(report["android_calls"][0]["line"], 3)
        self.assertEqual(report["android_calls"][0]["scope"]["qualified_name"], "Migrator.migrate")

    def test_requires_call_data_in_syntax_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            syntax_tree = Path(temp_dir) / "legacy.jsonl"
            syntax_tree.write_text(
                json.dumps({"path": "Index.ets", "tree": {"type": "file"}}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "regenerate"):
                detect_android_calls(syntax_tree)


if __name__ == "__main__":
    unittest.main()
