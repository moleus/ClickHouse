#!/usr/bin/env python3
"""R21: same-runner fixed-vs-rotated original byte-equivalent series order ABBA.

Prior jobs 41652867042 proved two independent working-set cache knees, but
the two orderings ran on different host machines. This test holds the same
32768-byte TAGS cache and all logical inputs fixed; change ONLY per-INSERT
input JSONEachRow ordering. No cache-capacity change, no new index or binary.
Four fresh servers: fixed / rotate / rotate / fixed.
"""
import argparse
import hashlib
import json
import math
import pathlib
import statistics

import r21_tags_cache_capacity_abba as cache

PHASES=(('A0','fixed'),('B1','rotate'),('B2','rotate'),('A3','fixed'))
FIELDS={
    'write_cpu_s':('ingest','cpu_s'),
    'write_io_bytes':('ingest','write_bytes_delta'),
    'raw_tags_rows':('raw_tags_rows',),
    'tags_disk_bytes':('raw_tags_bytes',),
    'tags_cache_hits':('total_cache_hits',),
    'tags_cache_misses':('total_cache_misses',),
    'native_wall_p50_ms':('native','wall_p50_ms'),
    'native_cpu_p50_ms':('native','cpu_p50_ms'),
    'native_read_rows':('native','read_rows_p50'),
    'native_read_bytes':('native','read_bytes_p50'),
}


def metric(phases,phase,path):
    value=phases[phase]['stats']
    for element in path:
        value=value[element]
    return float(value)


def comparison(phases):
    result={}
    for name,path in FIELDS.items():
        fixed=[metric(phases,x,path) for x in ('A0','A3')]
        rotate=[metric(phases,x,path) for x in ('B1','B2')]
        f=statistics.mean(fixed)
        r=statistics.mean(rotate)
        drift=abs(fixed[0]-fixed[1])/f if f else None
        result[name]={'fixed_phase_values':fixed,'rotate_phase_values':rotate,
                      'fixed':round(f,5),'rotate':round(r,5),
                      'rotated_over_fixed':round(r/f,6) if f else None,
                      'fixed_control_drift':round(drift,6) if drift is not None else None}
    return result


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--binary',required=True)
    a.add_argument('--outdir',required=True)
    a.add_argument('--port',type=int,default=18191)
    opts=a.parse_args()
    root=pathlib.Path(opts.outdir)
    root.mkdir(parents=True,exist_ok=True)
    fixture={order:cache.fixture(order) for order in ('fixed','rotate')}
    if fixture['fixed'][1]['canonical_sha256']!=fixture['rotate'][1]['canonical_sha256']:
        raise AssertionError('Logical data differs between order hypotheses')
    if fixture['fixed'][1]['ordered_sha256']==fixture['rotate'][1]['ordered_sha256']:
        raise AssertionError('Test ordering never changed bytes')
    report={'schema_version':1,
       'question_id':'R21-tags-dedup-cache-working-set-knee-v1',
       'subexperiment':'small-cache-only-fixed-rotated-rotated-fixed-ABBA',
       'source_sha':cache.SOURCE,'binary_sha256':cache.BINARY_SHA,
       'cache_bytes':cache.SMALL_BYTES,
       'canonical_fixture_sha256':cache.FIXTURE_CANONICAL_SHA,
       'fixture_by_order':{k:v[1] for k,v in fixture.items()},
       'phases':{},'comparison':{},'errors':[],
       'passed':False,'scientific_decision':'INCONCLUSIVE'}
    def save():
        (root/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    try:
        for phase,order in PHASES:
            blocks,proof=fixture[order]
            result=cache.one_phase(opts.binary,root,phase,'small',
                             blocks,proof,opts.port)
            if result['cache_bytes']!=cache.SMALL_BYTES:
                raise AssertionError('Input order study changed cache capacity')
            if result['input_sha256']!=proof['ordered_sha256']:
                raise AssertionError('Ordered fixture not applied to source')
            result['input_order']=order
            report['phases'][phase]=result
            save()
        native_sql={x['native_query_sha256'] for x in report['phases'].values()}
        if len(native_sql)!=1:
            raise AssertionError('Native query text differs between orders')
        for phase,order in PHASES:
            if report['phases'][phase]['physical']['physical_samples_points']!=cache.SERIES*cache.BATCHES:
                raise AssertionError('Physical sample data differs')
            if report['phases'][phase]['physical']['tags']['distinct_id']!=cache.SERIES:
                raise AssertionError('Physical stable IDs differ')
            if len(report['phases'][phase]['native_samples'])!=cache.WARMUP+cache.MEASURED:
                raise AssertionError('Native oracle sample count differs')
        report['comparison']=comparison(report['phases'])
        for field in ('write_cpu_s','native_wall_p50_ms','native_cpu_p50_ms'):
            if report['comparison'][field]['fixed_control_drift']>.25:
                raise AssertionError(field+' fixed A/A drift >25%')
        hits=report['comparison']['tags_cache_hits']
        misses=report['comparison']['tags_cache_misses']
        if hits['rotated_over_fixed']!=1 or misses['rotated_over_fixed']!=1:
            report['scientific_decision']='ORDER_CHANGES_CACHE_EFFECT_PENDING_REPLICATION'
        else:
            observed=report['comparison']['native_wall_p50_ms']
            cpu=report['comparison']['native_cpu_p50_ms']
            if (abs(observed['rotated_over_fixed']-1) > max(.05,observed['fixed_control_drift'])
                and abs(cpu['rotated_over_fixed']-1)>max(.05,cpu['fixed_control_drift'])):
                report['scientific_decision']='ORDER_CHANGES_READ_LAYOUT_PENDING_REPLICATION'
            else:
                report['scientific_decision']='NO_ROBUST_ORDER_EFFECT'
        report['passed']=True
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
        report['scientific_decision']='INVALID_METHOD_OR_CORRECTNESS_FAIL'
    finally:
        save()
        print('R21_ORDER_ABBA_DONE',json.dumps({'passed':report['passed'],
             'decision':report['scientific_decision'],
             'comparisons':report['comparison'],'errors':report['errors']},
             separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
