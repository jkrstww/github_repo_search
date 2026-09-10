import math
import unittest

import scoring_stats as ss


class RankAndTiesTest(unittest.TestCase):
    def test_rankdata_average_ranks_for_ties(self):
        self.assertEqual(ss._rankdata([3, 1, 2, 1]), [4.0, 1.5, 3.0, 1.5])

    def test_rankdata_single(self):
        self.assertEqual(ss._rankdata([42.0]), [1.0])

    def test_tie_correction(self):
        # 1,1 -> tie group size 2 -> 2**3 - 2 = 6 ; 2 -> single
        self.assertEqual(ss._tie_correction(sorted([1, 1, 2])), 6.0)
        self.assertEqual(ss._tie_correction(sorted([1, 2, 3])), 0.0)


class AucTest(unittest.TestCase):
    def test_perfect_separation(self):
        y = [0, 0, 0, 1, 1, 1]
        s = [1, 2, 3, 4, 5, 6]
        self.assertAlmostEqual(ss.auc_roc(y, s), 1.0)

    def test_reversed_separation(self):
        y = [0, 0, 0, 1, 1, 1]
        s = [6, 5, 4, 3, 2, 1]
        self.assertAlmostEqual(ss.auc_roc(y, s), 0.0)

    def test_all_ties_score_half(self):
        y = [0, 0, 1, 1]
        s = [5, 5, 5, 5]
        self.assertAlmostEqual(ss.auc_roc(y, s), 0.5)

    def test_pr_auc_perfect(self):
        y = [0, 1]
        s = [0.1, 0.9]
        self.assertAlmostEqual(ss.pr_auc(y, s), 1.0)


class PointCorrelationTest(unittest.TestCase):
    def test_perfect_monotone(self):
        # zero within-group variance in y -> r == 1.0
        self.assertAlmostEqual(ss.point_correlation([0, 0, 1, 1], [2, 2, 8, 8]), 1.0)

    def test_bounded_around_one_for_noisy_monotone(self):
        # monotone but within-group variance -> r in (0, 1)
        r = ss.point_correlation([0, 0, 1, 1], [1, 2, 3, 4])
        self.assertGreater(r, 0.0)
        self.assertLess(r, 1.0)

    def test_zero_variance_handles_gracefully(self):
        self.assertEqual(ss.point_correlation([0, 1], [3, 3]), 0.0)

    def test_single_group_handles_gracefully(self):
        self.assertEqual(ss.point_correlation([1, 1], [4, 5]), 0.0)


class MannWhitneyTest(unittest.TestCase):
    def test_separable(self):
        mw = ss.mann_whitney_u([5, 6, 7, 8], [1, 2, 3, 4])
        self.assertAlmostEqual(mw["U"], 16.0)
        self.assertAlmostEqual(mw["effect"], 1.0)
        self.assertLess(mw["p"], 0.05)


class SignAndWilcoxonTest(unittest.TestCase):
    def test_sign_test_sig(self):
        # 9 positive, 1 negative -> strongly nonrandom under p=0.5
        st = ss.sign_test([1, 1, 1, 1, 1, 1, 1, 1, 1, -1])
        self.assertEqual(st["pos"], 9)
        self.assertEqual(st["neg"], 1)
        self.assertLess(st["p"], 0.05)

    def test_sign_test_balanced_is_nonsig(self):
        st = ss.sign_test([1, 1, -1, -1])
        self.assertGreater(st["p"], 0.5)

    def test_wilcoxon_Wplus(self):
        # deltas 3,3,3,-1  -> abs sorted [1,3,3,3], ranks 1,3,3,3
        # W_plus = sum of ranks where delta>0 = 3+3+3 = 9
        wl = ss.wilcoxon_signed_rank([3, 3, 3, -1])
        self.assertAlmostEqual(wl["W_plus"], 9.0)
        self.assertGreaterEqual(wl["p"], 0.0)
        self.assertLessEqual(wl["p"], 1.0)


class ClusterBootstrapTest(unittest.TestCase):
    def test_ci_brackets_point(self):
        rows = []
        cids = []
        for inst in range(20):
            for resolved in (0, 1):
                rows.append((resolved, 60.0 + resolved * 30 + (inst % 3)))
                cids.append(inst)
        pt, lo, hi = ss.cluster_bootstrap_ci(
            cids, rows,
            lambda subset: ss.point_correlation([v[0] for v in subset], [v[1] for v in subset]),
            n_boot=200, seed=0,
        )
        self.assertGreater(pt, 0.0)
        self.assertLessEqual(lo, pt)


class IccTest(unittest.TestCase):
    def test_identical_judges_high(self):
        r = ss.icc_2_1([1, 2, 3, 4], [1, 2, 3, 4])
        self.assertAlmostEqual(r["icc"], 1.0)
        self.assertAlmostEqual(r["pearson"], 1.0)

    def test_anticorrelated_judges_low(self):
        r = ss.icc_2_1([1, 2, 3, 4], [4, 3, 2, 1])
        # negative reliability
        self.assertLess(r["icc"], 0)
        self.assertAlmostEqual(r["pearson"], -1.0)


if __name__ == "__main__":
    unittest.main()
