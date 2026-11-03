#!/usr/bin/env python3
"""R22: stable-ID TAGS cache expiration at 1s vs 3600s, held capacity 1MiB.

Independent root from R21 capacity knee. Four clean TimeSeries O1 servers
baseline 3600s, candidate 1s, candidate 1s, baseline3600s. All data:
4096 stable ID *12 batches = 49152 samples. Every batch AFTER first is
sent 1.10s after previous INSERT completes, so one-second cache entry
must be expired before subsequent use, while one-hour entry stays alive.
TAGS, TimeRanges, Samples merges stopped to observe raw write amplification.
Native query output remains exact. This accelerates expiration but does not
represent 1h retention in production or steady-state merged physical parts.
"""
import argparse
import json
import pathlib
import statistics
import math

import r21_tags_cache_capacity_abba as cache

PHASES=(('A0',3600),('B1',1),('B2',1),('A3',3600))
DELAY_SECONDS=1.1
CANONICAL_SHA=cache.FIXTURE_CANONICAL_SHA
METRICS={
  'raw_tags_rows':('raw_tags_rows',),
  'raw_tags_bytes':('raw_tags_bytes',),
  'process_ingest_cpu_s':('ingest','cpu_s'),
  'process_write_bytes':('ingest','write_bytes_delta'),
  'native_wall_p50_ms':('native','wall_p50_ms'),
  'native_cpu_p50_ms':('native','cpu_p50_ms'),
  'native_read_rows':('native','read_rows_p50'),
  'native_read_bytes':('native','read_bytes_p50'),
  'cache_hits':('total_cache_hits',),
  'cache_misses':('total_cache_misses',)
}


def compare(phases):
    results={}
    for k,path in METRICS.items():
        def value(name):
            v=phases[name]['stats']
            for p in path:
                v=v[p]
            return float(v)
        base=[value('A0'),value('A3')]
        cand=[value('B1'),value('B2')]
        a=statistics.mean(base)
        b=statistics.mean(cand)
        delta=abs(base[0]-base[1])/a if a else None
        results[k]={'long_expiration_values':base,
            'short_expiration_values':cand,
            'long_mean':round(a,6),
            'short_mean':round(b,6),
            'short_over_long':round(b/a,6) if a else None,
            'baseline_AA_drift':round(delta,6) if delta is not None else None}
    return results


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--binary',required=True)
    ap.add_argument('--outdir',required=True)
    ap.add_argument('--port',type=int,default=18191)
    a=ap.parse_args()
    root=pathlib.Path(a.outdir)
    root.mkdir(parents=True,exist_ok=True)
    blocks,input_proof=cache.fixture('fixed')
    if input_proof['canonical_sha256']!=CANONICAL_SHA:
        raise AssertionError('Canonical R21/R22 fixture SHA changed')
    report={'schema_version':1,
            'question_id':'R22-tags-cache-expiration-write-amplification-v1',
            'source_sha':cache.SOURCE,'binary_sha256':cache.BINARY_SHA,
            'cache_bytes':cache.LARGE_BYTES,
            'input_sha256':input_proof['canonical_sha256'],
            'batch_delay_seconds':DELAY_SECONDS,
            'phases':{},'comparisons':{},'errors':[],'passed':False,
            'cpu_valid':None,'native_latency_valid':None,
            'decision':'INCONCLUSIVE'}
    def save():
        (root/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    try:
        for phase,expire in PHASES:
            x=cache.one_phase(a.binary,root,phase,'large',blocks,input_proof,a.port,
                 expiration_seconds=expire,delay_between_batches_seconds=DELAY_SECONDS)
            x['expiration_seconds']=expire
            report['phases'][phase]=x
            save()
        for phase,expire in PHASES:
            p=report['phases'][phase]
            if p['cache_bytes']!=cache.LARGE_BYTES or p['cache_expiration_seconds']!=expire:
                raise AssertionError('Wrong cache size/expiration DDL')
            if p['delay_between_batches_seconds']!=DELAY_SECONDS:
                raise AssertionError('Input cadence changed')
            if p['physical']['physical_samples_points']!=cache.SERIES*cache.BATCHES:
                raise AssertionError('Wrong physical samples count')
            if p['physical']['tags']['distinct_id']!=cache.SERIES:
                raise AssertionError('Original series ID count changed')
            if (p['stats']['total_cache_hits']+p['stats']['total_cache_misses'])!=cache.SERIES*cache.BATCHES:
                raise AssertionError('Missing global cache hit/miss increments')
            if p['physical']['tags']['count']!=p['stats']['total_cache_misses']:
                raise AssertionError('Physical TAGS writes mismatch global miss count')
            if len(p['insert_rows'])!=cache.BATCHES or len(p['native_samples'])!=cache.WARMUP+cache.MEASURED:
                raise AssertionError('Missing input insert or native output samples')
            if p['input_sha256']!=input_proof['ordered_sha256']:
                raise AssertionError('One phase had different input bytes')
        report['comparisons']=compare(report['phases'])
        drift_cpu=report['comparisons']['process_ingest_cpu_s']['baseline_AA_drift']
        drift_native=report['comparisons']['native_wall_p50_ms']['baseline_AA_drift']
        report['cpu_valid']=drift_cpu<=.25
        report['native_latency_valid']=drift_native<=.25
        short_rows=report['comparisons']['raw_tags_rows']['short_mean']
        long_rows=report['comparisons']['raw_tags_rows']['long_mean']
        if long_rows!=cache.SERIES:
            raise AssertionError('Long cache did not retain 4096 stable IDs in controlled pacing')
        if short_rows==cache.SERIES*cache.BATCHES:
            report['decision']='EXPIRATION_FORCES_ALL_TAGS_REWRITES'
        elif short_rows>cache.SERIES:
            report['decision']='PARTIAL_EXPIRATION_WRITE_AMPLIFICATION'
        else:
            report['decision']='EXPIRATION_NO_PHYSICAL_EFFECT'
        if not report['cpu_valid'] or not report['native_latency_valid']:
            report['decision']+='_CPU_OR_WALL_INVALID'
        report['passed']=True
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
        report['decision']='INVALID_METHOD_OR_CORRECTNESS_FAIL'
    finally:
        save()
        print('R22_EXPIRATION_DONE',json.dumps({'passed':report['passed'],
            'decision':report['decision'],
            'cpu_valid':report['cpu_valid'],
            'native_latency_valid':report['native_latency_valid'],
            'ratios':report['comparisons'],'errors':report['errors']},
            separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
