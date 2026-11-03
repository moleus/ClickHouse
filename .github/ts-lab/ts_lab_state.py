#!/usr/bin/env python3
"""Read-only public TimeSeries lab status and strict handoff validation.

This program does not run source fetched from GitHub or write to GitHub.
GitHub MCP approval is still required for authenticated repository changes.
"""
import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = 'moleus/ClickHouse'
BRANCH = 'lab/ts-cbo-adaptive-20261103'
STATUS = {'working', 'waiting_ci', 'blocked_auth', 'handoff', 'done'}
SHA = re.compile(r'^[a-f0-9]{40}$')
ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{2,99}$')
SECRET = re.compile(r'(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,})')


def validate_handoff(data):
    errors = []
    if not isinstance(data, dict):
        return ['Expected a JSON object']
    required = ('schema_version', 'session_id', 'status', 'branch', 'branch_head',
                'last_verified_utc', 'summary', 'completed', 'negative_results',
                'active_run_ids', 'next_action', 'blocked_by')
    for key in required:
        if key not in data:
            errors.append('Missing: ' + key)
    if data.get('schema_version') != 1:
        errors.append('schema_version must be 1')
    if not isinstance(data.get('session_id'), str) or not ID.fullmatch(data['session_id']):
        errors.append('Invalid session_id')
    if data.get('branch') != BRANCH:
        errors.append('Not the shared research branch')
    if data.get('status') not in STATUS:
        errors.append('Invalid status')
    if not isinstance(data.get('branch_head'), str) or not SHA.fullmatch(data['branch_head']):
        errors.append('branch_head must be a full 40-character commit SHA')
    try:
        when = dt.datetime.fromisoformat(data['last_verified_utc'].replace('Z', '+00:00'))
        if when.utcoffset() != dt.timedelta(0):
            errors.append('last_verified_utc must be UTC')
    except (KeyError, AttributeError, ValueError, TypeError):
        errors.append('Invalid last_verified_utc')
    if not isinstance(data.get('summary'), str) or not data['summary'].strip():
        errors.append('summary must be a nonempty string')
    for key in ('completed', 'negative_results', 'active_run_ids', 'blocked_by'):
        if not isinstance(data.get(key), list):
            errors.append(key + ' must be an array')
    if isinstance(data.get('active_run_ids'), list):
        if not all(type(v) is int and v > 0 for v in data['active_run_ids']):
            errors.append('active_run_ids must be positive integers')
    action = data.get('next_action')
    if not isinstance(action, dict) or not all(
        isinstance(action.get(k), str) and action[k].strip()
        for k in ('question_id', 'step', 'stop_condition')
    ):
        errors.append('next_action requires question_id, step, stop_condition')
    if SECRET.search(json.dumps(data)):
        errors.append('Potential token/secret in PUBLIC handoff')
    return errors


