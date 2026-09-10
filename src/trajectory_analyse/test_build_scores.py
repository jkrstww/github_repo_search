import csv
import json
import tempfile
import unittest
from pathlib import Path

import build_scores
import score_trajectories as st


VALID_SCORE = {
    "scores": {
        "A": {"score": 10}, "B": {"score": 10}, "C": {"score": 20},
        "D": {"score": 20}, "E": {"score": 8}, "F": {"score": 8},
    },
    "raw_total_score": 76, "total_score": 76,
    "evidence_confidence": "high", "caps_or_flags": ["NO_BASELINE", "PIPE_MASKED"],
}


class BuildTest(unittest.TestCase):
    def _tree(self, root: Path, *, with_score: bool):
        inst = "django__django-123"
        (root / inst).mkdir(parents=True)
        (root / inst / "model.json").write_text(
            json.dumps({"agent": {"model_name": "gpt-5.2"}, "steps": []}), encoding="utf-8",
        )
        cmp = root / "compare.json"
        cmp.write_text(json.dumps({inst: {"model.json": True}}))
        if with_score:
            sp = st.score_path_for(root, inst, "model.json", None)
            sp.write_text(json.dumps(VALID_SCORE), encoding="utf-8")
        return cmp, inst

    def test_writes_tidy_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmp, inst = self._tree(root, with_score=True)
            out = root / "scored.csv"
            uns = root / "unstorable.jsonl"
            n, m = build_scores.build(cmp, root, out, uns, judge_id=None)
            self.assertEqual(n, 1)
            self.assertEqual(m, 0)
            with out.open(encoding="utf-8") as fh:
                row = next(csv.DictReader(fh))
            self.assertEqual(row["instance"], inst)
            self.assertEqual(row["submission"], "model")
            self.assertEqual(row["model_name"], "gpt-5.2")
            self.assertEqual(row["resolved"], "True")
            self.assertEqual(row["A"], "10")
            self.assertEqual(row["D"], "20")
            self.assertEqual(row["total_score"], "76")
            self.assertIn("NO_BASELINE", row["caps_or_flags"])

    def test_missing_score_is_unscorable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmp, inst = self._tree(root, with_score=False)
            out = root / "scored.csv"
            uns = root / "unstorable.jsonl"
            n, m = build_scores.build(cmp, root, out, uns, judge_id=None)
            self.assertEqual(n, 0)
            self.assertEqual(m, 1)
            line = uns.read_text(encoding="utf-8").strip()
            self.assertIn("missing_score", line)

    def test_placeholder_is_unscorable_not_scored(self):
        """A parse-failure placeholder (6x zero, _meta.valid=False) must be excluded
        from scored CSV even though it is structurally valid (0 == sum)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmp, inst = self._tree(root, with_score=True)
            sp = st.score_path_for(root, inst, "model.json", None)
            obj = json.loads(sp.read_text(encoding="utf-8"))
            for d in "ABCDEF":
                obj["scores"][d]["score"] = 0
            obj["total_score"] = 0
            obj["raw_total_score"] = 0
            obj["_meta"] = {"valid": False, "reason": "HTTP 429"}
            sp.write_text(json.dumps(obj), encoding="utf-8")
            out = root / "scored.csv"
            uns = root / "unstorable.jsonl"
            n, m = build_scores.build(cmp, root, out, uns, judge_id=None)
            self.assertEqual(n, 0)  # placeholder must NOT be scored
            self.assertEqual(m, 1)
            self.assertIn("placeholder", uns.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
