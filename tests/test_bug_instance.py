from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arkts_syntax_tree import (
    DEFAULT_MUTATION_OPERATORS,
    create_bug_instance,
    enumerate_mutations,
    find_bug_candidates,
    parse_repository,
    select_candidate_mutation,
    write_syntax_tree_outputs,
)
from arkts_syntax_tree.bug_instance import FunctionInfo


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
        self.assertIn("logical_replacement", {item.operator_id for item in candidate.mutations})
        self.assertGreater(candidate.impact_score, 0)

    def test_creates_mutated_snapshot_and_forward_repair_patch(self) -> None:
        output_dir = self.root / "instances"
        instance = create_bug_instance(
            self.repo,
            self.syntax_tree,
            output_dir=output_dir,
            instance_id="sample-instance",
            mutation_operator="logical_replacement",
            selection_seed=0,
        )

        instance_dir = output_dir / "sample-instance"
        snapshot_repo = instance_dir / "repo"
        original_source = (self.repo / "src/Producer.ets").read_text(encoding="utf-8")
        mutated_source = (snapshot_repo / "src/Producer.ets").read_text(encoding="utf-8")
        mutated_bytes = (snapshot_repo / "src/Producer.ets").read_bytes()
        fix_patch = (instance_dir / "fix.patch").read_text(encoding="utf-8")

        self.assertIn("return ok;", original_source)
        self.assertNotIn("return false;", original_source)
        self.assertIn("first || second && third", mutated_source)
        self.assertIn("return ok;", mutated_source)
        self.assertIn(b"first || second && third\r\n", mutated_bytes)
        self.assertEqual(mutated_bytes.count(b"\n"), mutated_bytes.count(b"\r\n"))
        self.assertFalse((snapshot_repo / ".git").exists())
        self.assertIn("-    const ok = first || second && third", fix_patch)
        self.assertIn("+    const ok = first && second && third", fix_patch)
        self.assertIn("ConsumerOne.run", instance["description"])
        self.assertIn("ConsumerTwo.run", instance["description"])
        self.assertEqual(instance["target"]["downstream_function_count"], 2)
        self.assertEqual(instance["target"]["mutation"]["operator_id"], "logical_replacement")
        self.assertNotIn("first && second", instance["description"])

    def test_enumerates_all_six_mutation_operators(self) -> None:
        source = """\
function mutateAll(limit: number, enabled: boolean): void {
  if (enabled) {
    let index = 0
    while (index < limit && enabled) {
      index += 1
      save(index)
    }
  }
}
"""
        function = FunctionInfo(
            path="src/All.ets",
            node_type="function",
            name="mutateAll",
            qualified_name="mutateAll",
            owner_name=None,
            owner_modifiers=(),
            start_line=1,
            end_line=9,
            signature="function mutateAll(limit: number, enabled: boolean): void {",
            modifiers=(),
        )

        mutations = enumerate_mutations(function, source)
        operators = {mutation.operator_id for mutation in mutations}

        self.assertEqual(operators, set(DEFAULT_MUTATION_OPERATORS))
        self.assertTrue(any(item.mutated_text == "!(enabled)" for item in mutations))
        self.assertTrue(any(item.original_text == "<" and item.mutated_text == "<=" for item in mutations))
        self.assertTrue(any(item.original_text == "&&" and item.mutated_text == "||" for item in mutations))
        self.assertTrue(any(item.original_text == "0" and item.mutated_text == "1" for item in mutations))
        self.assertTrue(any(item.original_text == "+=" and item.mutated_text == "-=" for item in mutations))
        self.assertTrue(any(item.operator_id == "call_deletion" and item.mutated_line == "\n" for item in mutations))

    def test_selection_seed_stratifies_operators_deterministically(self) -> None:
        candidates = find_bug_candidates(self.repo, self.syntax_tree)
        available = [
            operator
            for operator in DEFAULT_MUTATION_OPERATORS
            if any(operator in {m.operator_id for m in candidate.mutations} for candidate in candidates)
        ]

        selected = [select_candidate_mutation(candidates, seed=index)[1].operator_id for index in range(len(available))]

        self.assertEqual(selected, available)

    def test_mutations_ignore_comments_strings_and_logging_calls(self) -> None:
        source = """\
function safeScan(): void {
  // if (hidden && count < 1) { save() }
  const text = 'value && other < 1'
  console.info(text)
  logger.save(text)
  refresh()
}
"""
        function = FunctionInfo(
            path="src/Safe.ets",
            node_type="function",
            name="safeScan",
            qualified_name="safeScan",
            owner_name=None,
            owner_modifiers=(),
            start_line=1,
            end_line=7,
            signature="function safeScan(): void {",
            modifiers=(),
        )

        mutations = enumerate_mutations(function, source)

        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0].operator_id, "call_deletion")
        self.assertEqual(mutations[0].original_text, "refresh()")

    def test_unknown_operator_is_rejected(self) -> None:
        function = FunctionInfo(
            path="src/Invalid.ets",
            node_type="function",
            name="invalid",
            qualified_name="invalid",
            owner_name=None,
            owner_modifiers=(),
            start_line=1,
            end_line=1,
            signature="function invalid(): void {}",
            modifiers=(),
        )

        with self.assertRaisesRegex(ValueError, "unknown mutation operator"):
            enumerate_mutations(function, "function invalid(): void {}\n", operators=["unknown"])


if __name__ == "__main__":
    unittest.main()
