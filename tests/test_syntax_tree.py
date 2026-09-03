from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arkts_syntax_tree import (
    build_repository_summary,
    iter_source_files,
    parse_repository,
    parse_source,
    write_syntax_tree_outputs,
)


class SyntaxTreeParserTest(unittest.TestCase):
    def test_parse_repository_rejects_missing_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing-repository"
            with self.assertRaisesRegex(FileNotFoundError, "repository path does not exist"):
                parse_repository(missing)

    def test_parse_repository_rejects_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_file = Path(temp_dir) / "Index.ets"
            source_file.write_text("struct Index {}\n", encoding="utf-8")
            with self.assertRaisesRegex(NotADirectoryError, "repository path is not a directory"):
                parse_repository(source_file)

    def test_source_file_order_is_platform_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative_path in ["pages/chat.ets", "pages/Index.ets", "pages/Hardware.ets"]:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            paths = iter_source_files(root)

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in paths],
                ["pages/Hardware.ets", "pages/Index.ets", "pages/chat.ets"],
            )

    def test_output_reference_keeps_summary_portable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "tree.jsonl"
            result = write_syntax_tree_outputs(
                [],
                output_path=output,
                output_reference="syntax_trees/tree.jsonl",
            )

            self.assertEqual(result["output"], "syntax_trees/tree.jsonl")

    def test_parse_arkts_struct_methods_properties_and_ui_nodes(self) -> None:
        source = """
import router from '@ohos.router'

@Entry
@Component
struct Index {
  @State private currentIndex: number = 0

  build() {
    Column() {
      Text("hello { ignored }")
        .onClick(() => {
          router.pushUrl({ url: 'pages/Home' })
        })
    }
  }
}
"""

        parsed = parse_source(source, path="entry/src/main/ets/pages/Index.ets")
        root = parsed.tree
        struct = root.children[0]

        self.assertEqual(parsed.language, "ArkTS")
        self.assertEqual(parsed.imports[0]["source"], "@ohos.router")
        self.assertIn("router.pushUrl", [call["callee"] for call in parsed.calls])
        self.assertEqual(parsed.metrics["calls"], len(parsed.calls))
        self.assertEqual(build_repository_summary([parsed])["calls"], len(parsed.calls))
        self.assertEqual(struct.type, "struct")
        self.assertEqual(struct.name, "Index")
        self.assertEqual(struct.decorators, ["@Entry", "@Component"])
        self.assertEqual([child.type for child in struct.children[:2]], ["property", "method"])

        build_method = struct.children[1]
        self.assertEqual(build_method.name, "build")
        self.assertEqual(build_method.children[0].type, "ui_component")
        self.assertEqual(build_method.children[0].name, "Column")

    def test_ignores_braces_in_comments_and_strings(self) -> None:
        source = """
// function fake() {
export class Toast {
  show() {
    const text = "{";
  }
}
"""

        parsed = parse_source(source, path="entry/src/main/ets/utils/Toast.ets")
        class_node = parsed.tree.children[0]

        self.assertEqual(class_node.type, "class")
        self.assertEqual(class_node.end_line, 7)
        self.assertEqual(class_node.children[0].name, "show")


if __name__ == "__main__":
    unittest.main()
