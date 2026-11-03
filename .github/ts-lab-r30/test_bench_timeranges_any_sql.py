#!/usr/bin/env python3
import argparse
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from bench_timeranges_any_sql import rewritten, write_fixture, explicit_id_alias


class AnyMembershipTests(unittest.TestCase):
    def setUp(self):
        self.generated = ("SELECT timeSeriesStoreTags(id,tags) FROM tags"
                          " ALL LEFT JOIN (SELECT id,1 AS matched FROM timeranges)"
                          " AS time_ranges_subquery USING(id) WHERE matched=1")

    def test_generated_id_selector_has_no_alias_but_diagnostic_must(self):
        source = ("SELECT timeSeriesStoreTags(id, tags, '__name__', metric_name)"
                  " FROM tags ALL LEFT JOIN (SELECT id FROM r) AS time_ranges_subquery USING (id)")
        named=explicit_id_alias(source)
        self.assertIn("metric_name) AS r30_id FROM tags",named)
        self.assertEqual(named.count("ALL LEFT JOIN"),1)
        with self.assertRaisesRegex(ValueError,"already aliased"):
            explicit_id_alias(named)

    def test_exact_one_join_rewrite(self):
        out=rewritten(self.generated)
        self.assertEqual(out.count(" ANY LEFT JOIN "), 1)
        self.assertNotIn(" ALL LEFT JOIN ", out)
        self.assertIn("timeSeriesStoreTags(id,tags)", out)
        self.assertIn("WHERE matched=1", out)

    def test_missing_metadata_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing"):
            rewritten("SELECT id FROM x ALL LEFT JOIN y USING(id)")

    def test_duplicate_joins_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            rewritten(self.generated+" ALL LEFT JOIN unrelated")

    def test_stable_fixture_and_cohort_separation(self):
        args=argparse.Namespace(series=128,updates=3)
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/"data.jsonl"
            a=write_fixture(args,path)
            b=write_fixture(args,path)
            self.assertEqual(a,b)
            data=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(data),128*3+2*(128//8))
            # Identical tags across repeated insert batches => identical TimeSeries ID.
            self.assertEqual(data[0]["tags"],data[128]["tags"])
            self.assertEqual(data[128]["tags"],data[256]["tags"])
            self.assertNotEqual(data[0]["tags"]["pod"],data[384]["tags"]["pod"])
            self.assertNotEqual(data[384]["tags"]["pod"],data[400]["tags"]["pod"])

    def test_single_join_without_space_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            rewritten(self.generated.replace(" ALL LEFT JOIN "," LEFT ALL JOIN "))


if __name__=="__main__":
    unittest.main()
