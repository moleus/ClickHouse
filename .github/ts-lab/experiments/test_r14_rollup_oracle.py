#!/usr/bin/env python3
import random
import unittest
from r14_rollup_oracle import (Sample, Series, Unsupported, base_query,
                                snapshot, run_snapshot, candidate_counts)


class TestR14(unittest.TestCase):
    def setUp(self):
        self.series = [
            Series("1", "compile", "alpha", "main", "a", (Sample(90, 1.), Sample(99, 3.))),
            Series("2", "compile", "alpha", "dev", "b", (Sample(96, 9.),)),
            Series("3", "compile", "beta", "main", "c", (Sample(100, 5.),)),
            Series("4", "test", "alpha", "main", "d", (Sample(99, 100.),)),
            Series("5", "test", "alpha", "dev", "e", (Sample(98, 10.), Sample(99, None))),
            Series("6", "test", "beta", "dev", "f", (Sample(60, 8.),)),
        ]

    def pair(self, *, mode, horizon, when=100, job=None, branch=None):
        snap = snapshot(self.series, [when], mode=mode, horizon=horizon, source_revision="v1")
        expected = base_query(self.series, when, mode, horizon, job=job, branch=branch)
        got = run_snapshot(snap, when, mode=mode, horizon=horizon,
                           source_revision="v1", job=job, branch=branch)
        self.assertEqual(set(expected), set(got))
        for stage in expected:
            self.assertAlmostEqual(expected[stage], got[stage], places=12)
        return got

    def test_instant_staleness_lookback_and_filter(self):
        self.assertEqual(self.pair(mode="instant", horizon=5), {"compile": (3 + 9 + 5)/3, "test": 100.0})
        self.assertEqual(self.pair(mode="instant", horizon=5, job="alpha"), {"compile": 6.0, "test": 100.0})
        self.assertEqual(self.pair(mode="instant", horizon=5, branch="dev"), {"compile": 9.0})

    def test_range_average_is_per_series_not_per_sample(self):
        got = self.pair(mode="avg_over_time", horizon=20)
        self.assertAlmostEqual(got["compile"], (2 + 9 + 5)/3)
        self.assertNotAlmostEqual(got["compile"], (1 + 3 + 9 + 5)/4)
        self.pair(mode="avg_over_time", horizon=20, job="alpha", branch="main")

    def test_range_left_open_right_closed(self):
        self.pair(mode="avg_over_time", horizon=10)
        got = base_query(self.series, 100, "avg_over_time", 10, job="alpha", branch="main")
        self.assertEqual(got["compile"], 3.0)  # excludes sample at t=90

    def test_dirty_snapshot_falls_back(self):
        s = snapshot(self.series, [100], mode="instant", horizon=5, source_revision="v1")
        for params in ({"source_revision": "v2"}, {"when": 101},
                       {"horizon": 10}, {"build": "a"}):
            defaults = dict(when=100, mode="instant", horizon=5, source_revision="v1")
            defaults.update(params)
            with self.assertRaises(Unsupported):
                run_snapshot(s, **defaults)

    def test_unknown_labels_fallback_and_duplicate_timestamps(self):
        s = snapshot(self.series, [100], mode="instant", horizon=5, source_revision="v1")
        with self.assertRaises(Unsupported):
            run_snapshot(s, 100, mode="instant", horizon=5, source_revision="v1", build="a")
        duplicate = [Series("x", "a", "b", "c", "d", (Sample(1, 1.), Sample(1, 2.)))]
        with self.assertRaises(Unsupported):
            snapshot(duplicate, [2], mode="instant", horizon=5, source_revision="v1")

    def test_nonfinite_fallback(self):
        bad = [Series("x", "a", "b", "c", "d", (Sample(1, float("nan")),))]
        with self.assertRaises(Unsupported):
            snapshot(bad, [2], mode="instant", horizon=5, source_revision="v1")

    def test_randomized_query_snapshot_equivalence(self):
        rng = random.Random(141414)
        data = []
        for n in range(130):
            points = []
            for ts in rng.sample(range(60, 121), rng.randint(1, 5)):
                points.append(Sample(ts, None if rng.random() < .08 else float(rng.randrange(-20, 20))))
            data.append(Series(str(n), "stage" + str(n % 4), "job" + str(n % 3),
                               "branch" + str(n % 5), str(n), tuple(points)))
        for mode, horizon in (("instant", 5), ("instant", 30),
                              ("avg_over_time", 10), ("avg_over_time", 30)):
            times = [90, 100, 110, 120]
            snap = snapshot(data, times, mode=mode, horizon=horizon, source_revision="v1")
            for when in times:
                for job, branch in ((None, None), ("job0", None),
                                    (None, "branch2"), ("job1", "branch3")):
                    original = base_query(data, when, mode, horizon, job=job, branch=branch)
                    derived = run_snapshot(snap, when, mode=mode, horizon=horizon,
                                           source_revision="v1", job=job, branch=branch)
                    self.assertEqual(set(original), set(derived))
                    for stage in original:
                        self.assertAlmostEqual(original[stage], derived[stage], places=12)

    def test_duplicate_series_id_falls_back(self):
        with self.assertRaises(Unsupported):
            snapshot([self.series[0], self.series[0]], [100], mode="instant", horizon=5,
                     source_revision="v1")

    def test_cardinality_and_breakeven_bound(self):
        result = candidate_counts()
        self.assertEqual(result["input_series"], 1_330_000)
        self.assertEqual(result["max_all_branch_states"], 5_320)
        self.assertEqual(result["max_logical_shrink_if_full_groups"], 250)


if __name__ == "__main__":
    unittest.main()
