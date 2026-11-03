#!/usr/bin/env python3
"""R19 source-backed TimeSeries metadata-retention and native query-cost experiment.

Stock v11 RAW_UUID ClickHouse source explicitly says TAGS has no default TTL
after SAMPLES disappear. Create one clean 2k-active-series table and another
with exactly the same active series PLUS 48k retired identities. Physically
DROP only aged SAMPLES monthly partition, as an exact deterministic model of
sample retention, NOT a claim that asynchronous TTL itself was exercised.
Measure exact native PromQL and metadata reads through same-server ABBA.
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
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import r02_small_batch_write_merge as base

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
METRIC='tslab_r19_retention'
CLEAN='tslab_r19_clean'
AGED='tslab_r19_retained'
RETIRED=48000
LIVE=2000
OLD=int(dt.datetime(2026,8,10,12,tzinfo=dt.timezone.utc).timestamp())
NOW=int(dt.datetime(2026,10,20,12,tzinfo=dt.timezone.utc).timestamp())
END=NOW+300
PHASES=[('A0',CLEAN),('B1',AGED),('B2',AGED),('A3',CLEAN)]
WARMUP=3
TIMED=10


def row(i,old):
    tags={'project':f'p{i%4}', 'case_id':f'{"old" if old else "live"}-{i:06d}',
          'lifecycle':'retired' if old else 'active'}
    value=float(1+(i%97)*.01 + (50 if old else 0))
    sample_time=OLD if old else NOW
    return json.dumps({'metric_name':METRIC,'tags':tags,
       'samples':[[base.stamp(sample_time),value]]},
       sort_keys=True,separators=(',',':'))+'\n'


def insert(endpoint,table,rows,start,end,digest):
    for i in range(start,end,2000):
        block=''.join(row(k,rows=='retired') for k in range(i,min(i+2000,end)))
        digest.update(block.encode())
        base.sql(endpoint,f'INSERT INTO {table}(metric_name,tags,samples) FORMAT JSONEachRow\n'+block)


def inner_name(endpoint,table,kind):
    uuid=base.table_uuid(endpoint,table)
    return '.inner_id.'+kind+'.'+uuid


def quoted_inner(inner):
    return 'default.'+chr(96)+inner+chr(96)


def count_inner(endpoint,inner):
    return int(base.sql(endpoint,f'SELECT count() FROM {quoted_inner(inner)} FORMAT TSV').strip())


def active_parts(endpoint,inner):
    return base.jsonrows(endpoint,
        "SELECT partition_id, sum(rows) AS rows,sum(marks) AS marks,"
        "sum(bytes_on_disk) AS bytes,count() AS parts "
        "FROM system.parts WHERE active AND database='default' "
        f"AND table={base.q(inner)} GROUP BY partition_id ORDER BY partition_id")


def actual_oracle():
    groups={f'p{k}':[] for k in range(4)}
    for i in range(LIVE):
        groups[f'p{i%4}'].append(1+(i%97)*.01)
    return {k:sum(v)/len(v) for k,v in groups.items()}


def selector(name):
    suffix='' if name=='broad' else '{project="p0"}'
    return f'avg by(project)(avg_over_time({METRIC}{suffix}[1d]))'


def query(table,kind):
    return (f'SELECT * FROM prometheusQueryRange({table},{base.q(selector(kind))},'
            f'{END},{END},60) SETTINGS max_threads=2,use_query_cache=0 FORMAT JSONEachRow')


def normalize(body,kind):
    wanted=actual_oracle()
    if kind=='project_p0':
        wanted={'p0':wanted['p0']}
    output={}
    for line in body.splitlines():
        if not line:
            continue
        result=json.loads(line)
        tags=dict(result['tags'])
        if set(tags)!={'project'} or len(result['samples'])!=1:
            raise AssertionError('Unexpected native label or sample shape: '+str(result)[:300])
        stamp,val=result['samples'][0]
        parsed=int(dt.datetime.fromisoformat(stamp.replace(' ','T'))
                   .replace(tzinfo=dt.timezone.utc).timestamp())
        if parsed!=END or tags['project'] in output:
            raise AssertionError('Unexpected native timestamp or duplicate project')
        output[tags['project']]=float(val)
    if set(output)!=set(wanted):
        raise AssertionError('Project labels mismatch '+repr((output,wanted)))
    for k,v in wanted.items():
        if not math.isclose(output[k],v,abs_tol=1e-9,rel_tol=1e-11):
            raise AssertionError(f'Wrong mean {k}: got {output[k]}, expected {v}')
    return output


def percentile(seq,p):
    ordered=sorted(seq)
    if not ordered:
        return None
    x=(len(ordered)-1)*p
    i=int(math.floor(x))
    j=int(math.ceil(x))
    return round(ordered[i]+(ordered[j]-ordered[i])*(x-i),4)


def query_logs(endpoint,ids):
    base.sql(endpoint,'SYSTEM FLUSH LOGS')
    for attempt in range(8):
        arr=base.jsonrows(endpoint,
          "SELECT query_id,query_duration_ms,ProfileEvents,read_rows,read_bytes,"
          "memory_usage,exception_code FROM system.query_log "
          "WHERE type='QueryFinish' AND startsWith(query_id,'r19_')")
        found={x['query_id']:x for x in arr}
        if all(i in found for i in ids):
            return found
        time.sleep(.2*(attempt+1))
    raise AssertionError('Missing query logs '+repr(sorted(set(ids)-set(found))[:8]))


def phase_summary(trials):
    records=[x for x in trials if not x['warmup']]
    return {'count':len(records),'wall_p50_ms':percentile([x['client_ms'] for x in records],.5),
        'wall_p95_ms':percentile([x['client_ms'] for x in records],.95),
        'query_cpu_p50_ms':percentile([x['cpu_ms'] for x in records],.5),
        'query_duration_p50_ms':percentile([x['server_ms'] for x in records],.5),
        'read_rows_p50':percentile([x['read_rows'] for x in records],.5),
        'read_bytes_p50':percentile([x['read_bytes'] for x in records],.5),
        'query_peak_p95_bytes':percentile([x['peak_bytes'] for x in records],.95),
        'selected_marks_p50':percentile([x['selected_marks'] for x in records],.5)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--endpoint',default='http://127.0.0.1:18191/')
    p.add_argument('--out',required=True)
    args=p.parse_args()
    dest=pathlib.Path(args.out)
    dest.parent.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'question_id':'R19-expired-samples-retained-tags-timeranges-v1',
      'source_sha':SOURCE,'retired':RETIRED,'live':LIVE,'old_epoch':OLD,'new_epoch':NOW,
      'samples_deleted_by':'explicit DROP PARTITION ID of aged August SAMPLES; NOT an asynchronous TTL operation',
      'input_sha256':{},'physical':{},'trials':[],'phases':{},'comparisons':{},
      'errors':[],'passed':False,'scientific_decision':'INCONCLUSIVE'}
    def save():
        dest.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    try:
        for table in (CLEAN,AGED):
            raw=base.tables_ddl('unused',table)[1]
            base.sql(args.endpoint,raw)
        oldhash=hashlib.sha256()
        livehash=hashlib.sha256()
        livecopy=hashlib.sha256()
        insert(args.endpoint,AGED,'retired',0,RETIRED,oldhash)
        insert(args.endpoint,CLEAN,'live',0,LIVE,livehash)
        insert(args.endpoint,AGED,'live',0,LIVE,livecopy)
        if livehash.digest()!=livecopy.digest():
            raise AssertionError('Live input bytes differ in clean and aged tables')
        report['input_sha256']={'retired':oldhash.hexdigest(),
                                 'live_both':livehash.hexdigest()}
        inner={table:{kind:inner_name(args.endpoint,table,kind)
                for kind in ('tags','samples','timeranges')} for table in (CLEAN,AGED)}
        report['physical']['before']={table:{
            kind: {'count':count_inner(args.endpoint,x), 'parts':active_parts(args.endpoint,x)}
            for kind,x in kinds.items()} for table,kinds in inner.items()}
        if count_inner(args.endpoint,inner[AGED]['samples']) != RETIRED+LIVE:
            raise AssertionError('Historical samples did not ingest before retirement')
        for table in (CLEAN,AGED):
            for kind,x in inner[table].items():
                base.sql(args.endpoint,f'OPTIMIZE TABLE {quoted_inner(x)} FINAL SETTINGS optimize_throw_if_noop=0')
        prior=active_parts(args.endpoint,inner[AGED]['samples'])
        old_partition=str(dt.datetime.fromtimestamp(OLD,dt.timezone.utc).year*100+
                          dt.datetime.fromtimestamp(OLD,dt.timezone.utc).month)
        live_partition=str(dt.datetime.fromtimestamp(NOW,dt.timezone.utc).year*100+
                           dt.datetime.fromtimestamp(NOW,dt.timezone.utc).month)
        partitions={x['partition_id'] for x in prior}
        if partitions!={old_partition,live_partition}:
            raise AssertionError(f'Unexpected SAMPLES monthly partition IDs {partitions}')
        # This models sample retention exactly, without depending on background
        # TTL scheduling and without touching any external data.
        base.sql(args.endpoint,f'ALTER TABLE {quoted_inner(inner[AGED]["samples"])} '
                 f'DROP PARTITION ID {base.q(old_partition)}')
        report['physical']['dropped_partition_id']=old_partition
        report['physical']['live_partition_id']=live_partition
        report['physical']['after']={table:{
            kind: {'count':count_inner(args.endpoint,x), 'parts':active_parts(args.endpoint,x)}
            for kind,x in kinds.items()} for table,kinds in inner.items()}
        after=report['physical']['after']
        for table in (CLEAN,AGED):
            if after[table]['samples']['count']!=LIVE:
                raise AssertionError('Live sample points do not match')
        if after[CLEAN]['tags']['count']!=LIVE or after[CLEAN]['timeranges']['count']!=LIVE:
            raise AssertionError('Clean metadata count mismatch')
        if after[AGED]['tags']['count']!=RETIRED+LIVE or \
            after[AGED]['timeranges']['count']!=RETIRED+LIVE:
            raise AssertionError('Old TAGS/TimeRanges did not persist: '+repr(after[AGED]))
        if {x['partition_id'] for x in after[AGED]['samples']['parts']}!={live_partition}:
            raise AssertionError('Historical samples were not fully deleted')
        for table in (CLEAN,AGED):
            for x in inner[table].values():
                base.sql(args.endpoint,f'SYSTEM STOP MERGES {quoted_inner(x)}')
        report['physical']['active_sample_bytes']={
            table:sum(int(x['bytes']) for x in after[table]['samples']['parts'])
            for table in (CLEAN,AGED)}
        for kind in ('broad','project_p0'):
            report['phases'][kind]={}
            for phase,table in PHASES:
                q=query(table,kind)
                try:
                    plan=base.sql(args.endpoint,'EXPLAIN PLAN '+q)
                    report.setdefault('explain',{})[f'{kind}/{phase}']=plan[:20000]
                except Exception as exc:
                    report.setdefault('explain_errors',{})[f'{kind}/{phase}']=str(exc)[:900]
                this=[]
                for trial in range(WARMUP+TIMED):
                    qid=f'r19_{kind}_{phase}_{trial:02d}'
                    t0=time.perf_counter_ns()
                    body=base.sql(args.endpoint,q,qid)
                    client_ms=(time.perf_counter_ns()-t0)/1e6
                    values=normalize(body,kind)
                    this.append({'kind':kind,'phase':phase,'table':table,'trial':trial,
                      'warmup':trial<WARMUP,'id':qid,
                      'client_ms':round(client_ms,4),'values':values,
                      'query_sha256':hashlib.sha256(q.encode()).hexdigest()})
                logs=query_logs(args.endpoint,[x['id'] for x in this])
                for item in this:
                    entry=logs[item['id']]
                    if entry['exception_code']!=0:
                        raise AssertionError('QueryFinish returned error for '+item['id'])
                    item['server_ms']=int(entry['query_duration_ms'])
                    item['cpu_ms']=entry['ProfileEvents'].get('OSCPUVirtualTimeMicroseconds',0)/1000
                    item['read_rows']=int(entry['read_rows'])
                    item['read_bytes']=int(entry['read_bytes'])
                    item['peak_bytes']=int(entry['memory_usage'])
                    item['selected_marks']=entry['ProfileEvents'].get('SelectedMarks',0)
                    if item['cpu_ms']<=0:
                        raise AssertionError('Missing query CPU for '+item['id'])
                report['trials']+=this
                report['phases'][kind][phase]={'table':table,'summary':phase_summary(this)}
                save()
                print('R19_PHASE',json.dumps({'kind':kind,'phase':phase,
                    **report['phases'][kind][phase]['summary']},separators=(',',':')),flush=True)
            a=report['phases'][kind]['A0']['summary']
            z=report['phases'][kind]['A3']['summary']
            b=report['phases'][kind]['B1']['summary']
            c=report['phases'][kind]['B2']['summary']
            metric={}
            for key in ('wall_p50_ms','query_cpu_p50_ms','read_rows_p50','read_bytes_p50'):
                baseline=statistics.mean([a[key],z[key]])
                other=statistics.mean([b[key],c[key]])
                drift=abs(a[key]-z[key])/baseline if baseline else math.inf
                metric[key]={'clean':baseline,'retained':other,
                              'retained_over_clean':other/baseline if baseline else None,
                              'clean_AA_drift':drift}
            report['comparisons'][kind]=metric
            if any(metric[key]['clean_AA_drift']>.25
                   for key in ('wall_p50_ms','query_cpu_p50_ms')):
                raise AssertionError(f'{kind}: A/A CPU or wall control drift >25%')
        report['passed']=True
        # Pass means factual retention and correct, controlled measurement.
        # Do not automatically call an observed effect a speedup/regression.
        broad=report['comparisons']['broad']
        wc=broad['wall_p50_ms']
        cpu=broad['query_cpu_p50_ms']
        effect=max(.05,wc['clean_AA_drift'],cpu['clean_AA_drift'])
        if wc['retained_over_clean']>1+effect and cpu['retained_over_clean']>1+effect:
            report['scientific_decision']='ACCEPT_FOR_NEXT_GATE'
        else:
            report['scientific_decision']='NO_ROBUST_BROAD_REGRESSION'
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
        report['scientific_decision']='INVALID_METHOD_OR_CORRECTNESS_FAIL'
    finally:
        save()
        print('R19_DONE',json.dumps({'passed':report['passed'],
            'decision':report['scientific_decision'],
            'physical':report['physical'].get('after',{}),
            'comparisons':report['comparisons'],
            'errors':report['errors']},separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
