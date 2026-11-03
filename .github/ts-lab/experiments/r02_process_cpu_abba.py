#!/usr/bin/env python3
"""R02 next gate: whole-process CPU of LC UUID vs RAW UUID small-batch writes.

ABBA same GitHub runner; each phase starts a fresh ClickHouse process and data
directory. The binary and fixture are unchanged. STOP MERGES on all three inner
tables, separate INSERT CPU from explicit final merge CPU. Native PromQL and
physical samples are checked against an independent Python oracle.
"""
import argparse
import hashlib
import json
import math
import os
import pathlib
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r02_small_batch_write_merge as base

SOURCE = '52529f0c711566d696917678bf15e7068772fdde'
PHASES = (('A0', 'lc'), ('B1', 'raw'), ('B2', 'raw'), ('A3', 'lc'))
STEPS = 12
SLOTS = 2000
TABLE = 'tslab_r02_abba'
CPU_HZ = os.sysconf('SC_CLK_TCK')


def proc_tree(root_pid):
    """Follow clickhouse-server watchdog to its worker. The HTTP service runs in the child."""
    todo = [int(root_pid)]
    seen = set()
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        children_file = pathlib.Path('/proc') / str(pid) / 'task' / str(pid) / 'children'
        if children_file.exists():
            todo.extend(int(x) for x in children_file.read_text().split())
    # Stock ClickHouse starts a supervisor and a worker. A one-PID result
    # silently undercounted process CPU in CI 41623203578 (zero CPU while busy).
    if len(seen) < 2:
        raise AssertionError('Watchdog child is missing; refusing zero-CPU accounting for root pid '
                             + str(root_pid))
    return sorted(seen)


def measure(pid):
    """Sum Linux /proc/PID/stat for all ClickHouse worker processes, not just the watchdog.

    /proc/PID/stat fields 14,15 count process CPU. In this stock binary
    subprocess.Popen PID is the watchdog, whose CPU and IO stayed near zero.
    We walk /proc/PID/task/PID/children to include actual clickhouse-server.
    """
    pids = proc_tree(pid)
    cpu = cpu_user = cpu_system = 0.0
    status_kib = {'VmRSS': 0, 'VmHWM': 0, 'RssAnon': 0}
    io = {}
    sources = {}
    for child_pid in pids:
        pp = pathlib.Path('/proc') / str(child_pid)
        raw = (pp / 'stat').read_text()
        rest = raw[raw.rfind(')') + 2:].split()
        if len(rest) < 22:
            raise AssertionError('Truncated Linux proc stat ' + str(child_pid))
        user_ticks, system_ticks = int(rest[11]), int(rest[12])
        part_user = user_ticks / CPU_HZ
        part_system = system_ticks / CPU_HZ
        cpu_user += part_user
        cpu_system += part_system
        cpu += part_user + part_system
        proc_rss = {}
        for row in (pp / 'status').read_text().splitlines():
            if row.startswith(('VmRSS:', 'VmHWM:', 'RssAnon:')):
                key, value = row.split(':', 1)
                words = value.strip().split()
                if len(words) != 2 or words[1] != 'kB':
                    raise AssertionError('Unexpected process memory units: ' + row)
                proc_rss[key] = int(words[0])
                status_kib[key] += proc_rss[key]
        proc_io = {}
        for line in (pp / 'io').read_text().splitlines():
            if ':' in line:
                key, value = line.split(':', 1)
                proc_io[key] = int(value.strip())
                io[key] = io.get(key, 0) + proc_io[key]
        sources[str(child_pid)] = {'cpu_s': round(part_user+part_system, 5),
                                   'memory_kib': proc_rss, 'io': proc_io}
    status = {key: str(kib) + ' kB' for key,kib in status_kib.items()}
    return {'cpu_s': cpu, 'cpu_user_s': cpu_user, 'cpu_system_s': cpu_system,
            'at_monotonic_s': time.monotonic(), 'status': status,
            'io': io, 'pids': pids, 'sources': sources}


