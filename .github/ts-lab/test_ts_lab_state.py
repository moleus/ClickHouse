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

if __name__ == '__main__': unittest.main()
