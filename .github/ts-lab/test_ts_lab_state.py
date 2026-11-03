import unittest
from ts_lab_state import validate_handoff

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

if __name__ == '__main__': unittest.main()
