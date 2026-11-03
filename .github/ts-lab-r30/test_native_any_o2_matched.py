#!/usr/bin/env python3
import pathlib
import sys
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).parent))
from bench_native_any_o2_matched import PHASES,SELECTORS,STOCK_SHA,stock_sql,summarize

class NativeMatchedO2Tests(unittest.TestCase):
    def test_one_source_variable_and_four_phase_mirror(self):
        self.assertEqual(PHASES,("stock","any","any","stock"))
        self.assertEqual(len(STOCK_SHA),64)
    def test_expected_native_sql_is_not_standalone_metadata_select(self):
        for case in SELECTORS:
            query=stock_sql(case)
            self.assertIn("FROM prometheusQueryRange(r30_ts,",query)
            self.assertIn("max_threads=4",query)
            self.assertIn("max_memory_usage=1800000000",query)
            self.assertIn("FORMAT JSONEachRow",query)
    def test_strict_data_drift_rejection(self):
        r={"phases":[],"summary":{},"errors":[]}
        for i,mode in enumerate(PHASES):
            wall=10 if mode=="stock" else 9
            if i==3:wall=20
            queries={name:{"median":{"wall_ms":wall,"cpu_ms":11,
                      "query_memory_bytes":10000,"read_rows":300,"read_bytes":900}}
                     for name in SELECTORS}
            r["phases"].append({"mode":mode,"queries":queries})
        summarize(r)
        self.assertTrue(any("A/A wall drift" in x for x in r["errors"]))
    def test_no_drift_for_identical_controls(self):
        r={"phases":[],"summary":{},"errors":[]}
        for mode in PHASES:
            queries={name:{"median":{"wall_ms":10 if mode=="stock" else 8,
                      "cpu_ms":11,"query_memory_bytes":10000,
                      "read_rows":300,"read_bytes":900}}
                     for name in SELECTORS}
            r["phases"].append({"mode":mode,"queries":queries})
        summarize(r)
        self.assertFalse(r["errors"])
        self.assertEqual(r["summary"]["stage"]["any"]["wall_ms"],8)

if __name__=="__main__":
    unittest.main()
