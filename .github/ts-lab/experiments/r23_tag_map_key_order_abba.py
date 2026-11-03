#!/usr/bin/env python3
"""R23: actual tags Map key ordering versus canonicalized full native TimeSeries.

One controlled variable: order of key/value pairs in JSONEachRow tags Map.
The row arrival order, all series ID label pairs, values and timestamps,
cache size=1MiB, expiration=3600s, and source/binary are identical. Stock
TimeSeriesSink sorts tags before both ID calculation and TAGS dedup-cache
lookup. Does random Map key order cost extra whole-process CPU despite stable
TAGS/TimeRanges/SAMPLES? This runner tests that; does NOT implement a fastpath.

Distinct from R21 row order/capacity and R22 cache expiry. Reuse the proved
R21 fresh-server phase procedure unchanged and record its git blob SHA.
"""
import argparse
import hashlib
import json
import math
import pathlib
import random
import statistics
import sys

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r21_tags_cache_capacity_abba as r21

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
BINARY='f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e'
ROOT_QUESTION='R23-tag-map-key-order-normalization-cost-v1'
SERIES=4096
BATCHES=24
EXTRA_LABELS=16
INPUT_TAGS=3+EXTRA_LABELS
EXPECTED_TAGS=INPUT_TAGS+1  # metric __name__ is added by pinned source
TABLE='tslab_r23_tag_order'
METRIC='tslab_r23_tag_order_gauge'
PHASES=(('A0','sorted'),('B1','shuffled'),('B2','shuffled'),('A3','sorted'))
WARMUP=3
MEASURED=6

r21.SERIES=SERIES
r21.BATCHES=BATCHES
r21.METRIC=METRIC
r21.TABLE=TABLE
r21.WARMUP=WARMUP
r21.MEASURED=MEASURED


def tags_for(i):
    values=[('project',f'p{i%4}'),('series',f'series-{i:05d}'),('rack',f'r{i%32:02d}')]
    values.extend((f'lab_{j:02d}',f'v{(i*(j+3)+j)%997:03d}')
                  for j in range(EXTRA_LABELS))
    if len({k for k,v in values})!=INPUT_TAGS:
        raise AssertionError('Input tags are not distinct')
    return sorted(values)


def payload(i,batch,order):
    entries=tags_for(i)
    if order=='shuffled':
        random.Random((i*100003) ^ (batch*7919) ^ 0x230011).shuffle(entries)
    if order not in ('sorted','shuffled'):
        raise AssertionError('Unknown Map key order '+order)
    record={'metric_name':METRIC,'tags':dict(entries),
            'samples':[[r21.base.stamp(r21.BASE+60*batch),
                        20.0+(i%97)+batch*.25]]}
    wire=json.dumps(record,sort_keys=False,separators=(',',':'))+'\n'
    canonical=json.dumps(record,sort_keys=True,separators=(',',':'))+'\n'
    return wire,canonical


def fixture():
    all_blocks={v:[] for v in ('sorted','shuffled')}
    per_batch={v:[] for v in all_blocks}
    wire_hasher={v:hashlib.sha256() for v in all_blocks}
    sem_hasher={v:hashlib.sha256() for v in all_blocks}
    lengths={v:0 for v in all_blocks}
    changed=0
    for b in range(BATCHES):
        blocks={v:[] for v in all_blocks}
        for i in range(SERIES):
            first,canonical_a=payload(i,b,'sorted')
            second,canonical_b=payload(i,b,'shuffled')
            if canonical_a!=canonical_b:
                raise AssertionError('Canonical label/value/time differs between JSON key orders')
            if len(first)!=len(second):
                raise AssertionError('Input wire row length changed with label Map ordering')
            if first!=second:
                changed+=1
            for variant,wire,canonical in (('sorted',first,canonical_a),
                                             ('shuffled',second,canonical_b)):
                blocks[variant].append(wire)
                sem_hasher[variant].update(canonical.encode())
        for variant in all_blocks:
            block=''.join(blocks[variant])
            all_blocks[variant].append(block)
            per_batch[variant].append(hashlib.sha256(block.encode()).hexdigest())
            wire_hasher[variant].update(block.encode())
            lengths[variant]+=len(block)
        if len(blocks['sorted'])!=SERIES or len(blocks['shuffled'])!=SERIES:
            raise AssertionError('Bad sample point cardinality')
    semantics={v:sem_hasher[v].hexdigest() for v in all_blocks}
    if semantics['sorted']!=semantics['shuffled']:
        raise AssertionError('Canonical logical fixture hashes are different')
    if lengths['sorted']!=lengths['shuffled']:
        raise AssertionError('Input bytes by serialization differ')
    if changed<(SERIES*BATCHES)*.95:
        raise AssertionError('Too few JSON rows had a different tag key order')
    wire={v:wire_hasher[v].hexdigest() for v in all_blocks}
    if wire['sorted']==wire['shuffled']:
        raise AssertionError('Map key order did not affect wire byte encoding')
    proof={'canonical_sha256':semantics['sorted'],
           'wire_sha256':wire,'wire_bytes':lengths,
           'per_batch_sha256':per_batch,
           'changed_order_rows':changed,
           'series':SERIES,'batches':BATCHES,
           'sample_points_per_phase':SERIES*BATCHES,
           'input_tags':INPUT_TAGS,'canonical_stored_tags':EXPECTED_TAGS}
    return all_blocks,proof


