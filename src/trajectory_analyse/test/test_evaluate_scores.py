import csv
import tempfile
import unittest
from pathlib import Path

import evaluate_scores as ev


HEADER = ev.CSV_FIELDS


def _write_csv(path: Path, instances: list[tuple[str, str, str, float, int]]) -> None:
    """instances: list of (instance, submission, model, total_score, resolved[0/1])."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for inst, sub, model, total, resolved in instances:
            # distribute dims to sum to `total`: put it all in C, rest 0
            w.writerow([inst, sub, model, 0, 0, total, 0, 0, 0,
                        total, total, "medium", "", "True" if resolved else "False"])


class EvaluateTest(unittest.TestCase):
    def _perfect_data(self):
        rows = []
        for i in range(6):
            rows.append((f"inst{i}", "m_true", "gpt", 80.0, 1))
            rows.append((f"inst{i}", "m_false", "gpt", 40.0, 0))
        return rows

    def test_perfect_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scored.csv"
            _write_csv(inp, self._perfect_data())
            results, _rows = ev.run(inp, n_boot=50)
            self.assertEqual(results["n_pairs"], 12)
            self.assertGreater(results["global"]["auc_roc"], 0.99)
            self.assertAlmostEqual(results["within_instance"]["delta_mean"], 40.0)
            self.assertEqual(results["within_instance"]["sign_test"]["pos"], 6)
            self.assertLess(results["within_instance"]["sign_test"]["p"], 0.05)

    def test_null_signal(self):
        rows = []
        for i in range(6):
            # equal scores per group inside each instance -> delta 0, AUC ~0.5
            rows.append((f"inst{i}", "mt", "gpt", 50.0, 1))
            rows.append((f"inst{i}", "mf", "gpt", 50.0, 0))
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scored.csv"
            _write_csv(inp, rows)
            results, _rows = ev.run(inp, n_boot=50)
            self.assertAlmostEqual(results["global"]["auc_roc"], 0.5)
            self.assertAlmostEqual(results["within_instance"]["delta_mean"], 0.0)

    def test_decomposition_records_all_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scored.csv"
            _write_csv(inp, self._perfect_data())
            results, _rows = ev.run(inp, n_boot=30)
            dec = results["decomposition"]
            for k in ("process_only_AB_E", "outcome_CDF", "full_total"):
                self.assertIn(k, dec)
            # here all signal lives in C, so outcome(C+D+F) AUC = full AUC
            self.assertGreater(dec["outcome_CDF"]["auc_roc"], 0.99)
            # process-only (A+B+E) carries no signal here
            self.assertAlmostEqual(dec["process_only_AB_E"]["auc_roc"], 0.5)

    def test_reliability_identical_judge(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "scored.csv"
            extra = Path(tmp) / "extra.csv"
            _write_csv(inp, self._perfect_data())
            _write_csv(extra, self._perfect_data())
            results, _rows = ev.run(inp, n_boot=30, extra_path=extra)
            self.assertGreater(results["reliability"]["n_overlapping"], 0)
            self.assertAlmostEqual(results["reliability"]["total_icc"], 1.0, places=3)
            # per-dim ICC must be computed (not 0/None) when scores overlap for that dim
            pdim = results["reliability"]["per_dimension"]
            self.assertGreater(pdim["C"]["icc"], 0.99)  # C carries the signal here
            self.assertGreater(pdim["C"]["pearson"], 0.99)


if __name__ == "__main__":
    unittest.main()
