from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arkts_syntax_tree import build_repository_summary, parse_source


class SyntaxTreeParserTest(unittest.TestCase):
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
