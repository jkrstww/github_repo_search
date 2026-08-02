from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arkts_syntax_tree import (
    create_bug_instance,
    find_bug_candidates,
    parse_repository,
    write_syntax_tree_outputs,
)


PRODUCER_SOURCE = """\
export class Producer {
  static helperA(): boolean { return true }
  static helperB(): boolean { return true }
  static helperC(): boolean { return true }

  static async compute(): Promise<boolean> {
    const first = Producer.helperA()
    const second = Producer.helperB()
    const third = Producer.helperC()
    const ok = first && second && third
    return ok;
  }
}
"""

CONSUMER_ONE_SOURCE = """\
import { Producer } from './Producer'

export class ConsumerOne {
  static async run(): Promise<boolean> {
    const accepted = await Producer.compute()
    if (!accepted) {
      return false
    }
    return true
  }
}
"""

CONSUMER_TWO_SOURCE = """\
import { Producer } from './Producer'

export class ConsumerTwo {
  static async run(): Promise<boolean> {
    if (await Producer.compute()) {
      return true
    }
    return false
  }
}
"""

UNUSED_CONSUMER_SOURCE = """\
import { Producer } from './Producer'

export class UnusedConsumer {
  static async run(): Promise<void> {
    const ignored = await Producer.compute()
  }
}
"""


class BugInstanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repo = self.root / "sample_repo"
        source_dir = self.repo / "src"
        source_dir.mkdir(parents=True)
        (source_dir / "Producer.ets").write_bytes(PRODUCER_SOURCE.replace("\n", "\r\n").encode("utf-8"))
        (source_dir / "ConsumerOne.ets").write_text(CONSUMER_ONE_SOURCE, encoding="utf-8")
        (source_dir / "ConsumerTwo.ets").write_text(CONSUMER_TWO_SOURCE, encoding="utf-8")
        (source_dir / "UnusedConsumer.ets").write_text(UNUSED_CONSUMER_SOURCE, encoding="utf-8")
        git_dir = self.repo / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

        self.syntax_tree = self.root / "sample_syntax_tree.jsonl"
        write_syntax_tree_outputs(
            parse_repository(self.repo),
            output_path=self.syntax_tree,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_finds_cross_file_consumers_and_ignores_unused_assignment(self) -> None:
        candidates = find_bug_candidates(self.repo, self.syntax_tree)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.function.qualified_name, "Producer.compute")
        self.assertEqual(candidate.out_degree, 3)
        self.assertEqual(
            {consumer.identity for consumer in candidate.consumers},
            {
                "src/ConsumerOne.ets::ConsumerOne.run",
                "src/ConsumerTwo.ets::ConsumerTwo.run",
            },
        )
        self.assertEqual(candidate.mutation.original_expression, "ok")
        self.assertEqual(candidate.mutation.mutated_expression, "false")
        self.assertEqual(candidate.mutation.mutated_line, "    return false;\r\n")

    def test_creates_mutated_snapshot_and_forward_repair_patch(self) -> None:
        output_dir = self.root / "instances"
        instance = create_bug_instance(
            self.repo,
            self.syntax_tree,
            output_dir=output_dir,
            instance_id="sample-instance",
        )

        instance_dir = output_dir / "sample-instance"
        snapshot_repo = instance_dir / "repo"
        original_source = (self.repo / "src/Producer.ets").read_text(encoding="utf-8")
        mutated_source = (snapshot_repo / "src/Producer.ets").read_text(encoding="utf-8")
        mutated_bytes = (snapshot_repo / "src/Producer.ets").read_bytes()
        fix_patch = (instance_dir / "fix.patch").read_text(encoding="utf-8")

        self.assertIn("return ok;", original_source)
        self.assertNotIn("return false;", original_source)
        self.assertIn("return false;", mutated_source)
        self.assertNotIn("return ok;", mutated_source)
        self.assertIn(b"return false;\r\n", mutated_bytes)
        self.assertEqual(mutated_bytes.count(b"\n"), mutated_bytes.count(b"\r\n"))
        self.assertFalse((snapshot_repo / ".git").exists())
        self.assertIn("-    return false;", fix_patch)
        self.assertIn("+    return ok;", fix_patch)
        self.assertIn("ConsumerOne.run", instance["description"])
        self.assertIn("ConsumerTwo.run", instance["description"])
        self.assertEqual(instance["target"]["downstream_function_count"], 2)


if __name__ == "__main__":
    unittest.main()
