#!/usr/bin/env python3
"""R02 strict three-runner closed-loop native PromQL + INSERT replication.

Read-only reducer for exact GitHub artifacts. Emits within-runner p95 ratios,
stratified read latency by insert progress and a separate CPU decision.
A run with valid data is NOT proof of overall performance improvement.
"""
import argparse,hashlib,json,math,pathlib,statistics,zipfile

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
FIXTURE='dd2dd55ee8fcf3c11c1fef94ebdd54ac8b3a6a53cea6deaae991b3b94c88b931'
MANIFEST_SHA='712ac6f17f298a4b73542e56b95417e98cf67f3fab5b3f602dd22d1c4356d822'
MODES={'A0':'lc','B1':'raw','B2':'raw','A3':'lc'}
METRICS=['native_read.p95_ms','mixed_read_write_server_process.cpu_s',
         'mixed_read_write_server_process.wall_s']
FIXED_EXPECTED={'stable':147.995,'rotating':247.4}


def mean(values):
    return statistics.mean(values)


def load(p):
    p=pathlib.Path(p)
    binary_artifact_digest=hashlib.sha256(p.read_bytes()).hexdigest()
    with zipfile.ZipFile(p) as z:
        names=set(z.namelist())
        mandatory={'report.json','build-info.txt','script-sha256.txt','archive-verify.txt',
                   'binary-verify.txt','workflow-head.txt','console.log'}
        if mandatory - names:
            raise AssertionError(f'{p}: incomplete {mandatory-names}')
        report=json.loads(z.read('report.json'))
        build=z.read('build-info.txt').decode()
        scripts=z.read('script-sha256.txt').decode()
        archive=z.read('archive-verify.txt').decode()
        binary=z.read('binary-verify.txt').decode()
        commit=z.read('workflow-head.txt').decode().strip()
        if SOURCE not in build or 'optimization=O1 unmodified stock source no lab patches' not in build:
            raise AssertionError(f'{p}: wrong build')
        if 'clickhouse: OK' not in binary or 'clickhouse-stock-clickhouse-o1-linux-amd64.tar.zst: OK' not in archive:
            raise AssertionError(f'{p}: missing verified executable or compressed archive SHA')
        if MANIFEST_SHA not in scripts or 'r02_live_read_write_abba.py' not in scripts:
            raise AssertionError(f'{p}: source manifest mismatch')
        if len(commit)!=40:
            raise AssertionError(f'{p}: invalid workflow checkout sha')
        if not report.get('passed') or report.get('errors'):
            raise AssertionError(f'{p}: invalid report')
        if report.get('source_sha')!=SOURCE or report.get('fixture_sha256')!=FIXTURE:
            raise AssertionError(f'{p}: fixture/source SHA mismatch')
        if set(report.get('phases',{}))!=set(MODES):
            raise AssertionError(f'{p}: incomplete phases')
        phase_out={}
        for name,mode in MODES.items():
            entry=report['phases'][name]
            if entry['mode']!=mode or not entry['passed'] or entry['errors'] or entry['reader_errors']:
                raise AssertionError(f'{p}: invalid phase {name}')
            if len(entry.get('insert_records',[]))!=84 or len(entry.get('input_sha256',[]))!=96:
                raise AssertionError(f'{p}: incorrect insert count {name}')
            if entry['physical']['physical_sample_points']!=384000:
                raise AssertionError(f'{p}: missing physical points {name}')
            if len(entry.get('part_log',{}).get('events',[]))<5:
                raise AssertionError(f'{p}: no observed natural background merges')
            if entry.get('query_sha256')!=report['phases']['A0']['query_sha256']:
                raise AssertionError(f'{p}: different queries within phases')
            reads=entry.get('valid_reader_samples',[])
            if len(reads)<12 or entry['metrics']['native_read']['count']!=len(reads):
                raise AssertionError(f'{p}: too few fully overlapping reads {name}')
            if any(rec.get('native')!=FIXED_EXPECTED for rec in reads):
                raise AssertionError(f'{p}: native oracle mismatch {name}')
            if any(rec.get('cpu_ms',0)<=0 or rec.get('wall_ms',0)<=0 for rec in reads):
                raise AssertionError(f'{p}: missing native CPU/wall {name}')
            quarters=entry.get('read_progress_quartiles',{})
            if set(quarters)!={'0','1','2','3'} or any(quarters[i]['count']<8 for i in quarters):
                raise AssertionError(f'{p}: unbalanced/missing growth progress {name}')
            if sum(q['count'] for q in quarters.values())!=len(reads):
                raise AssertionError(f'{p}: progress buckets incomplete')
            phase_out[name]=entry
        result={'artifact_file':str(p),'zip_sha256':binary_artifact_digest,'git_head':commit,
                'script_manifest':scripts,'phases':phase_out,'comparison':report['comparison']}
        if not result['comparison'].get('method_valid'):
            raise AssertionError(f'{p}: internal method gate failed')
        for metric in METRICS:
            entry=result['comparison']['metrics'][metric]
            if entry['lc_control_drift']>.25:
                raise AssertionError(f'{p}: A/A drift >25% for {metric}')
        return result


