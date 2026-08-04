from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from arkts_syntax_tree.feature_instance import (
    create_feature_instance,
    find_feature_candidates,
    verify_feature_instance,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_PROJECT = ROOT / "test_project" / "Wechat_HarmonyOS"


class FeatureInstanceTest(unittest.TestCase):
    def test_finds_interface_with_multiple_files_in_test_project(self) -> None:
        candidates = find_feature_candidates(TEST_PROJECT, include_structural_usage=True)

        self.assertTrue(candidates)
        candidate = candidates[0]
        self.assertEqual(candidate.abstract_node.name, "ChatContentItemData")
        self.assertGreaterEqual(candidate.implementation_file_count, 2)
        self.assertIn(
            "entry/src/main/ets/component/ListChatContentLeftItem.ets",
            {item.path for item in candidate.implementation_files},
        )
        self.assertIn(
            "entry/src/main/ets/component/ListChatContentRightItem.ets",
            {item.path for item in candidate.implementation_files},
        )

    def test_creates_and_verifies_feature_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = self._make_explicit_repo(Path(temporary_directory))
            metadata = create_feature_instance(
                repo,
                output_dir=temporary_directory,
            )
            instance_dir = Path(temporary_directory) / metadata["instance_id"]
            masked_path = metadata["mask"]["path"]
            working_repo = Path(temporary_directory) / "working-repo"
            shutil.copytree(repo, working_repo)
            masked_file = working_repo / masked_path

            self.assertFalse((instance_dir / "repo").exists())
            self.assertFalse((instance_dir / "task.md").exists())
            self.assertTrue((instance_dir / "mask.patch").is_file())
            self.assertTrue((instance_dir / "gold.patch").is_file())
            self.assertTrue((instance_dir / "syntax_tree.jsonl").is_file())
            persisted = json.loads((instance_dir / "instance.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], 2)
            self.assertEqual(persisted["task_type"], "feature_implementation")
            self.assertEqual(persisted["gold_label"]["path"], "gold.patch")
            instance_uuid = metadata["instance_id"].removeprefix(f"{repo.name}-")
            self.assertEqual(str(uuid.UUID(instance_uuid)), instance_uuid)

            self._apply_patch(working_repo, instance_dir / "mask.patch")
            self.assertTrue(masked_file.read_text(encoding="utf-8").startswith("// CODE BENCHMARK MASK"))

            baseline_verification = verify_feature_instance(instance_dir, working_repo)
            self.assertEqual(baseline_verification["passed"], False)
            self.assertEqual(baseline_verification["matches_gold"], False)

            self._apply_patch(working_repo, instance_dir / "gold.patch")
            verification = verify_feature_instance(instance_dir, working_repo)
            self.assertEqual(verification["passed"], True)
            self.assertEqual(verification["matches_gold"], True)

            gold_patch = (instance_dir / "gold.patch").read_text(encoding="utf-8")
            self.assertIn("-// CODE BENCHMARK MASK", gold_patch)
            self.assertIn("+export class One implements Renderer {}", gold_patch)

    def test_equivalent_implementation_can_pass_without_matching_gold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = self._make_explicit_repo(Path(temporary_directory))
            metadata = create_feature_instance(
                repo,
                output_dir=temporary_directory,
            )
            instance_dir = Path(temporary_directory) / metadata["instance_id"]
            working_repo = Path(temporary_directory) / "working-repo"
            shutil.copytree(repo, working_repo)
            self._apply_patch(working_repo, instance_dir / "mask.patch")
            masked_file = working_repo / metadata["mask"]["path"]
            declaration = metadata["mask"]["declarations"][0]

            masked_file.write_text(
                "import { Renderer } from './base'\n"
                f"export class {declaration} implements Renderer {{\n"
                "  render(): void {}\n"
                "}\n",
                encoding="utf-8",
            )
            verification = verify_feature_instance(instance_dir, working_repo)

            self.assertEqual(verification["passed"], True)
            self.assertEqual(verification["matches_gold"], False)

    def test_finds_explicit_implementations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "base.ts").write_text(
                "export interface Renderer { render(): void }\n",
                encoding="utf-8",
            )
            (repo / "src" / "one.ts").write_text(
                "import { Renderer } from './base'\nexport class One implements Renderer {}\n",
                encoding="utf-8",
            )
            (repo / "src" / "two.ts").write_text(
                "import { Renderer } from './base'\nexport class Two implements Renderer {}\n",
                encoding="utf-8",
            )

            candidates = find_feature_candidates(repo)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(
                {item.path for item in candidates[0].implementation_files},
                {"src/one.ts", "src/two.ts"},
            )

    def test_resolves_aliases_and_ignores_same_name_from_other_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "base.ts").write_text(
                "export interface Renderer { render(): void }\n",
                encoding="utf-8",
            )
            (repo / "src" / "other.ts").write_text(
                "export interface Renderer { draw(): void }\n",
                encoding="utf-8",
            )
            (repo / "src" / "one.ts").write_text(
                "import { Renderer as BaseRenderer } from './base'\n"
                "export class One implements BaseRenderer {}\n",
                encoding="utf-8",
            )
            (repo / "src" / "two.ts").write_text(
                "import { Renderer } from './base'\nexport class Two implements Renderer {}\n",
                encoding="utf-8",
            )
            (repo / "src" / "wrong.ts").write_text(
                "import { Renderer } from './other'\nexport class Wrong implements Renderer {}\n",
                encoding="utf-8",
            )

            candidates = find_feature_candidates(repo)
            candidate = next(
                item for item in candidates if item.abstract_node.path == "src/base.ts"
            )

            self.assertEqual(
                {item.path for item in candidate.implementation_files},
                {"src/one.ts", "src/two.ts"},
            )

    def test_structural_usage_ignores_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "base.ts").write_text(
                "export interface Renderer { render(): void }\n",
                encoding="utf-8",
            )
            (repo / "src" / "comment.ts").write_text(
                "import { Renderer } from './base'\n// Renderer is not used here\n",
                encoding="utf-8",
            )

            self.assertEqual(find_feature_candidates(repo, min_implementation_files=1), [])

    def test_structural_usage_requires_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "base.ts").write_text(
                "export interface Renderer { render(): void }\n",
                encoding="utf-8",
            )
            (repo / "src" / "one.ts").write_text(
                "import { Renderer } from './base'\n"
                "export class One { data?: Renderer }\n",
                encoding="utf-8",
            )

            self.assertEqual(find_feature_candidates(repo, min_implementation_files=1), [])
            self.assertEqual(
                len(
                    find_feature_candidates(
                        repo,
                        min_implementation_files=1,
                        include_structural_usage=True,
                    )
                ),
                1,
            )

    def test_finds_non_abstract_base_class_with_two_derived_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "base.ts").write_text(
                "export class BaseRenderer {}\n",
                encoding="utf-8",
            )
            (repo / "src" / "one.ts").write_text(
                "import { BaseRenderer } from './base'\nexport class One extends BaseRenderer {}\n",
                encoding="utf-8",
            )
            (repo / "src" / "two.ts").write_text(
                "import { BaseRenderer } from './base'\nexport class Two extends BaseRenderer {}\n",
                encoding="utf-8",
            )

            candidates = find_feature_candidates(repo)

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].abstract_node.name, "BaseRenderer")

    def test_verification_reports_abstract_node_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = self._make_explicit_repo(Path(temporary_directory))
            metadata = create_feature_instance(
                repo,
                output_dir=temporary_directory,
            )
            instance_dir = Path(temporary_directory) / metadata["instance_id"]
            working_repo = Path(temporary_directory) / "working-repo"
            shutil.copytree(repo, working_repo)
            self._apply_patch(working_repo, instance_dir / "mask.patch")
            masked_file = working_repo / metadata["mask"]["path"]
            masked_file.unlink()

            verification = verify_feature_instance(instance_dir, working_repo)

            self.assertEqual(verification["checks"]["abstract_node_exists"], True)
            self.assertEqual(verification["checks"]["masked_file_exists"], False)
            self.assertEqual(verification["passed"], False)

    def test_rejects_unsafe_instance_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = self._make_explicit_repo(Path(temporary_directory))
            with self.assertRaises(ValueError):
                create_feature_instance(
                    repo,
                    output_dir=temporary_directory,
                    instance_id="../outside",
                )

    @staticmethod
    def _apply_patch(repo: Path, patch_path: Path) -> None:
        subprocess.run(
            ["patch", "-p1", "-i", str(patch_path)],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _make_explicit_repo(root: Path) -> Path:
        repo = root / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "base.ts").write_text(
            "export interface Renderer { render(): void }\n",
            encoding="utf-8",
        )
        (repo / "src" / "one.ts").write_text(
            "import { Renderer } from './base'\nexport class One implements Renderer {}\n",
            encoding="utf-8",
        )
        (repo / "src" / "two.ts").write_text(
            "import { Renderer } from './base'\nexport class Two implements Renderer {}\n",
            encoding="utf-8",
        )
        return repo


if __name__ == "__main__":
    unittest.main()
