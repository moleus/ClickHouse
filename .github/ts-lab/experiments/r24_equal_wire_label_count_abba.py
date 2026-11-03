#!/usr/bin/env python3
"""R24: TimeSeries input Map cardinality at exactly equal JSON wire bytes.

Two different *logical* label shapes: 8 vs 36 input tags, plus __name__. Both
have exactly 4096 stable metric IDs, 24 chronological inserts and identical
core project/series/rack labels, sample values/timestamps, source, RAW UUID
schema and a capacity-sufficient 1MiB TAGS cache. The low-tag case has a
longer deterministic 'payload' label so every input JSON row is exactly the
same number of bytes as the high-tag case. This is net label pair-count cost,
NOT an exclusive std::sort profile. TAGS physical bytes may differ by design.
"""
import argparse
import functools
import hashlib
import json
import pathlib
import statistics
import sys

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r21_tags_cache_capacity_abba as r21

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
BINARY='f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e'
QUESTION='R24-equal-wire-bytes-tag-map-cardinality-cost-v1'
SERIES=4096
BATCHES=24
EXTRA_BY_MODE={'few':4,'many':32}
EXPECTED_INPUT_TAGS={'few':8,'many':36}
EXPECTED_STORED_TAGS={'few':9,'many':37}
PHASES=(('A0','few'),('B1','many'),('B2','many'),('A3','few'))
METRIC='tslab_r24_tags_cardinality'
TABLE='tslab_r24_tags_count'

r21.SERIES=SERIES
r21.BATCHES=BATCHES
r21.METRIC=METRIC
r21.TABLE=TABLE
r21.WARMUP=3
r21.MEASURED=6


