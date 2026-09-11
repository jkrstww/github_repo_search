import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import score_trajectories as st


VALID_SCORE_JSON = json.dumps({
    "scores": {
        "A": {"name": "A", "score": 10}, "B": {"name": "B", "score": 10},
        "C": {"name": "C", "score": 20}, "D": {"name": "D", "score": 20},
        "E": {"name": "E", "score": 8}, "F": {"name": "F", "score": 8},
    },
    "raw_total_score": 76,
    "total_score": 76,
    "evidence_confidence": "medium",
    "caps_or_flags": [],
})


def _pair(tmp: Path, exists: bool = True) -> dict:
    traj = tmp / "inst" / "sub.json"
    traj.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        traj.write_text('{"agent":{"model_name":"gpt"}}', encoding="utf-8")
    return {
        "instance": "inst", "submission": "sub",
        "traj_path": str(traj),
        "score_path": str(st.score_path_for(tmp, "inst", "sub.json", None)),
        "resolved": True, "exists": exists,
    }


def _args(tmp: Path, force: bool = False) -> SimpleNamespace:
    return SimpleNamespace(judge_backend="codex", judge_id=None, timeout=5,
                           codex_command="codex", force=force, trajs_root=tmp)


class PromptExtractTest(unittest.TestCase):
    def test_extract_prompt_has_placeholder(self):
        prompt = st.extract_prompt(st.DEFAULT_PROMPT_FILE)
        self.assertIn("{{TRAJECTORY_PATH}}", prompt)
        self.assertIn("严格输出格式", prompt)

    def test_prompt_template_inlined_matches_md(self):
        # guard the no-drift contract documented in score_trajectories.py
        self.assertEqual(st.PROMPT_TEMPLATE, st.extract_prompt(st.DEFAULT_PROMPT_FILE))

    def test_prompt_template_is_self_contained(self):
        # the rubric the judge receives lives inline in the module
        self.assertIsInstance(st.PROMPT_TEMPLATE, str)
        self.assertIn("{{TRAJECTORY_PATH}}", st.PROMPT_TEMPLATE)
        self.assertIn("A 任务理解", st.PROMPT_TEMPLATE)
        self.assertIn("F 收尾", st.PROMPT_TEMPLATE)
        self.assertIn('"trajectory": "{{TRAJECTORY_PATH}}"', st.PROMPT_TEMPLATE)
        self.assertGreater(len(st.PROMPT_TEMPLATE), 4000)  # sanity lower bound, not exact

    def test_main_uses_inlined_template_by_default(self):
        with patch("score_trajectories._run_codex", return_value=VALID_SCORE_JSON):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "i").mkdir()
                (root / "i" / "m.json").write_text("{}", encoding="utf-8")
                cmp = root / "compare.json"
                cmp.write_text(json.dumps({"i": {"m.json": True}}))
                # default --prompt-file == DEFAULT_PROMPT_FILE -> must NOT read the md
                with patch("score_trajectories.extract_prompt") as fake:
                    st.main(["--compare", str(cmp), "--trajs-root", str(root),
                             "--concurrency", "1", "--timeout", "5", "--force"])
                    fake.assert_not_called()


class ScorePathTest(unittest.TestCase):
    def test_default_naming(self):
        p = st.score_path_for(Path("/r"), "inst", "sub.json", None)
        self.assertEqual(p.name, "sub.score.json")

    def test_judge_id_naming(self):
        p = st.score_path_for(Path("/r"), "inst", "sub.json", "gpt5")
        self.assertEqual(p.name, "sub.score.gpt5.json")


class ValidateScoreTest(unittest.TestCase):
    def test_valid(self):
        obj = json.loads(VALID_SCORE_JSON)
        ok, reason = st.validate_score(obj)
        self.assertTrue(ok, reason)

    def test_sum_mismatch(self):
        obj = json.loads(VALID_SCORE_JSON)
        obj["total_score"] = 70  # sum is 76
        self.assertFalse(st.validate_score(obj)[0])

    def test_missing_dim(self):
        obj = json.loads(VALID_SCORE_JSON)
        del obj["scores"]["D"]
        self.assertFalse(st.validate_score(obj)[0])

    def test_nonnumeric_total(self):
        obj = json.loads(VALID_SCORE_JSON)
        obj["total_score"] = "76"
        self.assertFalse(st.validate_score(obj)[0])


DIMENSIONS_FALLBACK = ("A", "B", "C", "D", "E", "F")


