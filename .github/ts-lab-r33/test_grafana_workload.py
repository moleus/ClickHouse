#!/usr/bin/env python3
import datetime as dt
import json
import pathlib
import sys
import tempfile
import unittest
from urllib.parse import parse_qs,urlsplit

sys.path.insert(0,str(pathlib.Path(__file__).parent))
from generate_grafana_ci_v2 import generate,validate,legacy_modulo_failure,series_rows,STAGES
from grafana_query_catalog import (
    catalog,api_url,instant_url,metadata_url,
    normalize_api,normalize_sql,exact_response_parity,sql_statement,
)

class R33FixtureTests(unittest.TestCase):
    def test_build_has_38_stages_one_build_label_and_stable_job_branch(self):
        rows=list(series_rows(3,25))
        self.assertEqual(len(rows),STAGES)
        self.assertEqual(len({x["tags"]["stage"] for x in rows}),STAGES)
        for label in ("build","branch","jenkins_job","app","project"):
            self.assertEqual(len({x["tags"][label] for x in rows}),1)
        self.assertEqual(len({x["samples"][0][0] for x in rows}),STAGES)

    def test_determinism_and_streamed_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=pathlib.Path(tmp)/"fixture.jsonl"
            a=generate(f,days=2,builds_per_day=64)
            self.assertEqual(a["unique_series"],2*64*38)
            self.assertEqual(a["unique_build_ids"],128)
            result=validate(f,a)
            self.assertEqual(result["rows"],a["unique_series"])
            self.assertEqual(result["unique_builds"],a["unique_build_ids"])
            before=f.read_bytes()[:1200]
            b=generate(f,days=2,builds_per_day=64)
            self.assertEqual(a["sha256"],b["sha256"])
            self.assertEqual(before,f.read_bytes()[:1200])
            self.assertGreaterEqual(result["min_stage_branch_choices"],2)

    def test_legacy_stage_branch_project_correlation(self):
        a=legacy_modulo_failure()
        self.assertTrue(a["half_stage_project_combinations_structurally_missing"])
        self.assertEqual(a["old_mean_stages_per_build"],1)
        for observed in a["checks"].values():
            self.assertEqual(observed["number_of_possible_projects"],4)
            self.assertEqual(observed["number_of_possible_feature_branches"],1)

class R33Requests(unittest.TestCase):
    def setUp(self):
        self.meta={"end":1787011200}
    def test_grafana_range_is_evaluated_per_step(self):
        cases=catalog(self.meta)
        self.assertEqual(len(cases),8)
        self.assertEqual({x["type"] for x in cases},{"range"})
        chosen=cases[0]
        self.assertEqual(chosen["eval_count"],14)
        url=api_url("http://127.0.0.1:18191","grafana_r33",chosen)
        split=urlsplit(url)
        self.assertEqual(split.path,"/prometheus/api/v1/query_range")
        p=parse_qs(split.query)
        self.assertEqual(p["step"],["86400"])
        self.assertEqual(p["database"],["default"])
        self.assertEqual(p["table"],["grafana_r33"])
        self.assertEqual(p["start"],[str(chosen["start"])])
        self.assertIn("avg_over_time",p["query"][0])
        self.assertIn("prometheusQueryRange",sql_statement("grafana_r33",chosen))

    def test_grafana_instant_and_variable_endpoint_shapes(self):
        url=instant_url("http://localhost:18191","grafana_r33","up",1787011200)
        self.assertIn("/prometheus/api/v1/query?",url)
        q=metadata_url("http://localhost:18191","grafana_r33","label/stage/values",
                       {"match[]":["{__name__=\"jenkins_avs_build_stage_duration\"}"]})
        self.assertIn("/prometheus/api/v1/label/stage/values?",q)
        self.assertIn("match%5B%5D",q)

    def test_exact_sql_http_point_value_label_parity_and_gaps(self):
        rows=[{"tags":{"stage":"build"},"samples":[["2026-08-04 01:00:00.000",2.5],
                                                  ["2026-08-04 02:00:00.000",3.5]]}]
        a={"status":"success","data":{"resultType":"matrix","result":[
          {"metric":{"stage":"build"},"values":[[1785805200,"2.5"],[1785808800,"3.5"]]}
        ]}}
        out=exact_response_parity(rows,a,"synthetic")
        self.assertEqual(out["points"],2)
        a["data"]["result"][0]["values"][0][1]="7.5"
        with self.assertRaisesRegex(ValueError,"value_mismatch"):
            exact_response_parity(rows,a,"synthetic")
        a["data"]["result"][0]["values"]=[]
        with self.assertRaises(ValueError):
            exact_response_parity(rows,a,"synthetic")

if __name__=="__main__":unittest.main()
