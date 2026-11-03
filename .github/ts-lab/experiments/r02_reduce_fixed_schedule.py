#!/usr/bin/env python3
"""Independent two-GitHub-runner fixed-arrival R02 native TimeSeries reducer.

Only validated independent 41634084470 jobs are included. The earlier
41633619697 has invalid A/A native p95 control drift (26.3%) and MUST NOT be
pooled with these results. Validate all physical, schedule, oracle and SHA
gates before reporting per-runner ratios or a scientific decision.
"""
import argparse
import hashlib
import json
import math
import pathlib
import statistics
import zipfile

SOURCE='52529f0c711566d696917678bf15e7068772fdde'
BINARY='f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e'
FIXTURE='dd2dd55ee8fcf3c11c1fef94ebdd54ac8b3a6a53cea6deaae991b3b94c88b931'
PHASES={'A0':'lc','B1':'raw','B2':'raw','A3':'lc'}
ORACLE={'stable':147.995,'rotating':247.4}
MAX_DRIFT=.25
N_READ=40
N_INSERT=84
N_POINTS=384000


def read_zip(path):
    path=pathlib.Path(path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as z:
        names=set(z.namelist())
        mandatory={'report.json','build-info.txt','script-sha256.txt',
                   'archive-verify.txt','binary-verify.txt','workflow-head.txt','console.log'}
        if not mandatory<=names:
            raise AssertionError(f'{path}: incomplete artifact: {mandatory-names}')
        d=json.loads(z.read('report.json'))
        build=z.read('build-info.txt').decode()
        scripts=z.read('script-sha256.txt').decode()
        verify=z.read('binary-verify.txt').decode()
        archive=z.read('archive-verify.txt').decode()
        workflow_sha=z.read('workflow-head.txt').decode().strip()
        if SOURCE not in build or 'optimization=O1 unmodified stock source no lab patches' not in build:
            raise AssertionError(f'{path}: ClickHouse build mismatch')
        if 'clickhouse: OK' not in verify or 'OK' not in archive:
            raise AssertionError(f'{path}: SHA checks missing')
        if not all(x in scripts for x in ('r02_small_batch_write_merge.py',
                'r02_process_cpu_abba.py','r02_fixed_schedule_read_write.py')):
            raise AssertionError(f'{path}: experiment script hashes missing')
        if len(workflow_sha)!=40:
            raise AssertionError(f'{path}: workflow SHA not stored')
    if not d.get('passed') or d.get('errors') or d.get('source_sha')!=SOURCE:
        raise AssertionError(f'{path}: failed study or wrong source')
    if d.get('fixture_sha256')!=FIXTURE:
        raise AssertionError(f'{path}: fixture SHA mismatch')
    if set(d.get('phases',{}))!=set(PHASES):
        raise AssertionError(f'{path}: incomplete phases')
    if d.get('comparison',{}).get('method_valid') is not True:
        raise AssertionError(f'{path}: invalid A/A control')
    qhashes=set()
    input_hashes=set()
    phase_points=[]
    for phase,mode in PHASES.items():
        p=d['phases'][phase]
        if p.get('mode')!=mode or not p.get('passed') or p.get('errors') or p.get('reader_errors'):
            raise AssertionError(f'{path}: incorrect phase: {phase}')
        if len(p.get('insert_records',[]))!=N_INSERT or len(p.get('valid_reader_samples',[]))!=N_READ:
            raise AssertionError(f'{path}: wrong offered traffic counts: {phase}')
        if len(p.get('reader_samples',[]))!=N_READ or p['metrics']['native_read']['count']!=N_READ:
            raise AssertionError(f'{path}: missed native requests: {phase}')
        config=p['schedule']
        if (abs(config['insert_cadence_s']-.2)>1e-12 or
            abs(config['read_cadence_s']-.4)>1e-12 or
            config['read_count']!=N_READ or
            config['max_read_late_ms']>75 or
            config['max_write_late_ms']>75 or
            config['max_progress_batch_deviation']>2):
            raise AssertionError(f'{path}: open-loop schedule violation: {phase}')
        for q in p['valid_reader_samples']:
            if q['schedule_late_ms']>75 or q['native'] is None or q['schedule_index']>=N_READ:
                raise AssertionError(f'{path}: invalid full native result: {phase}')
            for key,value in ORACLE.items():
                if not math.isclose(q['native'][key],value,rel_tol=1e-11,abs_tol=1e-8):
                    raise AssertionError(f'{path}: native value mismatch {phase}/{key}')
        for row in p['insert_records']:
            if not (12<=row['batch']<96):
                raise AssertionError(f'{path}: invalid input batch')
        if tuple(p['input_sha256']) not in input_hashes and input_hashes:
            raise AssertionError(f'{path}: input SHA differs between phases')
        input_hashes.add(tuple(p['input_sha256']))
        qhashes.add(p['query_sha256'])
        phy=p['physical']
        phase_points.append(phy['physical_sample_points'])
        if phy['physical_sample_points']!=N_POINTS:
            raise AssertionError(f'{path}: wrong sample count')
        if any(('LowCardinality' in phy[t]['id_type']) != (mode=='lc')
               for t in ('samples','tags','timeranges')):
            raise AssertionError(f'{path}: unexpected physical UUID type')
        if not p.get('part_log',{}).get('available'):
            raise AssertionError(f'{path}: no natural merge trace')
        if p['metrics']['mixed_read_write_server_process']['cpu_s']<=0:
            raise AssertionError(f'{path}: zero measured process CPU')
    if len(input_hashes)!=1 or len(qhashes)!=1:
        raise AssertionError(f'{path}: different fixtures or query text across phases')
    c=d['comparison']['metrics']
    for name in ('native_read.p95_ms','mixed_read_write_server_process.cpu_s',
                 'mixed_read_write_server_process.wall_s'):
        if name not in c:
            raise AssertionError(f'{path}: missing metric: {name}')
        if c[name]['lc_control_drift']>MAX_DRIFT:
            raise AssertionError(f'{path}: A/A drift too large: {name}')
        if c[name]['lc_mean']<=0 or c[name]['raw_mean']<=0:
            raise AssertionError(f'{path}: invalid metric: {name}')
    return {'artifact':str(path),'local_zip_sha256':digest,
            'workflow_head':workflow_sha,'script_sha_manifest':scripts,
            'query_sha256':next(iter(qhashes)),
            'input_sha256':next(iter(input_hashes)),
            'metrics':c,
            'samples_per_phase':N_POINTS}


def reduce(runs):
    if len(runs)!=2 or len({r['local_zip_sha256'] for r in runs})!=2:
        raise AssertionError('Exactly two independent runner artifacts required')
    if runs[0]['script_sha_manifest']!=runs[1]['script_sha_manifest']:
        raise AssertionError('Experiment code changed between independent runners')
    if runs[0]['query_sha256']!=runs[1]['query_sha256']:
        raise AssertionError('Native query text SHA differs between runners')
    if runs[0]['input_sha256']!=runs[1]['input_sha256']:
        raise AssertionError('Source input SHA differs between runners')
    result={'question_id':'R02-small-batch-raw-lc-write-v1',
            'experiment':'fixed-open-loop-native-40q-84insert-natural-merges',
            'valid':True,'source_sha':SOURCE,'binary_sha256':BINARY,'fixture_sha256':FIXTURE,
            'runner_artifacts':[r['local_zip_sha256'] for r in runs],
            'historical_excluded_invalid_run':'41633619697 (26.30% control p95 drift)',
            'ratios':{},'decision':'INCONCLUSIVE'}
    for key in ('native_read.p95_ms','mixed_read_write_server_process.cpu_s',
                'mixed_read_write_server_process.wall_s'):
        ratios=[r['metrics'][key]['raw_over_lc'] for r in runs]
        drifts=[r['metrics'][key]['lc_control_drift'] for r in runs]
        if any((v<=0 or d>MAX_DRIFT) for v,d in zip(ratios,drifts)):
            raise AssertionError(f'{key}: invalid direction or control')
        result['ratios'][key]={'per_runner_raw_over_lc':ratios,
             'geometric_mean_raw_over_lc':round(math.exp(statistics.mean(math.log(v) for v in ratios)),5),
             'baseline_drifts':drifts}
    p95=result['ratios']['native_read.p95_ms']
    cpu=result['ratios']['mixed_read_write_server_process.cpu_s']
    p95_consistent=all((1-v)>max(.05,d) for v,d in zip(p95['per_runner_raw_over_lc'],p95['baseline_drifts']))
    cpu_consistent=all((1-v)>max(.05,d) for v,d in zip(cpu['per_runner_raw_over_lc'],cpu['baseline_drifts']))
    result['p95_replicated']=p95_consistent
    result['cpu_replicated']=cpu_consistent
    result['decision']='REPLICATED_LAB_EFFECT' if (p95_consistent and cpu_consistent) else 'INCONCLUSIVE'
    result['limitations']=[
       'Only two independent GitHub-hosted runners with same synthetic 96-batch fixture',
       'A/A p95 drift in historical first runner exceeded 25%; that runner is excluded',
       'The client was open-loop with exactly 40 scheduled reads and 84 inserts, but only one in-flight read at a time',
       'This does not cover production optimized LLVM/jemalloc, multiple tenants, real 15d churn or S3 cold storage',
       'Latency difference is from one source/schema variable but CPU includes ingest, read and natural background merges']
    return result


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--artifact',action='append',required=True)
    a.add_argument('--output',required=True)
    args=a.parse_args()
    if len(args.artifact)!=2:
        raise SystemExit('Require exactly two independent runner ZIPs')
    data=reduce([read_zip(x) for x in args.artifact])
    p=pathlib.Path(args.output)
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,sort_keys=True,indent=2)+'\n')
    print('R02_FIXED_REDUCTION',json.dumps({'valid':data['valid'],'decision':data['decision'],
          'p95_replicated':data['p95_replicated'],'cpu_replicated':data['cpu_replicated'],
          'ratios':data['ratios']},separators=(',',':')))


if __name__=='__main__':
    main()
