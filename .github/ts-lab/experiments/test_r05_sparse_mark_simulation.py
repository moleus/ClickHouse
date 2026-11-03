#!/usr/bin/env python3
import unittest
from r05_sparse_mark_simulation import fraction_with_at_least_one_match, touched_marks, simulate


class TestR05(unittest.TestCase):
    def test_probability_edges(self):
        self.assertEqual(fraction_with_at_least_one_match(256, 0), 0)
        self.assertEqual(fraction_with_at_least_one_match(256, 1), 1)
        self.assertAlmostEqual(fraction_with_at_least_one_match(1, .3), .3)
        with self.assertRaises(ValueError):
            fraction_with_at_least_one_match(0, .1)

    def test_false_positive_envelope(self):
        # Candidate 25 does not occur in part, but lies in the first mark range.
        part = [10, 20, 30, 40, 50, 60]
        self.assertEqual(touched_marks(part, [25], 2), 1)
        self.assertEqual(touched_marks(part, [25, 55], 2), 2)
        self.assertEqual(touched_marks(part, [], 2), 0)

    def test_reproducibility_and_exact_marks(self):
        a = simulate(days=3, ids_per_day=1000, granularity=25, candidates=100, seed=7)
        b = simulate(days=3, ids_per_day=1000, granularity=25, candidates=100, seed=7)
        self.assertEqual(a, b)
        self.assertEqual(sum(x['exact_ids'] for x in a['per_day']), 100)
        self.assertEqual(a['per_day'][-1]['exact_ids'], 100)
        self.assertEqual(a['per_day'][0]['exact_ids'], 0)
        self.assertTrue(all(x['envelope_candidate_marks'] >= x['exact_positive_marks'] for x in a['per_day']))

if __name__ == '__main__':
    unittest.main()
