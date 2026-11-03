#!/usr/bin/env python3
"""R10 pure math + emitted Timeseries JSON tests for shifted node starts."""
import datetime as dt
import importlib.util
import json
import pathlib
import tempfile
import unittest


def module():
    file=pathlib.Path(__file__).parent.parent/"ts-lab-r01"/"generate_k8s_churn_fixture.py"
    spec=importlib.util.spec_from_file_location("r10_fixture_generator",file)
    m=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def actual_bucket_rows(fixture,steps):
    out={str(b):0 for b in steps}
    for line in fixture.open():
        row=json.loads(line)
        sec=[
            int(dt.datetime.strptime(t,"%Y-%m-%d %H:%M:%S.%f")
                .replace(tzinfo=dt.timezone.utc).timestamp())
            for t,_ in row["samples"]
        ]
        for b in steps:
            out[str(b)]+=len({t//b for t in sec})
    return out


class TestBucketAlignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g=module()

    def test_shift_is_bounded_and_reproducible(self):
        offsets=[self.g.node_start_offset(i) for i in range(400)]
        self.assertEqual(offsets[0],0)
        self.assertEqual(offsets[1],317)
        self.assertEqual(len(set(offsets)),400)
        self.assertTrue(all(0<=t<10800 for t in offsets))

    def test_closed_form_bucket_rows_full_fixture(self):
        # Independent arithmetic rather than a 97MB fixture to run quickly.
        out={900:0,3600:0,10800:0}
        expected_series=0
        for life in range(24):
            for slot in range(400):
                offset=(slot*317)%10800
                cadences=[60,300,300]+([15] if slot%100==0 else [])
                for step in cadences:
                    expected_series+=1
                    first=self.g.BASE+life*10800+offset+step-1
                    last=self.g.BASE+life*10800+offset+10800-1
                    for bucket in out:
                        out[bucket]+=last//bucket-first//bucket+1
        self.assertEqual(expected_series,28896)
        self.assertEqual(out,{900:368544,3600:113760,10800:57120})

    def test_emitted_rows_match_buckets_and_preserve_tags_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)
            aligned=root/"aligned.jsonl"
            shifted=root/"shifted.jsonl"
            a=self.g.generate(aligned,root/"a.json",1,4,stagger=False)
            b=self.g.generate(shifted,root/"b.json",1,4,stagger=True)
            self.assertEqual(a["series"],b["series"])
            self.assertEqual(a["samples"],b["samples"])
            self.assertNotEqual(a["sha256"],b["sha256"])
            self.assertNotIn("bucket_rows_by_step",a)
            self.assertEqual(b["bucket_rows_by_step"],
                             actual_bucket_rows(shifted,(900,3600,10800)))
            with aligned.open() as lhs, shifted.open() as rhs:
                different=0
                for l,r in zip(lhs,rhs):
                    x=json.loads(l);y=json.loads(r)
                    self.assertEqual(x["metric_name"],y["metric_name"])
                    self.assertEqual(x["tags"],y["tags"])
                    self.assertEqual([v for _,v in x["samples"]],
                                     [v for _,v in y["samples"]])
                    if x["samples"][0][0]!=y["samples"][0][0]:
                        different+=1
                self.assertGreater(different,0)
            again=self.g.generate(root/"again.jsonl",root/"c.json",1,4,stagger=True)
            self.assertEqual(b["sha256"],again["sha256"])
            self.assertEqual(shifted.read_bytes(),(root/"again.jsonl").read_bytes())


if __name__=="__main__":
    unittest.main()