def difference(before, after):
    dt = after['at_monotonic_s'] - before['at_monotonic_s']
    cpu = after['cpu_s'] - before['cpu_s']
    if before['pids'] != after['pids']:
        raise AssertionError('Process tree changed during phase: '
                             + repr((before['pids'],after['pids'])))
    if dt <= 0 or cpu < 0:
        raise AssertionError('Bad /proc monotonic counter delta')
    return {'cpu_s': round(cpu, 5), 'wall_s': round(dt, 5),
            'cores_used': round(cpu/dt, 4),
            'user_cpu_s': round(after['cpu_user_s'] - before['cpu_user_s'], 5),
            'system_cpu_s': round(after['cpu_system_s'] - before['cpu_system_s'], 5),
            'write_bytes_delta': after['io'].get('write_bytes', 0) - before['io'].get('write_bytes', 0),
            'rchar_delta': after['io'].get('rchar', 0) - before['io'].get('rchar', 0),
            'wchar_delta': after['io'].get('wchar', 0) - before['io'].get('wchar', 0),
            'rss_after': after['status'].get('VmRSS'),
            'rss_peak_after': after['status'].get('VmHWM'),
            'rss_anon_after': after['status'].get('RssAnon')}


def config_xml(root, port, tcp):
    return f"""<clickhouse>
      <logger><level>warning</level><console>true</console></logger>
      <listen_host>127.0.0.1</listen_host><http_port>{port}</http_port><tcp_port>{tcp}</tcp_port>
      <path>{root}/data/</path><tmp_path>{root}/tmp/</tmp_path>
      <user_files_path>{root}/userfiles/</user_files_path>
      <max_server_memory_usage>2800000000</max_server_memory_usage>
      <users><default><password></password><networks><ip>127.0.0.1</ip></networks>
          <profile>default</profile><quota>default</quota></default></users>
      <profiles><default><allow_experimental_time_series_table>1</allow_experimental_time_series_table>
          <enable_time_series_aggregate_functions>1</enable_time_series_aggregate_functions>
          <max_threads>2</max_threads><max_memory_usage>1800000000</max_memory_usage>
          <log_queries>1</log_queries></default></profiles>
      <quotas><default><interval><duration>3600</duration></interval></default></quotas>
      <query_log><database>system</database><table>query_log</table>
          <flush_interval_milliseconds>1000</flush_interval_milliseconds></query_log>
      <part_log><database>system</database><table>part_log</table>
          <flush_interval_milliseconds>1000</flush_interval_milliseconds></part_log>
      <default_profile>default</default_profile><default_database>default</default_database>
    </clickhouse>"""


def boot(binary, phase_dir, port):
    for name in ('data', 'tmp', 'userfiles'):
        (phase_dir / name).mkdir(parents=True, exist_ok=False)
    conf = phase_dir / 'config.xml'
    conf.write_text(config_xml(phase_dir, port, port + 1000))
    log = (phase_dir / 'server.log').open('w')
    proc = subprocess.Popen([binary, 'server', '--config-file=' + str(conf)],
                            stdout=log, stderr=subprocess.STDOUT)
    endpoint = f'http://127.0.0.1:{port}/'
    for i in range(90):
        if proc.poll() is not None:
            log.close()
            raise RuntimeError(f'Server exited early, phase log {phase_dir / "server.log"}')
        try:
            with urllib.request.urlopen(endpoint + 'ping', timeout=1) as response:
                if response.read().strip() == b'Ok.':
                    return proc, log, endpoint
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(.2)
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    log.close()
    raise RuntimeError('Cannot connect to isolated ClickHouse HTTP endpoint')


def shutdown(proc, log):
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=18)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    if log is not None and not log.closed:
        log.close()


def inner_names(endpoint):
    uuid = base.table_uuid(endpoint, TABLE)
    return {kind: f'default.' + chr(96) + f'.inner_id.{kind}.{uuid}' + chr(96)
            for kind in ('tags', 'samples', 'timeranges')}


