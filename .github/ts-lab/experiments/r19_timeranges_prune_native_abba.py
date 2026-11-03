#!/usr/bin/env python3
"""R19: Same 50k TAGS, same 2k live SAMPLES: is stale TimeRanges the cost?

Source-backed, single-variable TimeRanges-only historical row deletion.
Two independent raw-ID v11 tables have exact same 48k retired and 2k live
ingest streams. Both drop August SAMPLES partition, retaining 50k TAGS;
candidate drops just 48k old TimeRanges. Four native ABBA table read phases
+ independent exact oracle + physical read CPU/IO/parts. No extra index,
no C++ patch, no production mutation. Late resurrection tested afterward.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import statistics
import sys
import time

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r02_small_batch_write_merge as base
import r19_expired_metadata_native as exp

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
BINARY='f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e'
OLD_SHA='fc6ff8bf4ee1842b2f31e787d8cb336801bc59601c98ff249bdb0f6d16232267'
LIVE_SHA='df80189979bdc9ae0ba3ec0c4103f42444d703ef5948cfa5929989ae28b115d6'
FULL='tslab_r19_tr_full'
PRUNED='tslab_r19_tr_pruned'
PHASES=[('A0',FULL),('B1',PRUNED),('B2',PRUNED),('A3',FULL)]
PART_OLD='202608'
PART_LIVE='202610'
CUTOFF='2026-09-01 00:00:00'
WARMUP=3
TIMED=10
CASE=['broad','project_p0']


def physical(endpoint,table):
    kinds={k:exp.inner_name(endpoint,table,k) for k in ('tags','samples','timeranges')}
    return {k:{'rows':exp.count_inner(endpoint,x),
               'parts':exp.active_parts(endpoint,x)}
            for k,x in kinds.items()}


def count_unique(endpoint,inner):
    return int(base.sql(endpoint,
          f'SELECT uniqExact(id) FROM {exp.quoted_inner(inner)} FORMAT TSV').strip())


def check_oracle(kind,body):
    rows=exp.normalize(body,kind)
    expected=exp.actual_oracle()
    if kind=='project_p0':
        expected={'p0':expected['p0']}
    if set(rows)!=set(expected):
        raise AssertionError(f'Unexpected native labels: {rows} versus {expected}')
    for key,val in expected.items():
        if not math.isclose(rows[key],val,rel_tol=1e-11,abs_tol=1e-9):
            raise AssertionError(f'Wrong native value for {key}: {rows[key]} != {val}')
    return rows


def stat_comparison(p):
    out={}
    for field in ('wall_p50_ms','query_cpu_p50_ms',
                  'read_rows_p50','read_bytes_p50',
                  'selected_marks_p50','query_peak_p95_bytes'):
        a=p['A0']['summary'][field]
        z=p['A3']['summary'][field]
        b=p['B1']['summary'][field]
        c=p['B2']['summary'][field]
        control=statistics.mean((a,z))
        alternative=statistics.mean((b,c))
        out[field]={'full':control,'pruned':alternative,
                    'pruned_over_full':alternative/control if control else None,
                    'full_AA_drift':abs(a-z)/control if control else None}
    return out


def check_resume(endpoint):
    # Same original full-ID tags remain; a fresh sample must recreate
    # TimeRanges even if the tags-deduplication cache skips the TAGS write.
    sample=json.loads(exp.row(0,True))
    sample['samples']=[[base.stamp(exp.NOW+120),999.0]]
    payload=json.dumps(sample,sort_keys=True,separators=(',',':'))+'\n'
    base.sql(endpoint,
        f'INSERT INTO {PRUNED}(metric_name,tags,samples) FORMAT JSONEachRow\n'+payload)
    marker={'sample_payload_sha256':hashlib.sha256(payload.encode()).hexdigest()}
    expected={'active':sum(1+(i%97)*.01 for i in range(exp.LIVE))/exp.LIVE,'retired':999.}
    expr=f'avg by(lifecycle)(avg_over_time({exp.METRIC}[1d]))'
    q=(f'SELECT * FROM prometheusQueryRange({PRUNED},{base.q(expr)},'
       f'{exp.END},{exp.END},60) SETTINGS max_threads=2,use_query_cache=0 FORMAT JSONEachRow')
    body=base.sql(endpoint,q)
    groups={}
    for line in body.splitlines():
        if not line.strip():
            continue
        row=json.loads(line)
        tags=dict(row['tags'])
        if set(tags)!={'lifecycle'} or len(row['samples'])!=1:
            raise AssertionError('Wrong fresh resumed native labels/samples')
        ts,val=row['samples'][0]
        unix=int(dt.datetime.fromisoformat(ts.replace(' ','T'))
             .replace(tzinfo=dt.timezone.utc).timestamp())
        if unix!=exp.END or tags['lifecycle'] in groups:
            raise AssertionError('Resumed old timestamp or duplicate')
        groups[tags['lifecycle']]=float(val)
    if set(groups)!=set(expected):
        raise AssertionError('Resumed old identity absent from native output')
    for key,value in expected.items():
        if not math.isclose(groups[key],value,rel_tol=1e-11,abs_tol=1e-9):
            raise AssertionError(f'Resumed native values failed {key}: {groups[key]} != {value}')
    p=physical(endpoint,PRUNED)
    if p['tags']['rows']!=50000 or p['timeranges']['rows']!=2001 or p['samples']['rows']!=2001:
        raise AssertionError('Resumed old series did not restore TimeRanges without deleting TAGS')
    marker.update({'native':groups,'sample_rows':p['samples']['rows'],
                    'tags_rows':p['tags']['rows'],
                    'timeranges_rows':p['timeranges']['rows'],
                    'sql_sha256':hashlib.sha256(q.encode()).hexdigest()})
    return marker


def main():
    arg=argparse.ArgumentParser()
    arg.add_argument('--endpoint',default='http://127.0.0.1:18191/')
    arg.add_argument('--out',required=True)
    opts=arg.parse_args()
    outpath=pathlib.Path(opts.out)
    outpath.parent.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,
      'question_id':'R19-expired-samples-retained-tags-timeranges-v1',
      'subexperiment':'retired-TimeRanges-only-full-native-read-ABBA-v1',
      'source_sha':SOURCE,'binary_sha256':BINARY,
      'fixture':{'retired':exp.RETIRED,'live':exp.LIVE,
                 'expected_old_sha256':OLD_SHA,
                 'expected_live_sha256':LIVE_SHA},
      'tables':{},'physical':{},'trials':[],
      'phases':{},'comparisons':{},'explain':{},'errors':[],
      'passed':False,'scientific_decision':'INCONCLUSIVE'}
    def save():
        outpath.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    try:
        for table in (FULL,PRUNED):
            ddl=base.tables_ddl('unused',table)[1]
            base.sql(opts.endpoint,ddl)
            h_old,h_live=hashlib.sha256(),hashlib.sha256()
            exp.insert(opts.endpoint,table,'retired',0,exp.RETIRED,h_old)
            exp.insert(opts.endpoint,table,'live',0,exp.LIVE,h_live)
            if h_old.hexdigest()!=OLD_SHA or h_live.hexdigest()!=LIVE_SHA:
                raise AssertionError('R19 fixture changed; reject non-pinned test')
            report['tables'][table]={'ddl_sha256':hashlib.sha256(ddl.encode()).hexdigest(),
                'retired_sha256':h_old.hexdigest(),'live_sha256':h_live.hexdigest()}
            for kind in ('tags','samples','timeranges'):
                base.sql(opts.endpoint,'OPTIMIZE TABLE '+
                    exp.quoted_inner(exp.inner_name(opts.endpoint,table,kind))+
                    ' FINAL SETTINGS optimize_throw_if_noop=0')
            old_part=exp.active_parts(opts.endpoint,exp.inner_name(opts.endpoint,table,'samples'))
            if {p['partition_id'] for p in old_part}!={PART_OLD,PART_LIVE}:
                raise AssertionError(f'Unexpected monthly sample partitions {old_part}')
            base.sql(opts.endpoint,'ALTER TABLE '+
                exp.quoted_inner(exp.inner_name(opts.endpoint,table,'samples'))+
                ' DROP PARTITION ID '+base.q(PART_OLD))
        if count_unique(opts.endpoint,exp.inner_name(opts.endpoint,FULL,'tags'))!=50000 or \
           count_unique(opts.endpoint,exp.inner_name(opts.endpoint,PRUNED,'tags'))!=50000:
            raise AssertionError('Logical TAGS IDs changed before TimeRanges pruning')
        before=physical(opts.endpoint,PRUNED)
        if before['samples']['rows']!=2000 or before['timeranges']['rows']!=50000:
            raise AssertionError('Historical rows not present before TimeRanges mutation')
        predicate=(f"max_time < toDateTime64('{CUTOFF}', 3, 'UTC')")
        mutation=('ALTER TABLE '+
            exp.quoted_inner(exp.inner_name(opts.endpoint,PRUNED,'timeranges'))+
            ' DELETE WHERE '+predicate+' SETTINGS mutations_sync=2')
        report['mutation_sha256']=hashlib.sha256(mutation.encode()).hexdigest()
        base.sql(opts.endpoint,mutation)
        base.sql(opts.endpoint,'OPTIMIZE TABLE '+
            exp.quoted_inner(exp.inner_name(opts.endpoint,PRUNED,'timeranges'))+
            ' FINAL SETTINGS optimize_throw_if_noop=0')
        report['physical']={table:physical(opts.endpoint,table) for table in (FULL,PRUNED)}
        a=report['physical'][FULL];b=report['physical'][PRUNED]
        for t in (FULL,PRUNED):
            p=report['physical'][t]
            if p['samples']['rows']!=2000 or p['tags']['rows']!=50000:
                raise AssertionError('Active samples and original TAGS must be identical')
            if {q['partition_id'] for q in p['samples']['parts']}!={PART_LIVE}:
                raise AssertionError('Old SAMPLES physical partition not deleted')
        if a['timeranges']['rows']!=50000 or b['timeranges']['rows']!=2000:
            raise AssertionError('TimeRanges only old rows were not pruned')
        if sum(x['bytes'] for x in a['samples']['parts']) != sum(x['bytes'] for x in b['samples']['parts']):
            raise AssertionError('Active SAMPLES compressed bytes differ between tables')
        if sum(x['bytes'] for x in a['tags']['parts']) != sum(x['bytes'] for x in b['tags']['parts']):
            raise AssertionError('Original TAGS compressed bytes differ between tables')
        for table in (FULL,PRUNED):
            for kind in ('tags','samples','timeranges'):
                base.sql(opts.endpoint,'SYSTEM STOP MERGES '+
                         exp.quoted_inner(exp.inner_name(opts.endpoint,table,kind)))
        save()
        for kind in CASE:
            report['phases'][kind]={}
            for phase,table in PHASES:
                sql=exp.query(table,kind)
                try:
                    report['explain'][f'{kind}/{phase}']=base.sql(opts.endpoint,'EXPLAIN PLAN '+sql)[:15000]
                except Exception as exc:
                    report['explain'][f'{kind}/{phase}']={'error':str(exc)[:900]}
                rows=[]
                for n in range(WARMUP+TIMED):
                    qid=f'r19_tr_{kind}_{phase}_{n:02d}'
                    t0=time.perf_counter_ns()
                    body=base.sql(opts.endpoint,sql,qid)
                    elapsed=(time.perf_counter_ns()-t0)/1e6
                    native=check_oracle(kind,body)
                    rows.append({'phase':phase,'table':table,'kind':kind,
                      'id':qid,'warmup':n<WARMUP,'client_ms':round(elapsed,4),
                      'native':native,'query_sha256':hashlib.sha256(sql.encode()).hexdigest()})
                log=exp.query_logs(opts.endpoint,[x['id'] for x in rows])
                for row in rows:
                    q=log[row['id']]
                    if q['exception_code']:
                        raise AssertionError('Native QueryFinish failed '+row['id'])
                    row.update({'server_ms':q['query_duration_ms'],
                        'cpu_ms':q['ProfileEvents'].get('OSCPUVirtualTimeMicroseconds',0)/1000,
                        'read_rows':q['read_rows'],'read_bytes':q['read_bytes'],
                        'selected_marks':q['ProfileEvents'].get('SelectedMarks',0),
                        'peak_bytes':q['memory_usage']})
                    if row['cpu_ms']<=0:
                        raise AssertionError('Missing CPU duration '+row['id'])
                report['trials']+=rows
                report['phases'][kind][phase]={'table':table,'summary':exp.phase_summary(rows)}
                save()
                print('R19_RANGES_READ_PHASE',json.dumps({
                  'case':kind,'phase':phase,
                  'summary':report['phases'][kind][phase]['summary']},
                  separators=(',',':')),flush=True)
            c=stat_comparison(report['phases'][kind])
            report['comparisons'][kind]=c
            if any(c[field]['full_AA_drift']>.25
                   for field in ('wall_p50_ms','query_cpu_p50_ms')):
                raise AssertionError(f'{kind}: control drift >25%')
        report['resurrection']=check_resume(opts.endpoint)
        broad=report['comparisons']['broad']
        wall=broad['wall_p50_ms']
        cpu=broad['query_cpu_p50_ms']
        if (wall['pruned_over_full'] < 1-max(.05,wall['full_AA_drift']) and
            cpu['pruned_over_full'] < 1-max(.05,cpu['full_AA_drift']) and
            broad['read_rows_p50']['pruned_over_full']<.75):
            report['scientific_decision']='ACCEPT_FOR_NEXT_GATE'
        else:
            report['scientific_decision']='NO_ROBUST_BROAD_IMPROVEMENT'
        report['passed']=True
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
        report['scientific_decision']='INVALID_METHOD_OR_CORRECTNESS_FAIL'
    finally:
        save()
        print('R19_RANGES_NATIVE_DONE',json.dumps({'passed':report['passed'],
          'decision':report['scientific_decision'],
          'physical':{t:{k:v['rows'] for k,v in p.items()}
                     for t,p in report['physical'].items()},
          'comparisons':report['comparisons'],
          'resurrection':report.get('resurrection'),
          'errors':report['errors']},separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