_original_raw_counts=r21.raw_counts

def count_with_stable_id_fingerprint(endpoint,table):
    physics=_original_raw_counts(endpoint,table)
    inner='default.'+chr(96)+physics['tags']['inner_name']+chr(96) if 'inner_name' in physics['tags'] else None
    if inner is None:
        uuid=r21.base.table_uuid(endpoint,table)
        inner='default.'+chr(96)+'.inner_id.tags.'+uuid+chr(96)
    normalized=r21.base.sql(endpoint,
        'SELECT toString(id),toString(tags) FROM '+inner+' ORDER BY id FORMAT TSV')
    entries=normalized.splitlines()
    if len(entries)!=SERIES or any('\t' not in x for x in entries):
        raise AssertionError('Missing canonical TAGS ID+Map rows in physical table')
    physics['tags']['canonical_id_and_tags_sha256']=hashlib.sha256(normalized.encode()).hexdigest()
    shapes=r21.base.sql(endpoint,
        'SELECT min(length(tags)),max(length(tags)) FROM '+inner+' FORMAT TSV').strip().split('\t')
    if len(shapes)!=2 or [int(x) for x in shapes]!=[EXPECTED_TAGS,EXPECTED_TAGS]:
        raise AssertionError(f'Stored tag count differs from expected {EXPECTED_TAGS}: {shapes}')
    physics['tags']['normalized_map_labels']=EXPECTED_TAGS
    return physics

r21.raw_counts=count_with_stable_id_fingerprint