class LoadExistingTest(unittest.TestCase):
    def test_real_score_with_meta_valid_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.score.json"
            obj = json.loads(VALID_SCORE_JSON)
            obj["_meta"] = {"valid": True, "parse_ok": True}
            p.write_text(json.dumps(obj), encoding="utf-8")
            self.assertIsNotNone(st._load_existing(p))

    def test_placeholder_with_meta_valid_false_is_not_returned(self):
        """A parse-failure placeholder (6x zero, valid=False) must be re-scored on resume."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.score.json"
            obj = json.loads(VALID_SCORE_JSON)
            # force into placeholder shape: all-zero scores, total 0, valid False
            for d in DIMENSIONS_FALLBACK:
                obj["scores"][d]["score"] = 0
            obj["total_score"] = 0
            obj["_meta"] = {"valid": False, "parse_ok": False, "reason": "parse_failed"}
            p.write_text(json.dumps(obj), encoding="utf-8")
            self.assertIsNone(st._load_existing(p))

    def test_file_without_meta_falls_back_to_structural(self):
        # legacy/external cached score with no _meta: use structural validity only
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.score.json"
            obj = json.loads(VALID_SCORE_JSON)  # no _meta key
            p.write_text(json.dumps(obj), encoding="utf-8")
            self.assertIsNotNone(st._load_existing(p))


class ExtractJsonTest(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(st._extract_json(VALID_SCORE_JSON)["total_score"], 76)

    def test_fenced_json(self):
        fenced = "```json\n" + VALID_SCORE_JSON + "\n```"
        self.assertEqual(st._extract_json(fenced)["total_score"], 76)

    def test_prose_then_json(self):
        s = "Here is the verdict:\n" + VALID_SCORE_JSON + "\nThanks."
        self.assertEqual(st._extract_json(s)["total_score"], 76)

    def test_garbage(self):
        self.assertIsNone(st._extract_json("not json at all"))


@patch("score_trajectories._run_codex")
class ScoreOneTest(unittest.TestCase):
    def test_scores_and_writes_file(self, run):
        run.return_value = VALID_SCORE_JSON
        with tempfile.TemporaryDirectory() as tmp:
            pair = _pair(Path(tmp))
            status = st._score_one(pair, "PROMPT {{TRAJECTORY_PATH}}", _args(Path(tmp)))
            self.assertEqual(status, "scored")
            rec = json.loads(Path(pair["score_path"]).read_text(encoding="utf-8"))
            self.assertEqual(rec["instance"], "inst")
            self.assertEqual(rec["submission"], "sub")
            self.assertTrue(rec["_meta"]["valid"])
            self.assertEqual(rec["total_score"], 76)

    def test_resume_skips_when_valid_exists(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            pair = _pair(Path(tmp))
            # pre-write a valid score file
            Path(pair["score_path"]).write_text(VALID_SCORE_JSON, encoding="utf-8")
            status = st._score_one(pair, "P", _args(Path(tmp)))
            self.assertEqual(status, "skipped")
            run.assert_not_called()

    def test_force_rescores(self, run):
        run.return_value = VALID_SCORE_JSON
        with tempfile.TemporaryDirectory() as tmp:
            pair = _pair(Path(tmp))
            Path(pair["score_path"]).write_text(VALID_SCORE_JSON, encoding="utf-8")
            status = st._score_one(pair, "P", _args(Path(tmp), force=True))
            self.assertEqual(status, "scored")
            run.assert_called()

    def test_invalid_marks_placeholder(self, run):
        run.return_value = "not parseable"
        with tempfile.TemporaryDirectory() as tmp:
            pair = _pair(Path(tmp))
            status = st._score_one(pair, "P", _args(Path(tmp)))
            self.assertEqual(status, "invalid")
            rec = json.loads(Path(pair["score_path"]).read_text(encoding="utf-8"))
            self.assertEqual(rec["total_score"], 0)
            self.assertFalse(rec["_meta"]["valid"])
            self.assertTrue(rec["_meta"]["retried"])

    def test_missing_trajectory(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            pair = _pair(Path(tmp), exists=False)
            status = st._score_one(pair, "P", _args(Path(tmp)))
            self.assertEqual(status, "missing_traj")
            run.assert_not_called()


class EnumeratePairsTest(unittest.TestCase):
    def test_join_labels_and_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "django__x").mkdir()
            (root / "django__x" / "m1.json").write_text("{}")
            cmp = root / "compare.json"
            cmp.write_text(json.dumps({"django__x": {"m1.json": True, "m2.json": False}}))
            pairs = st.enumerate_pairs(cmp, root, None)
            self.assertEqual(len(pairs), 2)
            m1 = [p for p in pairs if p["submission"] == "m1"][0]
            self.assertTrue(m1["exists"])
            self.assertEqual(m1["score_path"], str(root / "django__x" / "m1.score.json"))
            m2 = [p for p in pairs if p["submission"] == "m2"][0]
            self.assertFalse(m2["exists"])


@patch("score_trajectories._run_codex")
class DryRunAndBudgetTest(unittest.TestCase):
    def test_dry_run_does_not_call_codex(self, run):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "i").mkdir()
            (root / "i" / "m.json").write_text("{}")
            cmp = root / "compare.json"
            cmp.write_text(json.dumps({"i": {"m.json": True}}))
            rc = st.main(["--compare", str(cmp), "--trajs-root", str(root), "--dry-run"])
            self.assertEqual(rc, 0)
            run.assert_not_called()

    def test_max_pairs_caps(self, run):
        run.return_value = VALID_SCORE_JSON
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for inst in ("a", "b", "c"):
                (root / inst).mkdir()
                (root / inst / "m.json").write_text("{}")
            cmp = root / "compare.json"
            cmp.write_text(json.dumps({k: {"m.json": True} for k in ("a", "b", "c")}))
            st.main(["--compare", str(cmp), "--trajs-root", str(root), "--max-pairs", "1",
                     "--concurrency", "1"])
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
