from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from arkts_syntax_tree.oracle_generator import generate_feature_oracle


class OracleGeneratorTest(unittest.TestCase):
    def test_generates_oracle_from_exported_interface_enum_alias_and_const(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repo = root / "repo"
            instance_dir = root / "instance"
            source_dir = repo / "src"
            source_dir.mkdir(parents=True)
            instance_dir.mkdir(parents=True)

            source_path = source_dir / "sample.ets"
            source_path.write_text(
                "export interface SampleData {\n"
                "  name: string\n"
                "  kind: Kind\n"
                "  defaultValue?: string | number\n"
                "}\n"
                "export enum Kind {\n"
                "  A,\n"
                "  B,\n"
                "}\n"
                "export type SampleKey = 'LEFT' | 'RIGHT'\n"
                "export const SampleMap: Record<SampleKey, Kind> = {\n"
                "  'LEFT': Kind.A,\n"
                "  'RIGHT': Kind.B,\n"
                "}\n",
                encoding="utf-8",
            )
            (instance_dir / "instance.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "task_type": "feature_implementation",
                        "instance_id": "repo-1234",
                        "target": {
                            "abstract_node": {
                                "path": "src/sample.ets",
                                "node_type": "interface",
                                "name": "SampleData",
                                "start_line": 1,
                                "end_line": 4,
                                "signature": "export interface SampleData {",
                                "modifiers": ["export"],
                            }
                        },
                        "mask": {"path": "src/sample.ets"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            artifact = generate_feature_oracle(instance_dir, repo)
            plan = json.loads(artifact.plan_path.read_text(encoding="utf-8"))
            test_text = artifact.test_path.read_text(encoding="utf-8")

            self.assertEqual(plan["interface_name"], "SampleData")
            self.assertEqual(plan["fields"][0]["sample_value"], "'sample'")
            self.assertEqual(plan["type_aliases"][0]["sample_value"], "'LEFT'")
            self.assertIn("expect(sample.name).assertEqual('sample')", test_text)
            self.assertIn("expect(Kind.A).assertEqual(0)", test_text)
            self.assertIn("expect(sample).assertEqual('LEFT')", test_text)
            self.assertIn("expect(SampleMap.LEFT).assertEqual(Kind.A)", test_text)

    def test_generates_oracle_from_masked_class_extending_base_class(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repo = root / "repo"
            instance_dir = root / "instance"
            source_dir = repo / "src"
            source_dir.mkdir(parents=True)
            instance_dir.mkdir(parents=True)

            (source_dir / "base.ets").write_text(
                "export default class BaseDataSource<T> implements IDataSource {\n"
                "  public totalCount(): number { return 0 }\n"
                "}\n",
                encoding="utf-8",
            )
            (source_dir / "child.ets").write_text(
                "import BaseDataSource from './base'\n"
                "export default class ChildDataSource extends BaseDataSource<string> {\n"
                "  public totalCount(): number { return 0 }\n"
                "  public getData(index: number): string { return '' }\n"
                "  public getListData(): string[] { return [] }\n"
                "  public addData(index: number, data: string): void {}\n"
                "  public pushData(data: string): void {}\n"
                "  public pushAllData(data: string[]): void {}\n"
                "  public reloadNewData(data: string[]): void {}\n"
                "}\n",
                encoding="utf-8",
            )
            (instance_dir / "instance.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "task_type": "feature_implementation",
                        "instance_id": "repo-class-1234",
                        "target": {
                            "abstract_node": {
                                "path": "src/base.ets",
                                "node_type": "class",
                                "name": "BaseDataSource",
                                "start_line": 1,
                                "end_line": 3,
                                "signature": "export default class BaseDataSource<T> {",
                                "modifiers": ["export", "default"],
                            }
                        },
                        "mask": {
                            "path": "src/child.ets",
                            "relation": "explicit_inheritance",
                            "declarations": ["ChildDataSource"],
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            artifact = generate_feature_oracle(instance_dir, repo)
            plan = json.loads(artifact.plan_path.read_text(encoding="utf-8"))
            test_text = artifact.test_path.read_text(encoding="utf-8")

            self.assertEqual(plan["class_name"], "ChildDataSource")
            self.assertEqual(plan["base_class_name"], "BaseDataSource")
            self.assertEqual(plan["extends_text"], "BaseDataSource<string>")
            self.assertIn("getData", plan["methods"])
            self.assertIn("import BaseDataSource from", test_text)
            self.assertIn("instance instanceof BaseDataSource", test_text)
            self.assertIn("expect(typeof instance.getData).assertEqual('function')", test_text)


if __name__ == "__main__":
    unittest.main()
