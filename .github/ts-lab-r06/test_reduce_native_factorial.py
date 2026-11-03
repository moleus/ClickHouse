#!/usr/bin/env python3
"""Focused negative tests for R06 raw historical-artifact reducer."""
import json
import pathlib
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from reduce_native_factorial import FIXTURE, ORDER, load, reduce


def make_report(case='stage'):
    phases=[]
    for i,mode in enumerate(ORDER):
        ms=100+(10 if mode.startswith('auto') else 0)+(20 if mode.endswith('hash') else 0)
        trials=[]
        for t in range(7):
            trials.append({'qid':f'x_{i}_{t}','warmup':t<3,'max_abs_error':1e-13,
                           'wall_ms':ms,'cpu_ms':ms*2,'peak_memory_bytes':1234567,
                           'read_rows':1000-(100 if mode.startswith('auto') else 0),
                           'read_bytes':20000,'selected_marks':10})
        phases.append({'variant':mode,'index':i,'wall_ms_median':ms,
                       'trials':trials,'representative_profile_qid':f'x_{i}_3',
                       'representative_profile':{'processors':[
                           {'name':'MergeSortingTransform' if mode=='auto_merge' else 'MergeJoinTransform',
                            'plan_step_name':'Sorting' if mode=='auto_merge' else 'Join','work_us':12345}]}})
    modes=set(ORDER)
    return {'case':case,'days':7,'fixture_sha':FIXTURE,'passed':True,'errors':[],
            'phases':phases,'summary':{'variants':{mode:{'wall_ms':sum(p['wall_ms_median'] for p in phases if p['variant']==mode)/2} for mode in modes}}}


def make_zip(path,report):
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('native-factorial-stage/report.json',json.dumps(report))
        z.writestr('95k-manifest.json',json.dumps({'sha256':FIXTURE,'series':1330000}))
        z.writestr('patch-binary-hash.txt','clickhouse: OK\n')


class ReducerTests(unittest.TestCase):
    def setUp(self):
        self.d=tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.path=pathlib.Path(self.d.name)/'fake.zip'

    def check(self,report):
        make_zip(self.path,report)
        r,origin=load(self.path,'stage')
        return reduce(r,origin)

    def test_paired_factorial(self):
        result=self.check(make_report())
        self.assertEqual(result['projection']['rows_saved'],100)
        self.assertAlmostEqual(result['projection']['merge_wall_delta_ms'],10)
        self.assertEqual(len(result['variants']),4)

    def test_wrong_fixture_fails(self):
        x=make_report();x['fixture_sha']='wrong'
        with self.assertRaisesRegex(ValueError,'fixture'):self.check(x)

    def test_mixed_phase_order_fails(self):
        x=make_report();x['phases'][0]['variant']='auto_merge'
        with self.assertRaisesRegex(ValueError,'order'):self.check(x)

    def test_duplicate_query_id_fails(self):
        x=make_report();x['phases'][0]['trials'][1]['qid']=x['phases'][0]['trials'][0]['qid']
        with self.assertRaisesRegex(ValueError,'Repeated'):self.check(x)

    def test_missing_log_fails(self):
        x=make_report();del x['phases'][0]['trials'][3]['cpu_ms']
        with self.assertRaisesRegex(ValueError,'Missing QueryFinish'):self.check(x)

    def test_numeric_parity_fails(self):
        x=make_report();x['phases'][0]['trials'][4]['max_abs_error']=0.2
        with self.assertRaisesRegex(ValueError,'Parity'):self.check(x)

    def test_report_passed_gate(self):
        x=make_report();x['passed']=False
        with self.assertRaisesRegex(ValueError,'gate'):self.check(x)


if __name__=='__main__':unittest.main()
