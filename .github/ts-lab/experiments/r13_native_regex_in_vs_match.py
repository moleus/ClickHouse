#!/usr/bin/env python3
"""R13: native PromQL literal-OR -> SQL IN vs equivalent compact regex -> match.

Same input, native PromQL output, stock ClickHouse O1 binary and server.
Only the regex representation changes. Matched 6400 of 32768 series in both.
Not a C++ speed claim. EXPLAIN plus QueryFinish CPU/marks give attribution.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE = '52529f0c711566d696917678bf15e7068772fdde'
METRIC = 'tslab_r13_branch_or'
BASE = int(dt.datetime(2026, 8, 10, 12, tzinfo=dt.timezone.utc).timestamp())
END = BASE + 3600
BRANCHES = 1024
ROWS = 32768
SELECTED = 200
STAGES = 16
LITERAL = '|'.join('b' + f'{k:04d}' for k in range(SELECTED))
COMPACT = 'b0[01][0-9][0-9]'
CASES = {'literal_IN': LITERAL, 'compact_MATCH': COMPACT}


def quote(s):
    return "'" + s.replace('\\', '\\\\').replace("'", "\\'") + "'"


def request(endpoint, query, qid=None):
    url = endpoint
    if qid:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode({'query_id': qid})
    start = time.perf_counter_ns()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=query.encode(), method='POST'), timeout=90
        ) as result:
            body = result.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f'ClickHouse HTTP {exc.code}: {exc.read().decode(errors="replace")[:1800]}'
        ) from exc
    return round((time.perf_counter_ns() - start) / 1e6, 4), body


def sql(endpoint, q, qid=None):
    return request(endpoint, q, qid)[1]


def json_rows(endpoint, q):
    return [json.loads(x) for x in sql(endpoint, q + ' FORMAT JSONEachRow').splitlines() if x]


def payload(i):
    branch = 'b' + f'{i % BRANCHES:04d}'
    value = 1 + (i % 97) * 0.01 + (i // BRANCHES) * .0001
    row = {'metric_name': METRIC, 'tags': {'branch': branch,
           'stage': f'stage-{i % STAGES:02d}', 'host': f'host-{i:06d}'},
           'samples': [[dt.datetime.fromtimestamp(BASE, dt.timezone.utc)
                        .strftime('%Y-%m-%d %H:%M:%S.000'), value]]}
    return json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n'


def oracle():
    values = {f'stage-{i:02d}': [] for i in range(STAGES)}
    for i in range(ROWS):
        if i % BRANCHES < SELECTED:
            values[f'stage-{i % STAGES:02d}'].append(
                1 + (i % 97) * .01 + (i // BRANCHES) * .0001)
    if sum(map(len, values.values())) != ROWS * SELECTED // BRANCHES:
        raise AssertionError('Bad oracle selectivity')
    return {k: sum(v) / len(v) for k, v in values.items() if v}


def normalize(text):
    out = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        tags = dict(row.get('tags', []))
        if set(tags) != {'stage'}:
            raise AssertionError('Wrong native label set ' + repr(tags))
        if len(row['samples']) != 1:
            raise AssertionError('Native must have one sample per stage')
        stamp, value = row['samples'][0]
        got_epoch = int(dt.datetime.fromisoformat(stamp.replace(' ', 'T'))
                        .replace(tzinfo=dt.timezone.utc).timestamp())
        if got_epoch != END or tags['stage'] in out:
            raise AssertionError('Duplicate stage or unexpected timestamp')
        out[tags['stage']] = float(value)
    return out


def query_for(table, case):
    pattern = CASES[case]
    expr = f'avg by(stage)(avg_over_time({METRIC}{{branch=~"{pattern}"}}[1d]))'
    return (f'SELECT * FROM prometheusQueryRange({table},{quote(expr)},'
            f'{END},{END},60) SETTINGS max_threads=2,use_query_cache=0 FORMAT JSONEachRow')


def pid_rss_kib(pid_path):
    if not pid_path:
        return None
    pid = int(pathlib.Path(pid_path).read_text().strip())
    data = pathlib.Path('/proc') / str(pid) / 'status'
    for line in data.read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1])
    return None


def ptile(items, p):
    if not items:
        return None
    x = sorted(items)
    k = (len(x) - 1) * p
    left, right = int(math.floor(k)), int(math.ceil(k))
    return x[left] + (x[right] - x[left]) * (k - left)


def phase_stats(trials):
    trials = [x for x in trials if not x['warmup']]
    return {'count': len(trials), 'wall_p50_ms': ptile([x['wall_ms'] for x in trials], .5),
            'wall_p95_ms': ptile([x['wall_ms'] for x in trials], .95),
            'server_p50_ms': ptile([x['query_log']['query_duration_ms'] for x in trials], .5),
            'cpu_p50_ms': ptile([x['query_log']['cpu_us']/1000 for x in trials], .5),
            'read_rows_p50': ptile([x['query_log']['read_rows'] for x in trials], .5),
            'read_bytes_p50': ptile([x['query_log']['read_bytes'] for x in trials], .5),
            'query_peak_memory_p95_bytes': ptile([x['query_log']['memory_usage'] for x in trials], .95),
            'rss_after_p50_kib': ptile([x['rss_after_kib'] for x in trials
                                      if x['rss_after_kib'] is not None], .5)}


def get_logs(endpoint, qids):
    sql(endpoint, 'SYSTEM FLUSH LOGS')
    for attempt in range(7):
        arr = json_rows(endpoint,
            "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage,"
            "ProfileEvents,exception_code FROM system.query_log "
            "WHERE type='QueryFinish' AND startsWith(query_id,'r13_')")
        found = {x['query_id']: x for x in arr}
        if all(q in found for q in qids):
            return found
        time.sleep(.2 * (attempt + 1))
    raise AssertionError(
        f'Only {len(set(qids) & set(found))} of {len(qids)} QueryFinish entries visible')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--endpoint', default='http://127.0.0.1:18191/')
    p.add_argument('--table', default='tslab_r13_regex')
    p.add_argument('--out', required=True)
    p.add_argument('--pid-file')
    p.add_argument('--warmups', type=int, default=2)
    p.add_argument('--measured', type=int, default=4)
    a = p.parse_args()
    if a.warmups < 2 or a.measured < 4:
        raise AssertionError('Need >=2 warmup and >=4 measured per phase')
    report = {'schema_version': 1, 'question_id': 'R13-large-regex-alternation-in-vs-match-v1',
              'source_sha': SOURCE, 'metric': METRIC, 'series': ROWS,
              'branch_values': BRANCHES, 'stages': STAGES, 'expected_selected_series': 6400,
              'regex': {'literal': LITERAL, 'compact': COMPACT},
              'phases': {}, 'trials': [], 'errors': [], 'passed': False,
              'semantic_correctness': False, 'fixture_sha256': None,
              'scientific_decision': 'INCONCLUSIVE'}
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    def save():
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    try:
        selected_literal = [i for i in range(BRANCHES) if re.fullmatch(LITERAL, f'b{i:04d}')]
        selected_compact = [i for i in range(BRANCHES) if re.fullmatch(COMPACT, f'b{i:04d}')]
        assert selected_literal == selected_compact == list(range(200)), 'Non-equivalent regex selectors'
        report['selected_branches'] = selected_literal
        report['oracle'] = oracle()
        sql(a.endpoint, f'CREATE TABLE {a.table} ENGINE=TimeSeries '
                        'SETTINGS recent_samples_ttl_seconds=0')
        digest = hashlib.sha256()
        for start in range(0, ROWS, 2048):
            batch = ''.join(payload(i) for i in range(start, min(start+2048, ROWS)))
            digest.update(batch.encode())
            sql(a.endpoint, f'INSERT INTO {a.table}(metric_name,tags,samples) '
                            f'FORMAT JSONEachRow\n' + batch)
        report['fixture_sha256'] = digest.hexdigest()
        got = int(sql(a.endpoint, f'SELECT count() FROM {a.table} FORMAT TSV').strip())
        if got != ROWS:
            raise AssertionError(f'Wrong stored IDs {got} vs {ROWS}')
        uuid = sql(a.endpoint,
            'SELECT toString(uuid) FROM system.tables WHERE database='
            "'default' AND name=" + quote(a.table) + ' FORMAT TSV').strip()
        if len(uuid) != 36:
            raise AssertionError('Missing TimeSeries table UUID')
        for kind in ('tags','samples','timeranges'):
            inner = f'default.' + chr(96) + f'.inner_id.{kind}.{uuid}' + chr(96)
            sql(a.endpoint, f'OPTIMIZE TABLE {inner} FINAL SETTINGS optimize_throw_if_noop=0')
        sql(a.endpoint, 'SYSTEM STOP MERGES')
        for case in CASES:
            try:
                report.setdefault('explain', {})[case] = sql(a.endpoint,
                    'EXPLAIN PLAN ' + query_for(a.table, case))[:25000]
            except Exception as exc:
                report.setdefault('explain_errors', {})[case] = str(exc)[:1200]
        canonical = report['oracle']
        run = os.environ.get('GITHUB_RUN_ID', 'local')
        for idx, case in enumerate(['literal_IN','compact_MATCH','compact_MATCH','literal_IN']):
            phase = f'{"A" if idx in (0,3) else "B"}{idx}'
            phase_trials = []
            query = query_for(a.table, case)
            for trial in range(a.warmups + a.measured):
                qid = f'r13_{run}_{phase}_{trial}'
                wall, body = request(a.endpoint, query, qid)
                got = normalize(body)
                if set(got) != set(canonical):
                    raise AssertionError(f'Wrong native stages for {phase} trial {trial}')
                for stage in canonical:
                    if not math.isclose(got[stage], canonical[stage], abs_tol=1e-8, rel_tol=1e-10):
                        raise AssertionError(f'Native numerical mismatch {phase} {stage} '
                                             f'{got[stage]} vs {canonical[stage]}')
                sample = {'phase': phase, 'case': case, 'trial': trial, 'warmup': trial < a.warmups,
                          'query_id': qid, 'wall_ms': wall, 'rss_after_kib': pid_rss_kib(a.pid_file),
                          'points': len(got), 'query_sha256': hashlib.sha256(query.encode()).hexdigest()}
                phase_trials.append(sample)
                report['trials'].append(sample)
            logs = get_logs(a.endpoint, [x['query_id'] for x in phase_trials])
            for sample in phase_trials:
                entry = logs[sample['query_id']]
                if int(entry['exception_code']) != 0:
                    raise AssertionError('QueryFinish reports a failure: ' + sample['query_id'])
                events = entry['ProfileEvents']
                sample['query_log'] = {
                    'query_duration_ms': entry['query_duration_ms'],
                    'cpu_us': events.get('OSCPUVirtualTimeMicroseconds', 0),
                    'read_rows': entry['read_rows'], 'read_bytes': entry['read_bytes'],
                    'memory_usage': entry['memory_usage'],
                    'selected_marks': events.get('SelectedMarks'),
                    'compressed_read_bytes': events.get('ReadCompressedBytes'),
                    'text_index_cpu_us': events.get('TextIndexReaderTotalMicroseconds')}
                if sample['query_log']['cpu_us'] <= 0:
                    raise AssertionError('Missing CPU attribution '+sample['query_id'])
            report['phases'][phase] = {'case': case, 'summary': phase_stats(phase_trials)}
            save()
            print('R13_PHASE', json.dumps({'phase': phase, 'case': case,
                **report['phases'][phase]['summary']}, separators=(',', ':')),flush=True)
        a1, a2 = report['phases']['A0']['summary'], report['phases']['A3']['summary']
        b1, b2 = report['phases']['B1']['summary'], report['phases']['B2']['summary']
        baseline = (a1['wall_p50_ms'] + a2['wall_p50_ms']) / 2
        candidate = (b1['wall_p50_ms'] + b2['wall_p50_ms']) / 2
        drift = abs(a1['wall_p50_ms'] - a2['wall_p50_ms']) / baseline
        cpu_a = (a1['cpu_p50_ms'] + a2['cpu_p50_ms']) / 2
        cpu_b = (b1['cpu_p50_ms'] + b2['cpu_p50_ms']) / 2
        report['comparison'] = {'literal_wall_p50_mean_ms': baseline,
            'compact_wall_p50_mean_ms': candidate, 'compact_over_literal_wall_ratio': candidate/baseline,
            'literal_cpu_p50_mean_ms': cpu_a, 'compact_cpu_p50_mean_ms': cpu_b,
            'compact_over_literal_cpu_ratio': cpu_b/cpu_a,
            'baseline_wall_drift_fraction': drift}
        report['semantic_correctness'] = True
        report['scientific_decision'] = 'INVALID_METHOD' if drift > .25 else 'ACCEPT_FOR_NEXT_GATE'
        report['passed'] = drift <= .25
        if not report['passed']:
            report['errors'].append(f'Control drift {drift:.3f} > .25')
    except Exception as exc:
        report['errors'].append(str(exc)[:2600])
    finally:
        try:
            sql(a.endpoint, 'SYSTEM START MERGES')
        except Exception:
            pass
        save()
        print('R13_REGEX_DONE', json.dumps({'passed': report['passed'],
            'decision': report['scientific_decision'], 'correct': report['semantic_correctness'],
            'comparison': report.get('comparison'), 'errors': report['errors']},
            separators=(',', ':')), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
