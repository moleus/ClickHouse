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


def fetch_json(url, token=None):
    headers = {'Accept': 'application/vnd.github+json',
               'User-Agent': 'public-timeseries-lab-resume/1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def snapshot(repo, branch, token=None, limit=12):
    base = 'https://api.github.com/repos/' + urllib.parse.quote(repo, safe='/')
    escaped_branch = urllib.parse.quote(branch, safe='')
    recent = fetch_json(base + '/commits?sha=' + escaped_branch + '&per_page=8', token)
    running = fetch_json(base + '/actions/runs?status=in_progress&per_page=100', token)
    pending = fetch_json(base + '/actions/runs?status=queued&per_page=100', token)
    results = {'repository': repo, 'branch': branch,
               'checked_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'head_sha': recent[0]['sha'] if recent else None,
               'recent_commits': [
                   {'sha': r['sha'], 'subject': r['commit']['message'].splitlines()[0]}
                   for r in recent], 'live_runs': [], 'queued_runs': []}
    for source, key in ((running, 'live_runs'), (pending, 'queued_runs')):
        relevant = [r for r in source.get('workflow_runs', [])
                    if r.get('head_branch') == branch or
                    r.get('head_branch', '').startswith('lab/ts-')]
        results[key] = [
            {'id': r['id'], 'name': r['name'], 'branch': r['head_branch'],
             'status': r['status'], 'url': r['html_url']}
            for r in relevant[:limit]]
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
    args = parser.parse_args(argv)
    if args.command == 'validate-handoff':
        errors = validate_handoff(json.loads(args.file.read_text(encoding='utf-8')))
        print('\n'.join(errors) if errors else 'VALID handoff')
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
