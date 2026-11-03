#!/usr/bin/env python3
"""R02 repeated-ID TimeSeries write / merge diagnostic, stock O1 only.

Two schemas differ ONLY by the physical UUID ID representation.
Writes 12 small batches, some series stable and others rotated every 3 batches.
Measures physical sample POINTS via sum(length(samples)), not bucket rows.
INSERT wall/CPU and explicit merge costs are observations, not a claimed
steady-state gain; the baseline is not a full A/B/B/A write-performance test.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

METRIC = 'tslab_r02_periodic'
SOURCE = '52529f0c711566d696917678bf15e7068772fdde'
BASE = int(dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc).timestamp())


def q(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def request(endpoint, statement, qid=None):
    url = endpoint
    if qid is not None:
        url += ('&' if '?' in url else '?') + urllib.parse.urlencode({'query_id': qid})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(
            url, data=statement.encode(), method='POST'
        ), timeout=180) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'HTTP {exc.code}: {exc.read().decode(errors="replace")[:1300]} '
                           f'statement={statement[:230]}') from exc
    return (time.perf_counter() - t0) * 1000, body


def sql(endpoint, statement, qid=None):
    return request(endpoint, statement, qid)[1]


def jsonrows(endpoint, statement):
    return [json.loads(line) for line in sql(endpoint, statement + ' FORMAT JSONEachRow').splitlines() if line]


def stamp(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.000')


def fixture_batch(batch, slots):
    rows = []
    for scope in ('stable', 'rotating'):
        for i in range(slots):
            generation = 0 if scope == 'stable' else batch // 3
            tags = {'scope': scope, 'slot': f'{i:05d}', 'generation': str(generation)}
            value = (100.0 + (i % 97) + batch / 10.0 if scope == 'stable' else
                     200.0 + (i % 83) + generation * 4.0 + batch / 10.0)
            row = {'metric_name': METRIC, 'tags': tags,
                   'samples': [[stamp(BASE + batch * 60), value]]}
            rows.append(json.dumps(row, sort_keys=True, separators=(',', ':')))
    return '\n'.join(rows) + '\n'


def tables_ddl(lc, raw):
    regular = f'CREATE TABLE {lc} ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0'
    # Matches tested v11 RAW_ID_HIGH_CHURN_V11.sql; exact same hash and index.
    explicit = f"""CREATE TABLE {raw} ENGINE=TimeSeries
    SETTINGS recent_samples_ttl_seconds=0
    SAMPLES INNER COLUMNS (id Tuple(UInt64, UUID))
    TAGS INNER COLUMNS (
        id Tuple(UInt64, UUID) DEFAULT tuple(
            sipHash64(metric_name), reinterpretAsUUID(sipHash128(tags))),
        metric_name LowCardinality(String),
        tags Map(LowCardinality(String), String),
        INDEX tags_idx tags TYPE text(tokenizer='keyValuePairs') GRANULARITY 100000000)
    TIME RANGES INNER COLUMNS (id Tuple(UInt64, UUID))"""
    return regular, explicit


def table_uuid(endpoint, table):
    res = sql(endpoint, f"SELECT toString(uuid) FROM system.tables WHERE database='default' "
                        f"AND name={q(table)} FORMAT TSV").strip()
    if len(res) != 36:
        raise AssertionError(f'Missing table UUID: {table}')
    return res


def physical(endpoint, table):
    uuid = table_uuid(endpoint, table)
    out = {}
    for kind in ('tags', 'samples', 'timeranges'):
        inner = f'.inner_id.{kind}.{uuid}'
        rows = jsonrows(endpoint,
            "SELECT count() AS parts,ifNull(sum(rows),0) AS rows,"
            "ifNull(sum(marks),0) AS marks,ifNull(sum(bytes_on_disk),0) AS bytes "
            "FROM system.parts WHERE active AND database='default' "
            f"AND table={q(inner)}")
        col = jsonrows(endpoint,
            "SELECT name,type FROM system.columns WHERE database='default' "
            f"AND table={q(inner)} AND name='id'")
        if len(col) != 1 or len(rows) != 1:
            raise AssertionError(f'Bad inner target {table} {kind}: {rows} {col}')
        out[kind] = {**rows[0], 'id_type': col[0]['type'], 'inner_name': inner}
    out['physical_sample_points'] = int(sql(endpoint,
        f"SELECT sum(length(samples)) FROM default.\x60{out['samples']['inner_name']}\x60 FORMAT TSV").strip())
    return out


def expected(slots, batches):
    values = {}
    for scope in ('stable', 'rotating'):
        average_per_series = []
        for i in range(slots):
            for generation in ([0] if scope == 'stable' else range((batches + 2) // 3)):
                vals = []
                for b in range(batches):
                    if scope == 'rotating' and b // 3 != generation:
                        continue
                    vals.append((100.0 + i % 97 + b / 10.0 if scope == 'stable' else
                                 200.0 + i % 83 + generation * 4.0 + b / 10.0))
                if vals:
                    average_per_series.append(sum(vals) / len(vals))
        values[scope] = sum(average_per_series) / len(average_per_series)
    return values


def normalize(response):
    out = {}
    for line in response.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        labels = dict(obj['tags'])
        if set(labels) != {'scope'}:
            raise AssertionError(f'Unexpected output labels {labels}')
        for ts, val in obj['samples']:
            key = (labels['scope'], ts)
            if key in out:
                raise AssertionError('Duplicate native output ' + str(key))
            out[key] = float(val)
    return out


def native(endpoint, table, batches):
    end = BASE + (batches - 1) * 60
    selector = f'avg by(scope)(avg_over_time({METRIC}[30m]))'
    query = f"SELECT * FROM prometheusQueryRange({table},{q(selector)},{end},{end},60) SETTINGS max_threads=2 FORMAT JSONEachRow"
    result = normalize(sql(endpoint, query))
    oracle = expected_tables = expected
    return {'sql_sha256': hashlib.sha256(query.encode()).hexdigest(),
            'samples': [{'scope': key[0], 'timestamp': key[1], 'value': value}
                        for key, value in sorted(result.items())],
            'values': {scope: value for (scope, _), value in result.items()}}


def part_log(endpoint, uuids):
    try:
        sql(endpoint, 'SYSTEM FLUSH LOGS')
        if not jsonrows(endpoint,
            "SELECT name FROM system.columns WHERE database='system' "
            "AND table='part_log' AND name='event_type'"):
            return {'available': False, 'reason': 'system.part_log is not configured'}
        tables = [f'.inner_id.{kind}.{uuid}' for uuid in uuids for kind in ('tags','samples','timeranges')]
        where = ','.join(q(x) for x in tables)
        arr = jsonrows(endpoint,
            "SELECT table,event_type,duration_ms,ProfileEvents FROM system.part_log "
            f"WHERE database='default' AND table IN ({where}) "
            "AND event_type IN ('MergeParts','MutatePart') ORDER BY event_time_microseconds")
        return {'available': True, 'events': arr}
    except Exception as exc:
        return {'available': False, 'error': str(exc)[:1100]}


def get_querylogs(endpoint, prefix):
    sql(endpoint, 'SYSTEM FLUSH LOGS')
    for attempt in range(7):
        found = jsonrows(endpoint,
            "SELECT query_id,query_duration_ms,ProfileEvents,memory_usage,exception_code,type "
            "FROM system.query_log WHERE type='QueryFinish' "
            f"AND startsWith(query_id,{q(prefix)})")
        by_id = {x['query_id']: x for x in found}
        if len(by_id) >= 24:
            return by_id
        time.sleep(0.2 * (attempt + 1))
    return by_id


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--endpoint', default='http://127.0.0.1:18191/')
    p.add_argument('--slots', type=int, default=2000)
    p.add_argument('--batches', type=int, default=12)
    p.add_argument('--prefix', default='tslab_r02')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    if a.slots < 2 or a.slots > 3000 or a.batches != 12:
        raise AssertionError('Bounded 12-batch workload with 2..3000 slots required')
    report = {'question_id': 'R02-small-batch-raw-lc-write-v1','source_sha': SOURCE,
              'slots_per_scope': a.slots, 'batches': a.batches,
              'total_sample_points_per_table': 2 * a.slots * a.batches,
              'expected_unique_series_per_table': a.slots * 5,
              'scope': 'Exploratory small batch write and explicit merge study; no production throughput claim',
              'input_sha256': [], 'inserts': [], 'merges': [], 'snapshots': [],
              'errors': [], 'passed': False}
    target = pathlib.Path(a.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    lc = a.prefix + '_lc'
    raw = a.prefix + '_raw'
    prefix = a.prefix + '_ins_'
    try:
        for stmt in tables_ddl(lc, raw):
            sql(a.endpoint, stmt)
        for name in (lc, raw):
            report['snapshots'].append({'phase': 'created', 'table': name, 'physical': physical(a.endpoint, name)})
        for b in range(a.batches):
            fixture = fixture_batch(b, a.slots)
            report['input_sha256'].append(hashlib.sha256(fixture.encode()).hexdigest())
            order = (lc, raw) if b % 2 == 0 else (raw, lc)
            for name in order:
                qid = prefix + name + '_' + str(b)
                statement = f'INSERT INTO {name}(metric_name,tags,samples) FORMAT JSONEachRow\n' + fixture
                ms, response = request(a.endpoint, statement, qid)
                report['inserts'].append({'batch': b, 'table': name, 'query_id': qid,
                                          'client_ms': round(ms, 3),
                                          'fixture_sha256': report['input_sha256'][-1],
                                          'bytes': len(fixture)})
            if (b + 1) % 4 == 0:
                for name in (lc, raw):
                    before = physical(a.endpoint, name)
                    uuid = table_uuid(a.endpoint, name)
                    for kind in ('tags', 'samples', 'timeranges'):
                        inner = f'default.' + chr(96) + f'.inner_id.{kind}.{uuid}' + chr(96)
                        qid = f'{a.prefix}_merge_{name}_{b}_{kind}'
                        ms, response = request(a.endpoint, f'OPTIMIZE TABLE {inner} FINAL SETTINGS optimize_throw_if_noop=0', qid)
                        report['merges'].append({'batch': b, 'table': name, 'target': kind,
                                                'query_id': qid,'wall_ms_client': round(ms, 3)})
                    after = physical(a.endpoint, name)
                    report['snapshots'].append({'phase': f'after_batch_{b+1}', 'table': name,
                                                'before': before, 'after': after})
                target.write_text(json.dumps(report, indent=2) + '\n')
        a_physical = physical(a.endpoint, lc)
        b_physical = physical(a.endpoint, raw)
        report['final_physical'] = {'lc': a_physical, 'raw': b_physical}
        for mode, snap in (('lc', a_physical), ('raw', b_physical)):
            if snap['physical_sample_points'] != report['total_sample_points_per_table']:
                raise AssertionError(f'{mode}: physical samples {snap["physical_sample_points"]} '
                                     f'!= {report["total_sample_points_per_table"]}')
            types = [snap[k]['id_type'] for k in ('tags','samples','timeranges')]
            if any(('LowCardinality' in typ) != (mode == 'lc') for typ in types):
                raise AssertionError('Wrong input ID encoding for '+mode+': '+repr(types))
        seen = {lc: native(a.endpoint, lc, a.batches),raw: native(a.endpoint, raw, a.batches)}
        report['native'] = seen
        if seen[lc]['values'] != seen[raw]['values']:
            for key in seen[lc]['values']:
                if abs(seen[lc]['values'][key]-seen[raw]['values'].get(key,float('nan'))) > 1e-8:
                    raise AssertionError('Native response differs '+key)
        reference = expected(a.slots, a.batches)
        for table in (lc, raw):
            observed = seen[table]['values']
            if set(reference) != set(observed):
                raise AssertionError(f'Native scope keys differ {table} {observed}')
            for key in reference:
                if abs(observed[key] - reference[key]) > 1e-8:
                    raise AssertionError(f'Independent oracle mismatch: {table} {key} {observed[key]} != {reference[key]}')
        report['query_log_inserts'] = get_querylogs(a.endpoint,prefix)
        known = {x['query_id'] for x in report['inserts']}
        if not known <= report['query_log_inserts'].keys():
            report['errors'].append('Some INSERT QueryFinish entries missing; wall and physical evidence preserved')
        report['part_log'] = part_log(a.endpoint, [table_uuid(a.endpoint,x) for x in (lc,raw)])
        report['passed'] = not report['errors']
    except Exception as exc:
        report['errors'].append(str(exc)[:2800])
    finally:
        target.write_text(json.dumps(report, indent=2, default=str)+'\n')
        print('R02_SMALL_WRITE_DONE',json.dumps({
            'passed':report['passed'],'points_per_table':report['total_sample_points_per_table'],
            'expected_unique_series':report['expected_unique_series_per_table'],
            'insert_records':len(report['inserts']),'merges':len(report['merges']),
            'final_physical':{k:{t:v[t]['bytes'] for t in ('samples','tags','timeranges')}
                              for k,v in report.get('final_physical',{}).items()},
            'errors':report['errors'][:3]},separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