def validate_claim(data):
    errors = []
    if not isinstance(data, dict):
        return ['Expected a JSON object']
    required = ('id', 'question', 'owner', 'state', 'source_sha', 'baseline',
                'candidate', 'fixture', 'single_variable', 'math_bound',
                'falsifier', 'test_matrix', 'runner_budget',
                'planned_workflow', 'dependencies')
    for key in required:
        if key not in data:
            errors.append('Missing: ' + key)
    if not isinstance(data.get('id'), str) or not ID.fullmatch(data['id']):
        errors.append('Invalid claim ID')
    if data.get('state') != 'claimed':
        errors.append('Initial claim state must be claimed; changes use new evidence files')
    if not isinstance(data.get('source_sha'), str) or not SHA.fullmatch(data['source_sha']):
        errors.append('source_sha must be a full 40-character SHA')
    for key in ('question', 'owner', 'baseline', 'candidate', 'fixture',
                'single_variable', 'math_bound', 'falsifier', 'test_matrix',
                'runner_budget', 'planned_workflow'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(key + ' must be a nonempty string')
    if not isinstance(data.get('dependencies'), list):
        errors.append('dependencies must be an array')
    if SECRET.search(json.dumps(data)):
        errors.append('Potential token/secret in PUBLIC claim')
    return errors


ROOT_ID = re.compile(r'^R[0-9]{2}
    errors = []
    folder = root / '.github' / 'ts-lab'
    for path in sorted((folder / 'handoffs').glob('*.json')):
        try:
            for error in validate_handoff(json.loads(path.read_text(encoding='utf-8'))):
                errors.append(str(path) + ': ' + error)
        except (ValueError, OSError) as exc:
            errors.append(str(path) + ': ' + str(exc))
    claims = {}
    for path in sorted((folder / 'claims').glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and path.stem != data.get('id'):
                errors.append(str(path) + ': name differs from claim ID')
            for error in validate_claim(data):
                errors.append(str(path) + ': ' + error)
            if isinstance(data, dict):
                claims[path.stem] = data
        except (ValueError, OSError) as exc:
            errors.append(str(path) + ': ' + str(exc))
    roots = {}
    for path in sorted((folder / 'claims' / 'roots').glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or path.stem != data.get('question_id'):
                errors.append(str(path) + ': root name differs from question_id')
            for error in validate_root_claim(data):
                errors.append(str(path) + ': ' + error)
            if isinstance(data, dict):
                roots[path.stem] = data
        except (ValueError, OSError) as exc:
            errors.append(str(path) + ': ' + str(exc))
    for claim_id in claims:
        question = claim_id.split('-', 1)[0]
        if not ROOT_ID.fullmatch(question):
            errors.append('Claim must start with R plus two digits: ' + claim_id)
            continue
        root_claim = roots.get(question)
        if root_claim is None:
            errors.append('Missing create-only root lock for ' + claim_id)
            continue
        authorized = {root_claim.get('primary_claim_id')}
        authorized.update(root_claim.get('allowed_support_claims', [])
                          if isinstance(root_claim.get('allowed_support_claims'), list) else [])
        if claim_id not in authorized:
            errors.append('Unregistered duplicate root question claim: ' + claim_id)
    for question, root_claim in roots.items():
        primary_id = root_claim.get('primary_claim_id')
        primary = claims.get(primary_id)
        if primary is not None and primary.get('owner') != root_claim.get('owner'):
            errors.append(question + ': root owner differs from primary claim')
    return errors


def fetch_json(url, token=None):
    headers = {'Accept': 'application/vnd.github+json',
               'User-Agent': 'public-timeseries-lab-resume/1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def fetch_action_runs(base, status, token=None, page_cap=10):
    """Read all pages up to an explicit limit; never hide truncation."""
    runs = []
    for page in range(1, page_cap + 1):
        data = fetch_json(
            base + '/actions/runs?status=' + urllib.parse.quote(status) +
            '&per_page=100&page=' + str(page), token)
        batch = data.get('workflow_runs', [])
        runs.extend(batch)
        total = data.get('total_count')
        if len(batch) < 100 or (isinstance(total, int) and len(runs) >= total):
            return runs, True
    return runs, False


def is_research_branch(candidate, current):
    """Legacy lab/timeseries-* jobs are still valid deduplication locks."""
    return isinstance(candidate, str) and (
        candidate == current or candidate.startswith('lab/ts-') or
        candidate.startswith('lab/timeseries-'))


def snapshot(repo, branch, token=None, limit=12):
    base = 'https://api.github.com/repos/' + urllib.parse.quote(repo, safe='/')
    escaped_branch = urllib.parse.quote(branch, safe='')
    recent = fetch_json(base + '/commits?sha=' + escaped_branch + '&per_page=8', token)
    running, live_complete = fetch_action_runs(base, 'in_progress', token)
    pending_by_id = {}
    pending_complete = True
    for state in ('queued', 'waiting', 'requested', 'pending'):
        page, complete = fetch_action_runs(base, state, token)
        pending_complete = pending_complete and complete
        for run in page:
            pending_by_id[run['id']] = run
    results = {'repository': repo, 'branch': branch,
               'checked_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'head_sha': recent[0]['sha'] if recent else None,
               'recent_commits': [
                   {'sha': r['sha'], 'subject': r['commit']['message'].splitlines()[0]}
                   for r in recent], 'live_runs': [], 'queued_runs': [],
               'live_inventory_complete': live_complete,
               'queue_inventory_complete': pending_complete,
               'active_statuses_checked': ['in_progress','queued','waiting','requested','pending']}
    for source, key in ((running, 'live_runs'), (list(pending_by_id.values()), 'queued_runs')):
        relevant = [r for r in source if is_research_branch(r.get('head_branch'), branch)]
        results[key] = [
            {'id': r['id'], 'name': r['name'], 'branch': r['head_branch'],
             'status': r['status'], 'url': r['html_url']}
            for r in relevant]
    for folder, key in (('claims', 'claim_files'), ('handoffs', 'handoff_files')):
        url = base + '/contents/.github/ts-lab/' + folder + '?ref=' + escaped_branch
        try:
            entries = fetch_json(url, token)
            results[key] = sorted(item['name'] for item in entries if item['type'] == 'file')[-limit:]
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            results[key] = []
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('snapshot', help='Public GitHub read: current branch, CI runs, claims, handoffs')
    s.add_argument('--repo', default=REPO)
    s.add_argument('--branch', default=BRANCH)
    s.add_argument('--json', action='store_true', help='Machine-readable output')
    s.add_argument('--fixture', help='Use a local saved snapshot instead of network access')
    v = sub.add_parser('validate-handoff', help='Check a handoff JSON before publishing')
    v.add_argument('file', type=Path)
    c = sub.add_parser('validate-claim', help='Check an atomic GitHub claim JSON')
    c.add_argument('file', type=Path)
    t = sub.add_parser('validate-tree', help='Validate all committed handoffs and claims')
    t.add_argument('root', type=Path, nargs='?', default=Path('.'))
    args = parser.parse_args(argv)
    if args.command == 'validate-handoff':
        errors = validate_handoff(json.loads(args.file.read_text(encoding='utf-8')))
        print('\n'.join(errors) if errors else 'VALID handoff')
        return 1 if errors else 0
    if args.command == 'validate-claim':
        errors = validate_claim(json.loads(args.file.read_text(encoding='utf-8')))
        print('\n'.join(errors) if errors else 'VALID claim')
        return 1 if errors else 0
    if args.command == 'validate-tree':
        errors = validate_tree(args.root)
        print('\n'.join(errors) if errors else 'VALID tree')
        return 1 if errors else 0
    try:
        data = json.loads(Path(args.fixture).read_text()) if args.fixture else snapshot(args.repo, args.branch)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print('Public GitHub read failed: ' + str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    print('Repo:', data['repository'], '/', data['branch'])
    print('HEAD:', data['head_sha'], '\nUTC:', data['checked_at_utc'])
    print('Recent commits:')
    print('CI inventory complete:', data.get('live_inventory_complete'),
          data.get('queue_inventory_complete'))
    for c in data['recent_commits'][:5]:
        print(' ', c['sha'][:10], c['subject'])
    for key in ('live_runs', 'queued_runs'):
        print(key + ':', len(data[key]))
        for r in data[key]:
            print(' ', r['id'], r['branch'], r['name'], r['url'])
    print('Claim files:', ', '.join(data.get('claim_files', [])) or 'none')
    print('Recent handoffs:', ', '.join(data.get('handoff_files', [])) or 'none')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
)


def validate_root_claim(data):
    errors = []
    if not isinstance(data, dict):
        return ['Expected a JSON object']
    for key in ('schema_version', 'question_id', 'primary_claim_id', 'owner',
                'scope', 'allowed_support_claims'):
        if key not in data:
            errors.append('Missing: ' + key)
    if data.get('schema_version') != 1:
        errors.append('schema_version must be 1')
    question = data.get('question_id')
    if not isinstance(question, str) or not ROOT_ID.fullmatch(question):
        errors.append('question_id must be R plus two digits')
    primary = data.get('primary_claim_id')
    if not isinstance(primary, str) or not isinstance(question, str) or not primary.startswith(question + '-'):
        errors.append('primary_claim_id must start with question_id-')
    for key in ('owner', 'scope'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(key + ' must be a nonempty string')
    support = data.get('allowed_support_claims')
    if not isinstance(support, list):
        errors.append('allowed_support_claims must be an array')
    elif not all(isinstance(c, str) and isinstance(question, str) and
                 c.startswith(question + '-') for c in support):
        errors.append('Every support claim must share the root question')
    elif primary in support or len(set(support)) != len(support):
        errors.append('Primary claim cannot be support; duplicate support claim')
    if SECRET.search(json.dumps(data)):
        errors.append('Potential token/secret in PUBLIC root claim')
    return errors


def validate_tree(root):
    errors = []
    folder = root / '.github' / 'ts-lab'
    for path in sorted((folder / 'handoffs').glob('*.json')):
        try:
            for error in validate_handoff(json.loads(path.read_text(encoding='utf-8'))):
                errors.append(str(path) + ': ' + error)
        except (ValueError, OSError) as exc:
            errors.append(str(path) + ': ' + str(exc))
    for path in sorted((folder / 'claims').glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and path.stem != data.get('id'):
                errors.append(str(path) + ': name differs from claim ID')
            for error in validate_claim(data):
                errors.append(str(path) + ': ' + error)
        except (ValueError, OSError) as exc:
            errors.append(str(path) + ': ' + str(exc))
    return errors


def fetch_json(url, token=None):
    headers = {'Accept': 'application/vnd.github+json',
               'User-Agent': 'public-timeseries-lab-resume/1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def fetch_action_runs(base, status, token=None, page_cap=10):
    """Read all pages up to an explicit limit; never hide truncation."""
    runs = []
    for page in range(1, page_cap + 1):
        data = fetch_json(
            base + '/actions/runs?status=' + urllib.parse.quote(status) +
            '&per_page=100&page=' + str(page), token)
        batch = data.get('workflow_runs', [])
        runs.extend(batch)
        total = data.get('total_count')
        if len(batch) < 100 or (isinstance(total, int) and len(runs) >= total):
            return runs, True
    return runs, False


def is_research_branch(candidate, current):
    """Legacy lab/timeseries-* jobs are still valid deduplication locks."""
    return isinstance(candidate, str) and (
        candidate == current or candidate.startswith('lab/ts-') or
        candidate.startswith('lab/timeseries-'))


def snapshot(repo, branch, token=None, limit=12):
    base = 'https://api.github.com/repos/' + urllib.parse.quote(repo, safe='/')
    escaped_branch = urllib.parse.quote(branch, safe='')
    recent = fetch_json(base + '/commits?sha=' + escaped_branch + '&per_page=8', token)
    running, live_complete = fetch_action_runs(base, 'in_progress', token)
    pending_by_id = {}
    pending_complete = True
    for state in ('queued', 'waiting', 'requested', 'pending'):
        page, complete = fetch_action_runs(base, state, token)
        pending_complete = pending_complete and complete
        for run in page:
            pending_by_id[run['id']] = run
    results = {'repository': repo, 'branch': branch,
               'checked_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'head_sha': recent[0]['sha'] if recent else None,
               'recent_commits': [
                   {'sha': r['sha'], 'subject': r['commit']['message'].splitlines()[0]}
                   for r in recent], 'live_runs': [], 'queued_runs': [],
               'live_inventory_complete': live_complete,
               'queue_inventory_complete': pending_complete,
               'active_statuses_checked': ['in_progress','queued','waiting','requested','pending']}
    for source, key in ((running, 'live_runs'), (list(pending_by_id.values()), 'queued_runs')):
        relevant = [r for r in source if is_research_branch(r.get('head_branch'), branch)]
        results[key] = [
            {'id': r['id'], 'name': r['name'], 'branch': r['head_branch'],
             'status': r['status'], 'url': r['html_url']}
            for r in relevant]
    for folder, key in (('claims', 'claim_files'), ('handoffs', 'handoff_files')):
        url = base + '/contents/.github/ts-lab/' + folder + '?ref=' + escaped_branch
        try:
            entries = fetch_json(url, token)
            results[key] = sorted(item['name'] for item in entries if item['type'] == 'file')[-limit:]
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            results[key] = []
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('snapshot', help='Public GitHub read: current branch, CI runs, claims, handoffs')
    s.add_argument('--repo', default=REPO)
    s.add_argument('--branch', default=BRANCH)
    s.add_argument('--json', action='store_true', help='Machine-readable output')
    s.add_argument('--fixture', help='Use a local saved snapshot instead of network access')
    v = sub.add_parser('validate-handoff', help='Check a handoff JSON before publishing')
    v.add_argument('file', type=Path)
    c = sub.add_parser('validate-claim', help='Check an atomic GitHub claim JSON')
    c.add_argument('file', type=Path)
    t = sub.add_parser('validate-tree', help='Validate all committed handoffs and claims')
    t.add_argument('root', type=Path, nargs='?', default=Path('.'))
    args = parser.parse_args(argv)
    if args.command == 'validate-handoff':
        errors = validate_handoff(json.loads(args.file.read_text(encoding='utf-8')))
        print('\n'.join(errors) if errors else 'VALID handoff')
        return 1 if errors else 0
    if args.command == 'validate-claim':
        errors = validate_claim(json.loads(args.file.read_text(encoding='utf-8')))
        print('\n'.join(errors) if errors else 'VALID claim')
        return 1 if errors else 0
    if args.command == 'validate-tree':
        errors = validate_tree(args.root)
        print('\n'.join(errors) if errors else 'VALID tree')
        return 1 if errors else 0
    try:
        data = json.loads(Path(args.fixture).read_text()) if args.fixture else snapshot(args.repo, args.branch)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print('Public GitHub read failed: ' + str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    print('Repo:', data['repository'], '/', data['branch'])
    print('HEAD:', data['head_sha'], '\nUTC:', data['checked_at_utc'])
    print('Recent commits:')
    print('CI inventory complete:', data.get('live_inventory_complete'),
          data.get('queue_inventory_complete'))
    for c in data['recent_commits'][:5]:
        print(' ', c['sha'][:10], c['subject'])
    for key in ('live_runs', 'queued_runs'):
        print(key + ':', len(data[key]))
        for r in data[key]:
            print(' ', r['id'], r['branch'], r['name'], r['url'])
    print('Claim files:', ', '.join(data.get('claim_files', [])) or 'none')
    print('Recent handoffs:', ', '.join(data.get('handoff_files', [])) or 'none')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
