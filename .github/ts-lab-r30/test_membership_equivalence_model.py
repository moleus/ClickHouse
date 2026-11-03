#!/usr/bin/env python3
"""Exhaustive finite model of the R30 membership identity (not CH semantics)."""
import itertools
import unittest


def emitted(left_ids,right_matched_ids,allowed_metric_hash_ids,mode):
    result=[]
    for key in left_ids:
        hits=sum(i==key for i in right_matched_ids)
        multiplicity=max(1,hits) if mode=="all" else 1
        permitted=(hits>0) or (key not in allowed_metric_hash_ids)
        if permitted:
            result.extend([key]*multiplicity)
    return result


class R30SetAlgebra(unittest.TestCase):
    def test_membership_all_any_equal_with_duplicated_and_missing_ranges(self):
        universe=(1,2,3)
        for count in range(5):
            for right in itertools.product(universe,repeat=count):
                for allowed_bits in range(1<<len(universe)):
                    allowed={id for i,id in enumerate(universe) if allowed_bits>>i&1}
                    for left in ((1,2,3),(2,2,3),(1,1,3)):
                        all_rows=emitted(left,right,allowed,"all")
                        any_rows=emitted(left,right,allowed,"any")
                        self.assertEqual(set(all_rows),set(any_rows))
                        # With each right matched flag fixed to 1,
                        # ANY can suppress copies but cannot add IDs.
                        self.assertGreaterEqual(len(all_rows),len(any_rows))

    def test_reduction_when_repeats_are_present(self):
        a=emitted([1,2],[1,1,1,2],{1,2},"all")
        b=emitted([1,2],[1,1,1,2],{1,2},"any")
        self.assertEqual((len(a),len(b)),(4,2))
        self.assertEqual(set(a),set(b))

    def test_legacy_id_not_in_metric_range_survives_absent_right(self):
        for mode in ("all","any"):
            self.assertEqual(emitted([99],[],{1,2},mode),[99])


if __name__=="__main__":unittest.main()
