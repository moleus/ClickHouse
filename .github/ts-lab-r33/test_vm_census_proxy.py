#!/usr/bin/env python3
"""R33 measured VMUI marginal cardinalities are identity gates, not a claim of joint label distribution."""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).parent))
from generate_vm_census_proxy import (
    COUNTS,TOTAL_SERIES,generate,validate,stage_labels,records
)


class VMCensusFixtureTests(unittest.TestCase):
    def test_exact_observed_census_is_not_38_stage_fixture(self):
        self.assertEqual(TOTAL_SERIES,53205)
        self.assertEqual(COUNTS["stage"],1448)
        self.assertEqual(COUNTS["app"],741)
        self.assertEqual(COUNTS["build"],623)
        self.assertEqual(COUNTS["app_name"],430)
        self.assertEqual(COUNTS["project"],68)
        self.assertEqual(COUNTS["jenkins_job"],34)
        self.assertEqual(COUNTS["result"],6)
        self.assertEqual(COUNTS["branch"],85)

    def test_source_and_falsifiable_dependencies(self):
        one=stage_labels(0)
        two=stage_labels(741)
        self.assertNotEqual(one["stage"],two["stage"])
        self.assertEqual(one["app"],two["app"])
        self.assertEqual(one["project"],two["project"])
        self.assertEqual(one["jenkins_job"],two["jenkins_job"])
        self.assertIn("/stage-",one["stage"])

    def test_full_marginals_and_exact_content_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/"vm_census.jsonl"
            meta=generate(path)
            proof=validate(path,meta)
            self.assertEqual(proof["rows"],53205)
            self.assertEqual(proof["marginals"],COUNTS)
            second=generate(path)
            self.assertEqual(meta["sha256"],second["sha256"])
            self.assertEqual(meta["bytes"],second["bytes"])
            self.assertEqual(validate(path,second)["distinct_full_label_keys"],TOTAL_SERIES)
            blob=path.read_bytes()
            self.assertNotIn(b'"metric_name":"jenkins_ci_95k_daily"',blob[:1000])

    def test_rejects_corrupted_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=pathlib.Path(tmp)/"census.jsonl"
            meta=generate(path)
            data=path.read_text()
            self.assertIn('"app_name"',data)
            path.write_text(data.replace('"app_name"','"app_name_bad"',1))
            with self.assertRaises(ValueError):
                validate(path,meta)


if __name__=="__main__":
    unittest.main()