def assert_physical(endpoint, variant):
    snap = base.physical(endpoint, TABLE)
    want = 2 * SLOTS * STEPS
    if snap['physical_sample_points'] != want:
        raise AssertionError(f'{variant}: sample points {snap["physical_sample_points"]} != {want}')
    for kind in ('tags', 'samples', 'timeranges'):
        is_lc = 'LowCardinality' in snap[kind]['id_type']
        if is_lc != (variant == 'lc'):
            raise AssertionError(f'{variant} inner {kind} wrong ID encoding: {snap[kind]["id_type"]}')
    return snap


def check_native(endpoint):
    # Full source history must be visible at 96 x 1m steps. The original
    # 30-minute selector was only equivalent for 12 steps.
    end_epoch = base.BASE + (STEPS-1)*60
    expr = f'avg by(scope)(avg_over_time({base.METRIC}[4h]))'
    qtext = (f'SELECT * FROM prometheusQueryRange({TABLE},{base.q(expr)},'
             f'{end_epoch},{end_epoch},60) SETTINGS max_threads=2,use_query_cache=0 FORMAT JSONEachRow')
    samples = base.normalize(base.sql(endpoint,qtext))
    actual = {}
    for (scope, time_str), value in samples.items():
        parsed_epoch = int(base.dt.datetime.fromisoformat(time_str.replace(' ','T'))
                           .replace(tzinfo=base.dt.timezone.utc).timestamp())
        if parsed_epoch != end_epoch or scope in actual:
            raise AssertionError('Wrong native timestamp or duplicate scope')
        actual[scope] = value
    expected = base.expected(SLOTS, STEPS)
    if set(actual) != set(expected):
        raise AssertionError(f'Native labels differ: actual {actual}, expected {expected}')
    for key in expected:
        if not math.isclose(actual[key], expected[key], rel_tol=1e-11, abs_tol=1e-8):
            raise AssertionError(f'Wrong native {key}: {actual[key]} vs {expected[key]}')
    return actual


def query_log(endpoint, qids):
    base.sql(endpoint, 'SYSTEM FLUSH LOGS')
    for attempt in range(7):
        data = base.jsonrows(endpoint,
            "SELECT query_id,query_duration_ms,ProfileEvents,exception_code "
            "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'r02cpu_')")
        rows = {r['query_id']:r for r in data}
        if all(x in rows for x in qids):
            return {i:rows[i] for i in qids}
        time.sleep(.2*(attempt+1))
    raise AssertionError(f'Only {len(set(qids)&set(rows))} of {len(qids)} INSERT QueryFinish entries')


