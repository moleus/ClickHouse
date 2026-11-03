#!/usr/bin/env python3
"""Audit immutable native projection x JOIN factorial evidence (read-only).

Inputs are raw GitHub Actions artifact ZIPs from run 41589798782, not
summaries. This reducer does not measure a new plan or fit a production CBO.
"""
import argparse
import hashlib
import json
import math
import pathlib
import statistics
import zipfile

FIXTURE = '8f3a36846f80d6be5a8604d142e934399f2687094766c57bf105e43977f52858'
ORDER = ('off_merge','auto_merge','auto_hash','off_hash','off_hash','auto_hash','auto_merge','off_merge')
MODES = ('off_merge','auto_merge','off_hash','auto_hash')
SORT_NAMES = {'MergeSortingTransform','MergingSortedTransform','PartialSortingTransform'}


def load(path, expected_case):
    blob = pathlib.Path(path).read_bytes()
    with zipfile.ZipFile(path) as archive:
        reports = [name for name in archive.namelist() if name.endswith(f'native-factorial-{expected_case}/report.json')]
        if len(reports) != 1:
            raise ValueError(f'{path}: expected 1 report, got {len(reports)}')
        report = json.loads(archive.read(reports[0]))
        manifest = json.loads(archive.read('95k-manifest.json'))
        if 'OK' not in archive.read('patch-binary-hash.txt').decode():
            raise ValueError('Missing original binary integrity check')
    if report['case'] != expected_case or report['days'] != 7:
        raise ValueError('Wrong selector/window')
    if report['fixture_sha'] != FIXTURE or manifest['sha256'] != FIXTURE or manifest['series'] != 1330000:
        raise ValueError('Wrong historical fixture')
    if not report['passed'] or report['errors']:
        raise ValueError('Original experimental gate did not pass')
    if tuple(phase['variant'] for phase in report['phases']) != ORDER:
        raise ValueError('Experimental phase order mismatch')
    seen = set()
    for i, phase in enumerate(report['phases']):
        if phase['index'] != i or len(phase['trials']) != 7:
            raise ValueError('Incomplete phase')
        measured = [t for t in phase['trials'] if not t['warmup']]
        warmup = [t for t in phase['trials'] if t['warmup']]
        if len(measured) != 4 or len(warmup) != 3:
            raise ValueError('Incorrect warmup/measured partition')
        for t in phase['trials']:
            if t['qid'] in seen: raise ValueError(f'Repeated query ID: {t["qid"]}')
            seen.add(t['qid'])
            error = t['max_abs_error']
            if not math.isfinite(error) or error > 1e-8:
                raise ValueError(f'Parity check failed: {t["qid"]}: {error}')
        for key in ('wall_ms','cpu_ms','peak_memory_bytes','read_rows','read_bytes','selected_marks'):
            if not all(key in x for x in measured):
                raise ValueError(f'Missing QueryFinish {key} for phase {i}')
        if phase['representative_profile_qid'] not in {t['qid'] for t in measured}:
            raise ValueError('Representative profiler does not refer to measured trial')
        if not phase['representative_profile']['processors']:
            raise ValueError('Missing processor profile')
    return report, {'artifact_sha256':hashlib.sha256(blob).hexdigest(),'artifact_bytes':len(blob),
                    'artifact_name':pathlib.Path(path).name, 'original_run':41589798782,
                    'case':expected_case,'fixture_sha256':FIXTURE}


def mean_of_phase_medians(phases, mode, field):
    selected = [p for p in phases if p['variant'] == mode]
    return statistics.mean(statistics.median(t[field] for t in p['trials'] if not t['warmup']) for p in selected)


def op_work_ms(phase, names=None, plan_step=None):
    return sum(float(x['work_us'])/1000 for x in phase['representative_profile']['processors']
               if (names is None or x['name'] in names) and
                  (plan_step is None or x.get('plan_step_name') == plan_step))


