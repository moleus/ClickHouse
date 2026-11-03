#!/usr/bin/env python3
import unittest
from r15_partition_oracle import simulate, occupancy


class R15Tests(unittest.TestCase):
    def test_single_day_granule_is_not_fast(self):
        x = simulate(days=7, series_per_day=100, granularity=1, windows=(1, 7))
        self.assertEqual(x['results'][0]['monthly_positive_marks'], 100)
        self.assertEqual(x['results'][0]['daily_partition_marks'], 100)
        self.assertEqual(x['results'][0]['monthly_over_daily_marks_ratio'], 1)

    def test_all_days_cannot_prune_monthly(self):
        x = simulate(days=7, series_per_day=1000, granularity=256, windows=(1, 3, 7))
        self.assertEqual(x['results'][-1]['monthly_positive_marks'], x['monthly_total_marks'])
        self.assertEqual(x['results'][-1]['daily_partition_marks'], x['daily_total_marks'])
        self.assertGreaterEqual(x['results'][0]['monthly_positive_marks'],
                                x['results'][0]['daily_partition_marks'])

    def test_reproducibility(self):
        a = simulate(days=14, series_per_day=1000, granularity=256, windows=(1, 3, 7, 14))
        b = simulate(days=14, series_per_day=1000, granularity=256, windows=(1, 3, 7, 14))
        self.assertEqual(a, b)

    def test_occupancy_edges(self):
        self.assertEqual(occupancy(14, 1, 1), 1/14)
        self.assertEqual(occupancy(14, 256, 14), 1.0)
        with self.assertRaises(ValueError):
            occupancy(14, 256, 0)
        self.assertGreater(occupancy(14, 256, 1), 0.999999)


if __name__ == '__main__':
    unittest.main()