def one_phase(binary, root, phase, variant, blocks, hashes, port):
    phase_dir = root / ('phase-' + phase + '-' + variant)
    phase_dir.mkdir(parents=True, exist_ok=False)
    proc, output, endpoint = None, None, None
    result = {'phase': phase, 'mode': variant, 'input_sha256': hashes,
              'points_expected': 2*SLOTS*STEPS, 'server_restarted':True,
              'errors': [], 'passed':False, 'inserts': [], 'merges': [], 'metrics':{}}
    try:
        proc, output, endpoint = boot(binary, phase_dir, port)
        result['pid'] = proc.pid
        result['proc_boot'] = measure(proc.pid)
        create = base.tables_ddl(TABLE, TABLE)
        base.sql(endpoint, create[0] if variant == 'lc' else create[1])
        inner = inner_names(endpoint)
        # Prevent background merges from mixing write and merge CPU intervals.
        for name in inner.values():
            base.sql(endpoint, 'SYSTEM STOP MERGES ' + name)
        time.sleep(.35)
        idlestart = measure(proc.pid)
        time.sleep(.35)
        idleend = measure(proc.pid)
        result['metrics']['prewrite_idle'] = difference(idlestart, idleend)
        qids = []
        before_write = measure(proc.pid)
        for i, block in enumerate(blocks):
            qid = 'r02cpu_' + phase + '_' + variant + '_ins_' + str(i)
            ms, _ = base.request(endpoint,
                f'INSERT INTO {TABLE}(metric_name,tags,samples) FORMAT JSONEachRow\n' + block, qid)
            qids.append(qid)
            result['inserts'].append({'batch': i, 'client_ms':round(ms,3),
                'query_id':qid, 'sha256':hashes[i], 'bytes':len(block)})
        after_write = measure(proc.pid)
        result['metrics']['ingest_only'] = difference(before_write, after_write)
        # Query-log collection itself has CPU cost, so collect it after snapshots.
        before_physical = assert_physical(endpoint, variant)
        result['physical_before_merges'] = before_physical
        result['insert_query_log'] = query_log(endpoint, qids)
        if len(result['insert_query_log']) != STEPS:
            raise AssertionError('Missing insert logs')
        parts_before = [before_physical[k]['parts'] for k in inner]
        if any(p < 2 for p in parts_before):
            raise AssertionError('No meaningful insert parts to merge ' + repr(parts_before))
        # Begin CPU accounting before START MERGES; it may schedule work.
        before_merge = measure(proc.pid)
        base.sql(endpoint, 'SYSTEM START MERGES')
        for kind, name in inner.items():
            ms, _ = base.request(endpoint,
                f'OPTIMIZE TABLE {name} FINAL SETTINGS optimize_throw_if_noop=0',
                'r02cpu_' + phase + '_' + variant + '_merge_' + kind)
            result['merges'].append({'kind':kind, 'wall_ms_client':round(ms,3)})
        after_merge = measure(proc.pid)
        result['metrics']['start_merges_and_optimize'] = difference(before_merge, after_merge)
        result['physical_after_merges'] = assert_physical(endpoint, variant)
        if any(result['physical_after_merges'][k]['parts'] != 1 for k in inner):
            raise AssertionError('Missing final one-part physical merge')
        before_query = measure(proc.pid)
        result['native'] = check_native(endpoint)
        after_query = measure(proc.pid)
        result['metrics']['native_oracle_query'] = difference(before_query, after_query)
        result['part_log'] = base.part_log(endpoint,[base.table_uuid(endpoint,TABLE)])
        if not result['part_log']['available']:
            raise AssertionError('system.part_log not available')
        # A single full query per phase is a correctness gate, not a p95.
        result['passed'] = True
        print('R02_CPU_PHASE',json.dumps({
            'phase':phase,'mode':variant,'write_cpu_s':result['metrics']['ingest_only']['cpu_s'],
            'write_wall_s':result['metrics']['ingest_only']['wall_s'],
            'merge_cpu_s':result['metrics']['start_merges_and_optimize']['cpu_s'],
            'merge_wall_s':result['metrics']['start_merges_and_optimize']['wall_s'],
            'bytes_after': {k:result['physical_after_merges'][k]['bytes'] for k in inner},
            'points':result['physical_after_merges']['physical_sample_points'],
            'passed':True},separators=(',',':')),flush=True)
    except Exception as exc:
        result['errors'].append(str(exc)[:2400])
        print('R02_CPU_PHASE_ERROR',json.dumps({'phase':phase,'mode':variant,
            'errors':result['errors']},separators=(',',':')),flush=True)
    finally:
        # Persist even failure, and guarantee the next phase runs a fresh process.
        (phase_dir / 'report.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        shutdown(proc, output)
    if not result['passed']:
        raise RuntimeError(f'Phase {phase} failed: {result["errors"]}')
    return result


def summarize(phase_results):
    out = {'phases': {}, 'ratios': {}, 'errors': [], 'decision': 'INCONCLUSIVE'}
    if len(phase_results)!=4:
        out['errors'].append('Expected four phases');return out
    for phase, variant in PHASES:
        if phase_results[phase]['mode'] != variant or not phase_results[phase]['passed']:
            out['errors'].append(f'Invalid phase {phase}')
    names = ['ingest_only', 'start_merges_and_optimize']
    for name in names:
        lc = [phase_results[x]['metrics'][name] for x in ('A0','A3')]
        raw = [phase_results[x]['metrics'][name] for x in ('B1','B2')]
        metric = {}
        for key in ('cpu_s','wall_s','write_bytes_delta'):
            a = statistics.mean(float(x[key]) for x in lc)
            b = statistics.mean(float(x[key]) for x in raw)
            delta = abs(float(lc[0][key])-float(lc[1][key]))/a if a else float('inf')
            metric[key] = {'lc':round(a,5), 'raw':round(b,5),
                           'raw_over_lc':round(b/a,5) if a else None,
                           'lc_control_drift':round(delta,5)}
        if any(metric[key]['lc_control_drift'] > .25 for key in ('cpu_s','wall_s')):
            out['errors'].append(f'Control A/A CPU/wall drift >25% for {name}')
        out['ratios'][name] = metric
    # A failing drift gate invalidates quantitative conclusions, not stored raw CPU.
    out['valid'] = len(out['errors'])==0
    if out['valid']:
        effects = []
        for name in names:
            m = out['ratios'][name]
            for key in ('cpu_s','wall_s'):
                effect = abs(m[key]['raw_over_lc']-1)
                drift = m[key]['lc_control_drift']
                effects.append(effect > max(.05,drift))
        out['decision'] = 'INCONCLUSIVE_REPLICATION_NEEDED' if any(effects) else 'NO_EFFECT'
    else:
        out['decision'] = 'INVALID_METHOD'
    return out


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--binary',required=True)
    p.add_argument('--outdir',required=True)
    p.add_argument('--port',type=int,default=18191)
    p.add_argument('--batches',type=int,default=12)
    a=p.parse_args()
    if a.batches not in (12,96):
        raise AssertionError('Only pinned 12 or 96 chronological batches allowed')
    global STEPS
    STEPS = a.batches
    root=pathlib.Path(a.outdir)
    root.mkdir(parents=True,exist_ok=True)
    if not pathlib.Path(a.binary).exists():
        raise FileNotFoundError(a.binary)
    blocks=[base.fixture_batch(i,SLOTS) for i in range(STEPS)]
    hashes=[hashlib.sha256(block.encode()).hexdigest() for block in blocks]
    all_sha=hashlib.sha256(''.join(blocks).encode()).hexdigest()
    report={'schema_version':1,'question_id':'R02-small-batch-raw-lc-write-v1',
            'subexperiment':'fresh-process-whole-CPU-ABBA-'+str(STEPS)+'batches-v3',
            'source_sha':SOURCE,'slots_per_scope':SLOTS,'batches':STEPS,
            'physical_points_per_phase':2*SLOTS*STEPS,
            'input_sha256':hashes,'combined_fixture_sha256':all_sha,
            'phases':{},'comparison':{},'errors':[], 'passed':False}
    try:
        for phase, mode in PHASES:
            report['phases'][phase]=one_phase(a.binary,root,phase,mode,blocks,hashes,a.port)
            (root/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        fixture_sets={tuple(x['input_sha256']) for x in report['phases'].values()}
        if len(fixture_sets)!=1:
            raise AssertionError('Fixture differs across ABBA phases')
        reference=report['phases']['A0']['native']
        for phase,record in report['phases'].items():
            observed=record['native']
            if set(observed)!=set(reference):
                raise AssertionError('Native labels differ in '+phase)
            for label in reference:
                if not math.isclose(reference[label],observed[label],abs_tol=1e-8,rel_tol=1e-11):
                    raise AssertionError('Native numeric mismatch '
                                         +phase+' '+label+': '+repr((reference[label],observed[label])))
        report['comparison']=summarize(report['phases'])
        report['passed']=report['comparison'].get('valid',False)
        if not report['passed']:
            report['errors'].extend(report['comparison']['errors'])
    except Exception as exc:
        report['errors'].append(str(exc)[:2400])
    finally:
        (root/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        print('R02_CPU_ABBA_DONE',json.dumps({'passed':report['passed'],
            'decision':report['comparison'].get('decision'),
            'ratios':report['comparison'].get('ratios'),
            'errors':report['errors']},separators=(',',':')),flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
