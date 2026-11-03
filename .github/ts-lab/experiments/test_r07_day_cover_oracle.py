#!/usr/bin/env python3
import random
import math
import unittest
from r07_day_cover_oracle import (
    DAY, Series, Unsupported, sampled_days, build_index, base_query,
    query_cover, approximate_row_ceiling, audit_r01_400_slot_fixture
)

def series(series_id,metric,labels,samples):
    return Series(series_id,metric,tuple(sorted(labels.items())),tuple(samples))

class R07DayCoverTests(unittest.TestCase):
    def test_r01_aligned_day_cover_exact_count(self):
        a=audit_r01_400_slot_fixture(False)
        self.assertEqual(a["series"],28896)
        self.assertEqual(a["copies"],28896)
        self.assertEqual(a["copies_per_series"],1.)
        self.assertEqual(a["metadata_entries_by_day"],{0:9632,1:9632,2:9632})

    def test_r01_staggered_day_cover_exact_count(self):
        a=audit_r01_400_slot_fixture(True)
        self.assertEqual(a["series"],28896)
        self.assertEqual(a["copies"],32424)
        self.assertEqual(a["copy_count_histogram"],{1:25368,2:3528})
        self.assertEqual(a["metadata_entries_by_day"],{0:9608,1:10808,2:10808,3:1200})
        self.assertAlmostEqual(a["copies_per_series"],1.122093023255814)

    def test_uniform_3h_day_copy_ratio(self):
        result=sum(sampled_days(minute*60,3*3600,60)
                   for minute in range(1440))/1440
        # Real samples start at start+60s, not at series creation.
        # The 23:59 start has every sample AFTER midnight; 179/1440 cross.
        self.assertAlmostEqual(result,1+(3*60-1)/1440)

    def test_cross_midnight_id_dedup_preserves_avg(self):
        items=[
            series("full-128-bit-A","m",{"stage":"compile","job":"alpha"},
                   [(DAY-60,10.),(DAY+60,20.)]),
            series("full-128-bit-B","m",{"stage":"compile","job":"beta"},
                   [(DAY+60,50.)])]
        index=build_index(items,max_copies_per_series=2)
        a=base_query(items,DAY+120,300,"m")
        b=query_cover(index,items,DAY+120,300,"m")
        self.assertEqual(a["compile"],32.5)
        self.assertEqual(a,b["output"])
        self.assertEqual(b["duplicate_day_entries_removed"],1)
        # A naive join of covering rows counts A twice. It is wrong.
        self.assertNotAlmostEqual((15+15+50)/3,a["compile"])

    def test_left_open_boundary_must_not_count_previous(self):
        items=[series("id","m",{"stage":"s"},
               [(DAY-300,3.),(DAY-60,7.)])]
        idx=build_index(items)
        self.assertEqual(base_query(items,DAY,300,"m"),{"s":7.})
        self.assertEqual(query_cover(idx,items,DAY,300,"m")["output"],{"s":7.})

    def test_source_revision_fail_closed_after_late_write(self):
        before=[series("id","m",{"stage":"s"},[(DAY+30,5.)])]
        idx=build_index(before)
        after=[series("id","m",{"stage":"s"},[(DAY+30,5.),(DAY-30,9.)])]
        with self.assertRaises(Unsupported):
            query_cover(idx,after,DAY+40,100,"m")
        assert base_query(after,DAY+40,100,"m")["s"]==7.0

    def test_unknown_labels_fall_back(self):
        items=[series("id","m",{"stage":"s"},[(10,2.)])]
        idx=build_index(items)
        with self.assertRaises(Unsupported):
            query_cover(idx,items,10,100,"m",{"build":"b"})

    def test_nonfinite_or_repeated_identity_fail_closed(self):
        bad=[series("id","m",{"stage":"s"},[(1,float("nan"))])]
        with self.assertRaises(Unsupported):build_index(bad)
        repeat=[series("id","m",{"stage":"s"},[(1,1.)]),
                series("id","m",{"stage":"s"},[(2,2.)])]
        with self.assertRaises(Unsupported):build_index(repeat)
        duplicate_ts=[series("id","m",{"stage":"s"},[(1,1.),(1,2.)])]
        with self.assertRaises(Unsupported):build_index(duplicate_ts)

    def test_long_lived_budget_rejects_30d(self):
        items=[series("stable","m",{"stage":"s"},
               [(d*DAY+60,1.0) for d in range(31)])]
        with self.assertRaises(Unsupported):
            build_index(items,max_copies_per_series=2)

    def test_conservative_day_filter_no_false_negatives(self):
        items=[series("a","m",{"stage":"a"},[(DAY+300,6.)]),
               series("b","m",{"stage":"b"},[(DAY*4+300,1.)])]
        idx=build_index(items)
        self.assertEqual(query_cover(idx,items,DAY+300,300,"m")["output"],{"a":6.})
        self.assertEqual(query_cover(idx,items,DAY*5,300,"m")["output"],{})

    def test_randomized_mixed_cadences_and_literal_matchers(self):
        rng=random.Random(7007)
        items=[]
        for n in range(72):
            start=rng.randrange(0,3*DAY)
            interval=rng.choice((15,60,300))
            duration=rng.choice((3600,3*3600,6*3600))
            points=[(start+i*interval,
                     None if i%67==0 else float((n*7+i)%101)/10)
                    for i in range(1,duration//interval+1)]
            items.append(series(f"128bit-id-{n:06d}","m",
                {"stage":f"s{n%5}","job":f"j{n%3}","project":f"p{n%4}"},
                points))
        idx=build_index(items,max_copies_per_series=2)
        self.assertLessEqual(idx["amplification"],2.)
        for _ in range(28):
            end=rng.randrange(DAY//2,4*DAY)
            horizon=rng.choice((600,3600,6*3600,DAY))
            filt=rng.choice(({},{"job":"j1"},{"project":"p2"},{"stage":"s1"}))
            expected=base_query(items,end,horizon,"m",filt)
            observed=query_cover(idx,items,end,horizon,"m",filt)
            self.assertEqual(set(expected),set(observed["output"]))
            for stage in expected:
                self.assertTrue(math.isclose(expected[stage],observed["output"][stage],
                                             rel_tol=1e-12,abs_tol=1e-12))

    def test_conditional_cost_ratio_not_native_speed(self):
        m=approximate_row_ceiling(1_330_000,14,3,2)
        self.assertEqual(m["metadata_copies_per_id"],1.125)
        self.assertAlmostEqual(m["metadata_rows_ratio"],2*14/(2*1.125))
        self.assertTrue(m["does_not_include_samples_or_write_cpu"])

if __name__=="__main__":
    unittest.main()