def reduce(report, provenance):
    phases = report['phases']
    stats = {}
    for mode in MODES:
        chosen = [p for p in phases if p['variant'] == mode]
        walls = [p['wall_ms_median'] for p in chosen]
        stats[mode] = {
            'wall_ms':statistics.mean(walls),
            'cpu_ms':mean_of_phase_medians(phases,mode,'cpu_ms'),
            'query_peak_bytes':mean_of_phase_medians(phases,mode,'peak_memory_bytes'),
            'read_rows':mean_of_phase_medians(phases,mode,'read_rows'),
            'read_bytes':mean_of_phase_medians(phases,mode,'read_bytes'),
            'selected_marks':mean_of_phase_medians(phases,mode,'selected_marks'),
            'control_drift':abs(walls[0]-walls[1])/statistics.mean(walls),
            'sort_work_ms_representative_mean':statistics.mean(op_work_ms(p, SORT_NAMES) for p in chosen),
            'join_work_ms_representative_mean':statistics.mean(op_work_ms(p,plan_step='Join') for p in chosen),
            'wall_ms_per_phase':walls,
        }
        if stats[mode]['control_drift'] > 0.30:
            raise ValueError(f'{report["case"]}/{mode} exceeds original 30% drift threshold')
        if abs(stats[mode]['wall_ms']-report['summary']['variants'][mode]['wall_ms']) > 0.01:
            raise ValueError('Reducer disagrees with original summary')
    offm, autom = stats['off_merge'], stats['auto_merge']
    offh, autoh = stats['off_hash'], stats['auto_hash']
    result = {
      **provenance,
      'original_report_passed':True,
      'group':'historical_observed_not_held_out',
      'variants':stats,
      'projection':{
          'rows_saved':offm['read_rows']-autom['read_rows'],
          'rows_saved_fraction':(offm['read_rows']-autom['read_rows'])/offm['read_rows'],
          'marks_saved':offm['selected_marks']-autom['selected_marks'],
          'bytes_saved':offm['read_bytes']-autom['read_bytes'],
          'merge_wall_delta_ms':autom['wall_ms']-offm['wall_ms'],
          'merge_cpu_delta_ms':autom['cpu_ms']-offm['cpu_ms'],
          'hash_wall_delta_ms':autoh['wall_ms']-offh['wall_ms'],
          'hash_cpu_delta_ms':autoh['cpu_ms']-offh['cpu_ms'],
          'interaction_wall_ms':(autom['wall_ms']-offm['wall_ms'])-(autoh['wall_ms']-offh['wall_ms']),
          'interaction_cpu_ms':(autom['cpu_ms']-offm['cpu_ms'])-(autoh['cpu_ms']-offh['cpu_ms']),
          'merge_sort_work_increase_ms_representative':autom['sort_work_ms_representative_mean']-offm['sort_work_ms_representative_mean'],
          'paired_projection_wall_deltas_ms':[a-b for a,b in zip(autom['wall_ms_per_phase'],offm['wall_ms_per_phase'])],
      },
      'caveats':[
        'Operator elapsed_us values are concurrent processor work, not critical-path wall time.',
        'Factorial interactions include more than sort cost: not a causal isolation of only sorting.',
        'Artifacts omit raw query result arrays; semantic parity inherited from original native query validator.',
        'This is the same historical fixture used for discovery; not a held-out CBO evaluation.',
        'The old artifact stores a pass/fail binary hash check, not the binary SHA256 itself.',
        'This O1 lab build cannot establish optimized/release-like production performance.',
        'No robust p95/p99 or production memory safety conclusions from these phase medians.',
      ]
    }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',required=True)
    p.add_argument('--project',required=True)
    p.add_argument('--out',required=True)
    args = p.parse_args()
    all_result = {}
    for case in ('stage','project'):
        report, origin=load(getattr(args,case),case)
        all_result[case]=reduce(report,origin)
    target=pathlib.Path(args.out)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'schema':'r06_factorial_offline_v1','run':41589798782,'cases':all_result},indent=2,sort_keys=True)+'\n')
    for case,r in all_result.items():
        p=r['projection']
        print(f'{case}: saved {p["rows_saved"]:,.0f} rows ({p["rows_saved_fraction"]:.1%}), '
              f'merge wall +{p["merge_wall_delta_ms"]:.2f} ms, interaction {p["interaction_wall_ms"]:.2f} ms, '
              f'extra sampled sort-work {p["merge_sort_work_increase_ms_representative"]:.1f} ms')
    print('REPORT',target)


if __name__=='__main__': main()
