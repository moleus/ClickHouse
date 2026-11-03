#!/usr/bin/env python3
"""Reduce three independent-runner R02 96-batch native CPU ABBA artifacts.

Do not aggregate performance trials by pooling raw times across machines.
Compare within-runner RAW/LC ratios, require per-runner A/A drift and scientific
parity, then test direction on all three GitHub-hosted jobs.
"""
import argparse
import hashlib
import json
import math
import pathlib
import statistics
import zipfile

SOURCE = '52529f0c711566d696917678bf15e7068772fdde'
FIXTURE = 'dd2dd55ee8fcf3c11c1fef94ebdd54ac8b3a6a53cea6deaae991b3b94c88b931'
METRICS = ('ingest_only','start_merges_and_optimize')
PHASES = {'A0':'lc','B1':'raw','B2':'raw','A3':'lc'}


def load(path):
    path=pathlib.Path(path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    with zipfile.ZipFile(path) as archive:
        names=set(archive.namelist())
        mandatory={'report.json','build-info.txt','binary-verify.txt','archive-verify.txt',
                   'script-sha256.txt','workflow-head.txt','console.log'}
        if not mandatory <= names:
            raise AssertionError(f'{path}: missing artifact files {mandatory-names}')
        report=json.loads(archive.read('report.json'))
        build=archive.read('build-info.txt').decode()
        manifest=archive.read('script-sha256.txt').decode()
        binary_ok=archive.read('binary-verify.txt').decode()
        archive_ok=archive.read('archive-verify.txt').decode()
        commit=archive.read('workflow-head.txt').decode().strip()
        if SOURCE not in build or 'optimization=O1 unmodified stock source no lab patches' not in build:
            raise AssertionError(f'{path}: build metadata differs')
        if 'clickhouse: OK' not in binary_ok or 'OK' not in archive_ok:
            raise AssertionError(f'{path}: binary/archive checks absent')
        if 'r02_process_cpu_abba.py' not in manifest or 'r02_small_batch_write_merge.py' not in manifest:
            raise AssertionError(f'{path}: missing script hash evidence')
        if len(commit)!=40:
            raise AssertionError(f'{path}: missing git head')
    if not report.get('passed') or report.get('errors'):
        raise AssertionError(f'{path}: experiment did not pass')
    if report.get('source_sha')!=SOURCE or report.get('combined_fixture_sha256')!=FIXTURE:
        raise AssertionError(f'{path}: source/fixture mismatch')
    if report.get('physical_points_per_phase')!=384000 or len(report.get('input_sha256',[]))!=96:
        raise AssertionError(f'{path}: fixture scale mismatch')
    if set(report['phases'])!=set(PHASES):
        raise AssertionError(f'{path}: missing phase')
    reference=None
    bytes_by_mode={}
    for phase,mode in PHASES.items():
        row=report['phases'][phase]
        if not row.get('passed') or row.get('mode')!=mode or len(row.get('inserts',[]))!=96:
            raise AssertionError(f'{path}: incomplete {phase}')
        if len(row.get('insert_query_log',{}))!=96 or len(row.get('merges',[]))!=3:
            raise AssertionError(f'{path}: missing QueryFinish/merge events in {phase}')
        if len(row.get('proc_boot',{}).get('pids',[]))<2:
            raise AssertionError(f'{path}: missed watchdog child in {phase}')
        if not row.get('part_log',{}).get('available'):
            raise AssertionError(f'{path}: missing part_log in {phase}')
        physical=row['physical_after_merges']
        if physical['physical_sample_points']!=384000:
            raise AssertionError(f'{path}: wrong physical sample points in {phase}')
        if any(physical[x]['parts']!=1 for x in ('tags','samples','timeranges')):
            raise AssertionError(f'{path}: merge incomplete in {phase}')
        series=row['native']
        if reference is None:
            reference=series
        if set(reference)!=set(series) or any(not math.isclose(reference[k],series[k],
                              abs_tol=1e-8,rel_tol=1e-11) for k in reference):
            raise AssertionError(f'{path}: native parity broken {phase}')
        if any(x['sha256'] != report['input_sha256'][x['batch']] for x in row['inserts']):
            raise AssertionError(f'{path}: input differs in {phase}')
        size=sum(physical[x]['bytes'] for x in ('tags','samples','timeranges'))
        if mode in bytes_by_mode and bytes_by_mode[mode]!=size:
            raise AssertionError(f'{path}: same mode physical bytes differ')
        bytes_by_mode[mode]=size
        for name in METRICS:
            if row['metrics'][name]['cpu_s']<=0 or row['metrics'][name]['wall_s']<=0:
                raise AssertionError(f'{path}: zero CPU/wall in {phase} {name}')
    c=report.get('comparison',{})
    if not c.get('valid') or c.get('errors'):
        raise AssertionError(f'{path}: internal comparison invalid')
    for name in METRICS:
        for key in ('cpu_s','wall_s'):
            stat=c['ratios'][name][key]
            if stat['lc_control_drift']>.25 or stat['lc']<=0 or stat['raw']<=0:
                raise AssertionError(f'{path}: invalid drift/delta in {name} {key}')
    return {'artifact':str(path),'zip_sha256':digest,'git_head':commit,'script_manifest':manifest,
            'fixture_sha256':FIXTURE,'ratios':c['ratios'],'physical_bytes':bytes_by_mode,
            'full_native_values':reference,
            'per_phase_metrics':{phase:{key:row['metrics'][key]
                  for key in METRICS} for phase,row in report['phases'].items()}}


def summarize(records):
    if len(records)!=3:
        raise AssertionError('Expected original plus two independent GitHub runner job artifacts')
    if len({r['zip_sha256'] for r in records})!=3:
        raise AssertionError('Duplicate artifacts')
    manifests={r['script_manifest'] for r in records}
    if len(manifests)!=1:
        raise AssertionError('Source script hashes differ across runner artifacts')
    physical={tuple(sorted(r['physical_bytes'].items())) for r in records}
    if len(physical)!=1:
        raise AssertionError('Same fixture produced inconsistent physical compressed bytes')
    for row in records[1:]:
        for label,original in records[0]['full_native_values'].items():
            if not math.isclose(row['full_native_values'][label],original,abs_tol=1e-8,rel_tol=1e-11):
                raise AssertionError('Native output differs between independent runners')
    result={'question_id':'R02-small-batch-raw-lc-write-v1',
            'scientific_scope':'three independent GitHub hosted runner jobs, each four fresh-server ABBA phases',
            'binary_sha256':'f0a2fa1f814c58853f43423705cac10ab9df3bfe5887e27ebe9620ba7cb9660e',
            'source_sha':SOURCE,'fixture_sha256':FIXTURE,
            'artifact_hashes':[r['zip_sha256'] for r in records],
            'git_heads':[r['git_head'] for r in records],
            'ratios':{},'physical_bytes':records[0]['physical_bytes'],
            'valid':True,'decision':'INCONCLUSIVE'}
    signal=[]
    for name in METRICS:
        for metric in ('cpu_s','wall_s','write_bytes_delta'):
            values=[r['ratios'][name][metric]['raw_over_lc'] for r in records]
            if any(v is None or v<=0 for v in values):
                raise AssertionError('Bad effect ratio')
            drifts=[r['ratios'][name][metric]['lc_control_drift'] for r in records]
            result['ratios'][f'{name}.{metric}']={
                'per_runner_raw_over_lc':values,
                'geomean_raw_over_lc':round(math.exp(statistics.mean(math.log(v) for v in values)),5),
                'min':min(values),'max':max(values),'control_drifts':drifts}
            if metric=='cpu_s':
                # All three independent host measurements must indicate
                # at least 5% lower CPU, and exceed each local A/A drift.
                signal.append(all((1-v)>max(.05,d) for v,d in zip(values,drifts)))
    result['cpu_write_and_merge_both_replicated']=all(signal)
    if all(signal):
        result['decision']='REPLICATED_LAB_EFFECT'
    else:
        result['decision']='INCONCLUSIVE'
    result['limitations']=[
        'Three GitHub-hosted jobs do not approximate real production hardware/traffic.',
        'Stock LLVM22 O1 build with glibc, not production-optimized jemalloc binary.',
        'Background merges disabled during writes and then explicitly finished; no simultaneous read load.',
        'No hypothesis-test p-value or cross-hardware production inference; per-host ratios and full artifacts preserved.',
        'Query-log INSERT CPU is not a complete process CPU; only /proc watchdog plus worker deltas used.']
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifact',action='append',required=True,
                        help='repeat exactly three times: original and two independent GitHub runner ZIPs')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if len(args.artifact)!=3:
        parser.error('Require exactly 3 artifact ZIPs')
    record=summarize([load(p) for p in args.artifact])
    dest=pathlib.Path(args.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
    print('R02_THREE_RUNNER_REDUCER',json.dumps({
         'valid':record['valid'],'decision':record['decision'],
         'ratios':record['ratios'],'fixture':record['fixture_sha256']},separators=(',',':')))


if __name__=='__main__':
    main()
