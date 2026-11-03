import pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from probe_nested_selector_plan import parse_selector_sql, fixture, ANCHOR

class TestNestedPlan(unittest.TestCase):
    def test_extract(self):
        log = ('2026.11.03 00:00:00 [1] <Debug> StorageTimeSeriesSelector: Will execute query:\n'
               'SELECT timeSeriesStoreTags(id,tags) AS series_id\nFROM x\n'
               '2026.11.03 00:00:00 [1] <Debug> Other: next')
        self.assertIn('FROM x', parse_selector_sql(log))
        self.assertEqual(parse_selector_sql(log).count('SELECT'), 1)
    def test_outer_samples_select_does_not_need_series_id_alias(self):
        log = (ANCHOR + '\nSELECT _CAST(id, \'Tuple(UInt64, UUID)\') AS id '
               'FROM (SELECT samples FROM x WHERE id IN '
               '(SELECT timeSeriesStoreTags(id, tags) FROM y))\n'
               '2026.11.03 00:00:00 [1] <Debug> Next: line')
        result = parse_selector_sql(log)
        self.assertIn('timeSeriesStoreTags', result)
        self.assertNotIn('series_id', result)

    def test_no_debug(self):
        with self.assertRaisesRegex(ValueError, 'Missing'): parse_selector_sql('irrelevant')
    def test_stateful_required(self):
        with self.assertRaisesRegex(ValueError, 'stateful'):
            parse_selector_sql(ANCHOR + '\nSELECT id AS series_id FROM x\n')
    def test_stable_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'f.jsonl'
            a = fixture(path, 101)
            b = fixture(path, 101)
            self.assertEqual(a, b)
            self.assertEqual(a['samples'], 404)

if __name__ == '__main__': unittest.main()
