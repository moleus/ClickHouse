import pathlib, sys, tempfile, unittest
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from probe_nested_selector_plan import parse_selector_sql, extract_ids_subquery, estimated_join_rows, fixture, settings_for, ANCHOR

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

    def test_extract_nested_in_subquery(self):
        generated = ("SELECT * FROM samples WHERE in(id, (SELECT "
                     "timeSeriesStoreTags(id, tags, '__name__', metric_name) "
                     "FROM tags ALL LEFT JOIN (SELECT id FROM ranges WHERE "
                     "equals(key, 'a)b')) USING (id))) AND notEmpty(samples)")
        ids = extract_ids_subquery(generated)
        self.assertTrue(ids.startswith('SELECT timeSeriesStoreTags('))
        self.assertIn("equals(key, 'a)b')", ids)
        self.assertIn('LEFT JOIN', ids)
        self.assertNotIn('notEmpty(samples)', ids)

    def test_explain_matches_native_forced_join_settings(self):
        for mode in ('off_merge', 'auto_merge'):
            settings = settings_for(mode)
            self.assertIn("join_algorithm='full_sorting_merge'", settings)
            self.assertIn('optimize_read_in_order=1', settings)
        for mode in ('off_hash', 'auto_hash'):
            settings = settings_for(mode)
            self.assertIn("join_algorithm='hash'", settings)
            self.assertIn('optimize_read_in_order=0', settings)

    def test_missing_in_source(self):
        with self.assertRaisesRegex(ValueError, 'No native SAMPLES'):
            extract_ids_subquery('SELECT id FROM samples')

    def test_estimated_input_units(self):
        plan = ('Type: left | Algorithm: FullSortingMergeJoin\n'
                'Left: rows estimated 1.35 thousand\n'
                'Right: rows estimated 892.00\n')
        self.assertEqual(estimated_join_rows(plan), {'left': 1350.0,'right':892.0})
        self.assertEqual(estimated_join_rows('Left: rows estimated 0.5 million\n'
                                             'Right: rows estimated 10 billion'),
                         {'left':500000.0,'right':10000000000.0})
        with self.assertRaisesRegex(ValueError,'Right'):
            estimated_join_rows('Left: rows estimated 12')

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