@functools.lru_cache(maxsize=8192)
def filler(i, n):
    if n<0:
        raise AssertionError('Invalid pad length')
    return ''.join(hashlib.sha256(f'r24:{i}:{k}'.encode()).hexdigest()
                   for k in range((n+63)//64))[:n]


def common(i):
    return [('project',f'p{i%4}'),('rack',f'r{i%32:02d}'),
            ('series',f'series-{i:05d}')]


def user_tags(i,count,padding):
    fields=common(i)
    fields.extend((f'e{j:02d}',f'v{(i*(j+3)+j)%997:03d}') for j in range(count))
    fields.append(('payload',filler(i,padding)))
    return dict(sorted(fields))


def sample(i,b):
    return [[r21.base.stamp(r21.BASE+60*b),20.0+(i%97)+b*.25]]


def encoded(metric,tags,points):
    return json.dumps({'metric_name':metric,'tags':tags,'samples':points},
                      sort_keys=False,separators=(',',':'))+'\n'


def row_pair(i,b):
    times=sample(i,b)
    many_tags=user_tags(i,EXTRA_BY_MODE['many'],8)
    few_empty=user_tags(i,EXTRA_BY_MODE['few'],0)
    many=encoded(METRIC,many_tags,times)
    few_zero=encoded(METRIC,few_empty,times)
    padding=len(many)-len(few_zero)
    if padding<8:
        raise AssertionError('High label count did not increase wire bytes')
    few_tags=user_tags(i,EXTRA_BY_MODE['few'],padding)
    few=encoded(METRIC,few_tags,times)
    if len(few)!=len(many):
        raise AssertionError('Wire byte length mismatch despite padding')
    if len(few_tags)!=EXPECTED_INPUT_TAGS['few'] or len(many_tags)!=EXPECTED_INPUT_TAGS['many']:
        raise AssertionError('Wrong input tag counts')
    # Stable set of labels required for exact native grouping, independent of
    # different high/low additional labels and padding values.
    core=encoded(METRIC,dict(common(i)),times)
    return few,many,core


def fixture():
    modes=('few','many')
    blocks={k:[] for k in modes}
    wire={k:hashlib.sha256() for k in modes}
    each={k:[] for k in modes}
    total_bytes={k:0 for k in modes}
    common_hash=hashlib.sha256()
    padding_lengths=[]
    for b in range(BATCHES):
        batch={k:[] for k in modes}
        for i in range(SERIES):
            a,c,core=row_pair(i,b)
            if len(a)!=len(c):
                raise AssertionError('Physical network wire bytes are not matched per row')
            batch['few'].append(a)
            batch['many'].append(c)
            common_hash.update(core.encode())
            if b==0:
                # The common labels and samples must have the same canonical
                # row SHA even though the Map key/value density differs.
                few_dict=json.loads(a)['tags']
                many_dict=json.loads(c)['tags']
                if any(few_dict[k]!=many_dict[k] for k in ('project','rack','series')):
                    raise AssertionError('Core stable series identity changed')
                padding_lengths.append(len(few_dict['payload']))
        for mode in modes:
            txt=''.join(batch[mode])
            blocks[mode].append(txt)
            each[mode].append(hashlib.sha256(txt.encode()).hexdigest())
            wire[mode].update(txt.encode())
            total_bytes[mode]+=len(txt)
    if total_bytes['few']!=total_bytes['many']:
        raise AssertionError('Total network bytes changed')
    shas={k:wire[k].hexdigest() for k in modes}
    if shas['few']==shas['many']:
        raise AssertionError('Semantic Map cardinality intervention was not encoded')
    if len(padding_lengths)!=SERIES or min(padding_lengths)!=max(padding_lengths):
        raise AssertionError('Unexpected variable payload pad length across series')
    proof={'common_labels_samples_sha256':common_hash.hexdigest(),
           'wire_sha256':shas,'per_batch_sha256':each,
           'equal_wire_bytes':total_bytes['few'],
           'series':SERIES,'batches':BATCHES,'samples_per_phase':SERIES*BATCHES,
           'input_tags':EXPECTED_INPUT_TAGS,
           'stored_tags':EXPECTED_STORED_TAGS,
           'low_payload_length':padding_lengths[0],
           'high_payload_length':8}
    return blocks,proof


_original_counts=r21.raw_counts
def shape_counts(endpoint,table):
    p=_original_counts(endpoint,table)
    tag_table='default.'+chr(96)+'.inner_id.tags.'+r21.base.table_uuid(endpoint,table)+chr(96)
    sql='SELECT min(length(mapKeys(tags))),max(length(mapKeys(tags))) FROM '+tag_table+' FORMAT TSV'
    lens=r21.base.sql(endpoint,sql).strip().split('\t')
    if len(lens)!=2 or int(lens[0])!=int(lens[1]):
        raise AssertionError('Input tag cardinality varied across logical IDs')
    p['tags']['stored_labels_per_series']=int(lens[0])
    rows=r21.base.sql(endpoint,'SELECT toString(id),toString(tags) FROM '+
                      tag_table+' ORDER BY id FORMAT TSV')
    if len(rows.splitlines())!=SERIES:
        raise AssertionError('Incorrect source-normalized TAGS rows')
    p['tags']['full_id_tags_sha256']=hashlib.sha256(rows.encode()).hexdigest()
    return p
r21.raw_counts=shape_counts


def paired(phases):
    metrics={
        'ingest_process_cpu_s':('ingest','cpu_s'),
        'ingest_process_wall_s':('ingest','wall_s'),
        'ingest_write_bytes':('ingest','write_bytes_delta'),
        'querylog_ingest_cpu_ms':('querylog_ingest_cpu_ms',),
        'native_wall_p50_ms':('native','wall_p50_ms'),
        'native_cpu_p50_ms':('native','cpu_p50_ms'),
        'native_read_bytes_p50':('native','read_bytes_p50'),
        'native_read_rows_p50':('native','read_rows_p50'),
        'raw_tags_bytes':('raw_tags_bytes',),
        'raw_timeranges_bytes':('raw_timeranges_bytes',),
        'raw_samples_bytes':('raw_samples_bytes',)}
    result={}
    for key,path in metrics.items():
        def val(phase):
            y=phases[phase]['stats']
            for attr in path:
                y=y[attr]
            return float(y)
        a,z=val('A0'),val('A3')
        b,c=val('B1'),val('B2')
        few=statistics.mean((a,z))
        many=statistics.mean((b,c))
        if few<=0 or many<=0:
            raise AssertionError('Bad metric values '+key)
        result[key]={'few_mean':round(few,6),'many_mean':round(many,6),
                     'many_over_few':round(many/few,6),
                     'few_AA_drift':round(abs(a-z)/few,6),
                     'few_phase_values':[a,z],'many_phase_values':[b,c]}
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--binary',required=True)
    p.add_argument('--outdir',required=True)
    p.add_argument('--port',type=int,default=18191)
    a=p.parse_args()
    root=pathlib.Path(a.outdir)
    root.mkdir(parents=True,exist_ok=True)
    blocks,proof=fixture()
    out={'schema_version':1,'question_id':QUESTION,
         'subexperiment':'8-versus36-input-tags-exact-equal-wirebytes-stockO1-native-ABBA',
         'source_sha':SOURCE,'binary_sha256':BINARY,
         'fixture':proof,'phases':{},'comparison':{},'errors':[],
         'passed':False,'decision':'INCONCLUSIVE'}
    def save():
        (root/'report.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
    try:
        for phase,mode in PHASES:
            fixture_proof={'ordered_sha256':proof['wire_sha256'][mode],
                           'per_batch_sha256':proof['per_batch_sha256'][mode]}
            result=r21.one_phase(a.binary,root,phase,'large',
                                 blocks[mode],fixture_proof,a.port)
            result['tag_count_mode']=mode
            if result['physical']['tags']['stored_labels_per_series']!=EXPECTED_STORED_TAGS[mode]:
                raise AssertionError('Stored TAGS Map cardinality does not match input intervention')
            if result['stats']['total_cache_hits']!=94208 or result['stats']['total_cache_misses']!=4096:
                raise AssertionError('Cache hits/misses changed with tag Map cardinality')
            if result['physical']['tags']['count']!=SERIES or \
               result['physical']['tags']['distinct_id']!=SERIES or \
               result['physical']['physical_samples_points']!=SERIES*BATCHES:
                raise AssertionError('Unexpected TAGS/SAMPLES physical cardinality')
            out['phases'][phase]=result
            save()
            print('R24_PHASE',json.dumps({'phase':phase,'mode':mode,
               'process_ingest_cpu_s':result['stats']['ingest']['cpu_s'],
               'native_wall_p50_ms':result['stats']['native']['wall_p50_ms'],
               'read_bytes':result['stats']['native']['read_bytes_p50'],
               'tags_storage_bytes':result['stats']['raw_tags_bytes'],
               'cache_hits':result['stats']['total_cache_hits']},
               separators=(',',':')),flush=True)
        for pair in (('A0','A3'),('B1','B2')):
            x,y=(out['phases'][i] for i in pair)
            if x['physical']['tags']['full_id_tags_sha256']!=y['physical']['tags']['full_id_tags_sha256']:
                raise AssertionError('Same tag-count fixture changed identity between control phases')
        if out['phases']['A0']['physical']['tags']['full_id_tags_sha256']==\
           out['phases']['B1']['physical']['tags']['full_id_tags_sha256']:
            raise AssertionError('Different logical tag counts generated identical full ID+tags fingerprint')
        if len({x['native_query_sha256'] for x in out['phases'].values()})!=1:
            raise AssertionError('Native query changed between label-count modes')
        if len({json.dumps(x['native_samples'][0]['labels'],sort_keys=True)
                for x in out['phases'].values()})!=1:
            raise AssertionError('Native output differs between modes')
        out['comparison']=paired(out['phases'])
        for key in ('ingest_process_cpu_s','native_wall_p50_ms','native_cpu_p50_ms'):
            if out['comparison'][key]['few_AA_drift']>.25:
                raise AssertionError('A/A control drift >25% for '+key)
        if abs(out['comparison']['native_read_rows_p50']['many_over_few']-1)>.01:
            raise AssertionError('Read row count changed despite same 4096 IDs and 98304 samples')
        ratio=out['comparison']['ingest_process_cpu_s']['many_over_few']
        threshold=max(.05,out['comparison']['ingest_process_cpu_s']['few_AA_drift'])
        if ratio>1+threshold:
            out['decision']='HIGH_TAG_COUNT_CPU_COST_REPLICATION_NEEDED'
        elif ratio<1-threshold:
            out['decision']='HIGH_TAG_COUNT_CPU_BENEFIT_REPLICATION_NEEDED'
        else:
            out['decision']='NO_ROBUST_TAG_COUNT_CPU_EFFECT'
        out['passed']=True
    except Exception as exc:
        out['errors'].append(str(exc)[:2200])
        out['decision']='INVALID_METHOD_OR_CORRECTNESS_FAIL'
    finally:
        save()
        print('R24_DONE',json.dumps({'passed':out['passed'],
            'decision':out['decision'],'fixture':out['fixture'],
            'comparison':out['comparison'],'errors':out['errors']},
            separators=(',',':')),flush=True)
    if not out['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
