import unittest
from ts_lab_state import validate_handoff, validate_claim

GOOD = dict(
    schema_version=1, session_id='session-20261103-001', status='handoff',
    branch='lab/ts-cbo-adaptive-20261103', branch_head='a'*40,
    last_verified_utc='2026-11-03T09:10:00Z', summary='Test result',
    completed=['source reviewed'], negative_results=[], active_run_ids=[1234],
    next_action={'question_id':'R01','step':'Verify fixture','stop_condition':'On mismatch'},
    blocked_by=[])

class TestHandoff(unittest.TestCase):
    def test_valid(self): self.assertEqual(validate_handoff(GOOD), [])
    def test_missing(self):
        x=dict(GOOD); del x['next_action']; self.assertTrue(validate_handoff(x))
    def test_auth(self):
        x=dict(GOOD); x['summary']='ghp_'+'A'*25; self.assertTrue(validate_handoff(x))
    def test_wrong_branch(self):
        x=dict(GOOD); x['branch']='master'; self.assertTrue(validate_handoff(x))
    def test_broken_action(self):
        x=dict(GOOD); x['next_action']={'step':'Do'}; self.assertTrue(validate_handoff(x))


CLAIM = dict(id='R01-k8s-mixed-c1', question='Hypothesis', owner='web-agent',
    state='claimed', source_sha='a'*40, baseline='binary and run SHA',
    candidate='raw ID', fixture='synthetic', single_variable='UUID encoding',
    math_bound='scan work reduction', falsifier='equal/slower or OOM',
    test_matrix='14d native', runner_budget='one runner 15 min',
    planned_workflow='.github/workflows/ts-lab-r01.yml', dependencies=[])

class TestClaim(unittest.TestCase):
    def test_valid(self): self.assertEqual(validate_claim(CLAIM), [])
    def test_missing(self):
        x=dict(CLAIM); del x['falsifier']; self.assertTrue(validate_claim(x))
    def test_wrong_sha(self):
        x=dict(CLAIM); x['source_sha']='v1'; self.assertTrue(validate_claim(x))



class TestSnapshotCoverage(unittest.TestCase):
    """Prevent an incomplete CI scan from enabling duplicate research jobs."""

    def make_run(self, number, branch, status='in_progress'):
        return dict(id=number, name='test-' + str(number), head_branch=branch,
                    status=status, html_url='https://github.com/moleus/ClickHouse/actions/runs/' + str(number))

    def test_legacy_branch_and_queued_states(self):
        from unittest.mock import patch
        from urllib.parse import parse_qs, urlparse
        from ts_lab_state import snapshot
        old = self.make_run(11, 'lab/timeseries-fastpath-ci')
        current = self.make_run(12, 'lab/ts-cbo-adaptive-20261103')
        irrelevant = self.make_run(13, 'renovate/dependency')
        waiting = self.make_run(14, 'lab/timeseries-layout-results', 'waiting')

        def fake_fetch(url, token=None):
            if '/commits?' in url:
                return [dict(sha='a'*40, commit=dict(message='update'))]
            if '/contents/' in url:
                return []
            params = parse_qs(urlparse(url).query)
            status = params['status'][0]
            if status == 'in_progress':
                return dict(workflow_runs=[old, current, irrelevant], total_count=3)
            if status == 'waiting':
                return dict(workflow_runs=[waiting], total_count=1)
            return dict(workflow_runs=[], total_count=0)

        with patch('ts_lab_state.fetch_json', side_effect=fake_fetch):
            result = snapshot('moleus/ClickHouse', 'lab/ts-cbo-adaptive-20261103')
        self.assertEqual({r['id'] for r in result['live_runs']}, {11, 12})
        self.assertEqual({r['id'] for r in result['queued_runs']}, {14})
        self.assertTrue(result['live_inventory_complete'])
        self.assertTrue(result['queue_inventory_complete'])

    def test_running_pages_are_not_silently_dropped(self):
        from unittest.mock import patch
        from urllib.parse import parse_qs, urlparse
        from ts_lab_state import snapshot
        hundred = [self.make_run(i, 'lab/timeseries-fastpath-ci') for i in range(1,101)]
        last = self.make_run(101, 'lab/ts-cbo-adaptive-20261103')

        def fake_fetch(url, token=None):
            if '/commits?' in url:
                return [dict(sha='b'*40, commit=dict(message='update'))]
            if '/contents/' in url:
                return []
            params = parse_qs(urlparse(url).query)
            if params['status'][0] == 'in_progress':
                return dict(workflow_runs=hundred if params['page'][0] == '1' else [last],
                            total_count=101)
            return dict(workflow_runs=[], total_count=0)

        with patch('ts_lab_state.fetch_json', side_effect=fake_fetch):
            result = snapshot('moleus/ClickHouse', 'lab/ts-cbo-adaptive-20261103', limit=12)
        self.assertEqual(len(result['live_runs']), 101)
        self.assertEqual(result['live_runs'][-1]['id'], 101)
        self.assertTrue(result['live_inventory_complete'])

    def test_page_cap_is_reported_as_incomplete(self):
        from unittest.mock import patch
        from ts_lab_state import fetch_action_runs
        data = dict(workflow_runs=[self.make_run(i, 'lab/timeseries-fastpath-ci')
                                   for i in range(100)], total_count=400)
        with patch('ts_lab_state.fetch_json', return_value=data) as mocked:
            runs, complete = fetch_action_runs('https://api.github.com/repos/moleus/ClickHouse',
                                               'in_progress', page_cap=2)
        self.assertFalse(complete)
        self.assertEqual(len(runs), 200)
        self.assertEqual(mocked.call_count, 2)

if __name__ == '__main__': unittest.main()