def reduce(phases):
    metrics={
       'ingest_process_cpu_s':('ingest','cpu_s'),
       'ingest_process_wall_s':('ingest','wall_s'),
       'ingest_write_bytes':('ingest','write_bytes_delta'),
       'query_log_insert_cpu_ms':('querylog_ingest_cpu_ms',),
       'native_wall_p50_ms':('native','wall_p50_ms'),
       'native_cpu_p50_ms':('native','cpu_p50_ms'),
       'native_read_bytes_p50':('native','read_bytes_p50'),
       'native_read_rows_p50':('native','read_rows_p50'),
       'raw_tags_bytes':('raw_tags_bytes',),
       'raw_timeranges_bytes':('raw_timeranges_bytes',)}
    comparison={}
    for name,path in metrics.items():
        def get(phase):
            d=phases[phase]['stats']
            for token in path:
                d=d[token]
            return float(d)
        a,z=get('A0'),get('A3')
        b,c=get('B1'),get('B2')
        baseline=statistics.mean((a,z))
        candidate=statistics.mean((b,c))
        if baseline<=0 or candidate<=0:
            raise AssertionError('Nonpositive benchmark metric '+name)
        comparison[name]={'sorted_mean':round(baseline,5),
                          'shuffled_mean':round(candidate,5),
                          'shuffled_over_sorted':round(candidate/baseline,5),
                          'sorted_AA_drift':round(abs(a-z)/baseline,5),
                          'sorted_phase_values':[a,z],
                          'shuffled_phase_values':[b,c]}
    return comparison


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--binary',required=True)
    p.add_argument('--outdir',required=True)
    p.add_argument('--port',type=int,default=18191)
    args=p.parse_args()
    root=pathlib.Path(args.outdir)
    root.mkdir(parents=True,exist_ok=True)
    blocks,inputs=fixture()
    report={'schema_version':1,'question_id':ROOT_QUESTION,
      'subexperiment':'same-full-ID-same-row-order-map-key-shuffle-ABBA',
      'source_sha':SOURCE,'binary_sha256':BINARY,
      'fixture':inputs,'phases':{},'comparison':{},'errors':[],
      'passed':False,'decision':'INCONCLUSIVE'}
    def save():
        (root/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    try:
        for phase,order in PHASES:
            # R21 one_phase preserves the binary, CPU, QueryFinish, full native
            # oracle and cache event gates. All four runs use 1MiB cache.
            input_proof={'ordered_sha256':inputs['wire_sha256'][order],
                         'per_batch_sha256':inputs['per_batch_sha256'][order]}
            r=r21.one_phase(args.binary,root,phase,'large',
                            blocks[order],input_proof,args.port)
            r['tag_map_key_order']=order
            r['canonical_fixture_sha256']=inputs['canonical_sha256']
            if len(r['insert_rows'])!=BATCHES or len(r['native_samples'])!=WARMUP+MEASURED:
                raise AssertionError('Missing request or semantic evidence')
            if r['stats']['total_cache_misses']!=SERIES or \
               r['stats']['total_cache_hits']!=SERIES*(BATCHES-1):
                raise AssertionError('Map order changed TAGS cache hit/miss cardinality')
            if r['physical']['tags']['count']!=SERIES or \
               r['physical']['tags']['distinct_id']!=SERIES or \
               r['physical']['samples']['distinct_id']!=SERIES or \
               r['physical']['physical_samples_points']!=SERIES*BATCHES:
                raise AssertionError('Map key order altered stable physical IDs or samples')
            if r['physical']['tags']['normalized_map_labels']!=EXPECTED_TAGS:
                raise AssertionError('Incorrect normalized label cardinality')
            report['phases'][phase]=r
            save()
            print('R23_PHASE',json.dumps({'phase':phase,'order':order,
                'cpu_s':r['stats']['ingest']['cpu_s'],
                'write_bytes':r['stats']['ingest']['write_bytes_delta'],
                'cache_hits':r['stats']['total_cache_hits'],
                'cache_misses':r['stats']['total_cache_misses'],
                'tags_rows':r['physical']['tags']['count'],
                'id_map_sha256':r['physical']['tags']['canonical_id_and_tags_sha256'],
                'native_p50_ms':r['stats']['native']['wall_p50_ms']},
                separators=(',',':')),flush=True)
        if len({p['physical']['tags']['canonical_id_and_tags_sha256']
                for p in report['phases'].values()})!=1:
            raise AssertionError('Same tag key/value sets produced different physical ID+Map data')
        if len({p['native_query_sha256'] for p in report['phases'].values()})!=1:
            raise AssertionError('Native query text changed between phases')
        if len({json.dumps(p['native_samples'][0]['labels'],sort_keys=True)
                for p in report['phases'].values()})!=1:
            raise AssertionError('Native output different among phases')
        report['comparison']=reduce(report['phases'])
        for metric in ('ingest_process_cpu_s','native_wall_p50_ms','native_cpu_p50_ms'):
            if report['comparison'][metric]['sorted_AA_drift']>.25:
                raise AssertionError(f'Control drift>25% for {metric}')
        for metric in ('raw_tags_bytes','native_read_rows_p50'):
            ratio=report['comparison'][metric]['shuffled_over_sorted']
            if abs(ratio-1)>.01:
                raise AssertionError(f'Unexpected physical/read amplification {metric}: {ratio}')
        cpu=report['comparison']['ingest_process_cpu_s']
        relevant=max(.05,cpu['sorted_AA_drift'])
        if cpu['shuffled_over_sorted']>1+relevant:
            report['decision']='INPUT_MAP_ORDER_CPU_PENALTY_REPLICATION_NEEDED'
        elif cpu['shuffled_over_sorted']<1-relevant:
            report['decision']='INPUT_MAP_ORDER_CPU_BENEFIT_REPLICATION_NEEDED'
        else:
            report['decision']='NO_ROBUST_CPU_ORDER_EFFECT'
        report['passed']=True
    except Exception as exc:
        report['errors'].append(str(exc)[:2200])
        report['decision']='INVALID_METHOD_OR_CORRECTNESS_FAIL'
    finally:
        save()
        print('R23_DONE',json.dumps({'passed':report['passed'],
           'decision':report['decision'],
           'fixture':report['fixture']['canonical_sha256'],
           'comparison':report['comparison'],'errors':report['errors']},
           separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