def reduce(records):
    if len(records)!=3 or len(set(r['zip_sha256'] for r in records))!=3:
        raise AssertionError('Require three distinct GitHub runner artifacts')
    if len({r['script_manifest'] for r in records})!=1:
        raise AssertionError('Scripts differ between independent runner jobs')
    result={'schema_version':1,'question_id':'R02-small-batch-raw-lc-write-v1',
            'subexperiment':'concurrent-native-with-natural-merge-3-runner-reduction',
            'source_sha':SOURCE,'fixture_sha256':FIXTURE,'script_sha256':MANIFEST_SHA,
            'artifacts':[{'zip_sha256':r['zip_sha256'],'checkout_head':r['git_head'],
                          'file':r['artifact_file']} for r in records],
            'comparisons':{},'growth_quartiles':{},'decisions':{},
            'passed':True}
    for metric in METRICS:
        items=[r['comparison']['metrics'][metric] for r in records]
        ratios=[x['raw_over_lc'] for x in items]
        drift=[x['lc_control_drift'] for x in items]
        effect=[(1-v)>max(.05,d) for v,d in zip(ratios,drift)]
        result['comparisons'][metric]={'raw_over_lc_per_runner':ratios,
                 'geomean_raw_over_lc':round(math.exp(mean(math.log(x) for x in ratios)),5),
                 'lc_control_drift_per_runner':drift,'replicated_reduction':all(effect)}
    for q in ('0','1','2','3'):
        out=[]
        for record in records:
            ph=record['phases']
            a=mean(ph[k]['read_progress_quartiles'][q]['p95_ms'] for k in ('A0','A3'))
            b=mean(ph[k]['read_progress_quartiles'][q]['p95_ms'] for k in ('B1','B2'))
            out.append({'lc_mean_p95_ms':round(a,4),'raw_mean_p95_ms':round(b,4),
                        'raw_over_lc':round(b/a,5),
                        'counts':{k:ph[k]['read_progress_quartiles'][q]['count'] for k in MODES}})
        result['growth_quartiles'][q]={
            'per_runner':out,'replicated_raw_p95_reduction':all(x['raw_over_lc']<.95 for x in out),
            'geomean_raw_over_lc':round(math.exp(mean(math.log(x['raw_over_lc']) for x in out)),5)}
    result['decisions']['read_p95']=('REPLICATED_LAB_EFFECT' if
             result['comparisons']['native_read.p95_ms']['replicated_reduction'] else 'INCONCLUSIVE')
    result['decisions']['mixed_cpu']=('REPLICATED_LAB_EFFECT' if
             result['comparisons']['mixed_read_write_server_process.cpu_s']['replicated_reduction'] else 'INCONCLUSIVE')
    result['decisions']['later_growth_quartile_p95']=('REPLICATED_LAB_EFFECT' if
             all(result['growth_quartiles'][i]['replicated_raw_p95_reduction'] for i in ('2','3')) else 'INCONCLUSIVE')
    result['limitations']=[
        'Reader is closed-loop: faster modes issue more full native queries during the insert window (not same QPS).',
        'p95 distributions have 58-83 requests per phase, different phase sample counts and different temporal weighting.',
        'Growth quartile p95 has only 11-23 samples; direction can be tested but p95 precision is limited.',
        'CPU covers natural writes+merges+reads and cannot be attributed solely to one operator.',
        'Same stock LLVM22 O1 glibc binary, synthetic 96-batch fixture and hosted runners; not production performance.',
        'No fixed-rate query load. A new synchronized progress-triggered experiment is needed to remove query-count bias.',
    ]
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact',action='append',required=True)
    parser.add_argument('--output',required=True)
    opts=parser.parse_args()
    if len(opts.artifact)!=3:
        parser.error('Pass original run plus two independent-runner ZIPs')
    data=reduce([load(x) for x in opts.artifact])
    target=pathlib.Path(opts.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
    print('R02_LIVE_THREE_RUNNER',json.dumps({'passed':data['passed'],
        'decisions':data['decisions'],'ratios':data['comparisons'],
        'growth':{k:{'ratio':v['geomean_raw_over_lc'],
                     'replicated':v['replicated_raw_p95_reduction']}
                  for k,v in data['growth_quartiles'].items()}},
        separators=(',',':')),flush=True)


if __name__=='__main__':
    main()
