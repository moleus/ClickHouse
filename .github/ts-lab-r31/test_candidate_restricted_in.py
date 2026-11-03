#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).parent))
from candidate_restricted_in import restrict_time_ranges_in_to_candidates, top_level_comma_parts

SOURCE=(
"SELECT timeSeriesStoreTags(id, tags, '__name__', metric_name) "
"FROM default.`.inner_id.tags.abc` WHERE "
"and(and(equals(metric_name, 'm'), equals(arrayElement(tags, 'stage'), 's1')), "
"or(in(id, (SELECT id FROM default.`.inner_id.timeranges.abc` WHERE "
"and(greaterOrEquals(max_time, toDateTime64('2026-10-19', 3)), "
"lessOrEquals(min_time, toDateTime64('2026-10-20',3)), "
"and(greaterOrEquals(id, tuple(1, toUUID('00000000-0000-0000-0000-000000000000'))),"
" lessOrEquals(id, tuple(1, toUUID('ffffffff-ffff-ffff-ffff-ffffffffffff'))))))), "
"notEquals(tupleElement(id,1),sipHash64('m'))))"
)


class RestrictionTests(unittest.TestCase):
    def test_positive_tag_condition_kept_inside_right_set(self):
        out=restrict_time_ranges_in_to_candidates(SOURCE)
        self.assertIn("SELECT id FROM default.`.inner_id.tags.abc` WHERE "
                      "and(equals(metric_name, 'm'), "
                      "equals(arrayElement(tags, 'stage'), 's1'))",out)
        self.assertEqual(out.count("default.`.inner_id.tags.abc`"),2)
        self.assertEqual(out.count("default.`.inner_id.timeranges.abc`"),1)
        self.assertIn("notEquals(tupleElement(id,1),sipHash64('m'))",out)
        self.assertEqual(out.count("greaterOrEquals(max_time"),1)
        self.assertEqual(out.count("lessOrEquals(min_time"),1)
        self.assertEqual(out.count("timeSeriesStoreTags"),1)

    def test_uses_same_left_predicate_not_a_hardcoded_label(self):
        second=SOURCE.replace("'stage'","'project'").replace("'s1'","'project-00'")
        out=restrict_time_ranges_in_to_candidates(second)
        self.assertIn("equals(arrayElement(tags, 'project'), 'project-00')",out)

    def test_rejects_missing_legacy_escape(self):
        with self.assertRaisesRegex(ValueError,"outer filter"):
            restrict_time_ranges_in_to_candidates(SOURCE.replace("or(in(id, ","in(id, ",1))

    def test_rejects_other_table_and_repeated_time_selectors(self):
        with self.assertRaisesRegex(ValueError, "TAGS"):
            restrict_time_ranges_in_to_candidates(SOURCE.replace(".inner_id.tags.",".inner_id.other."))
        with self.assertRaisesRegex(ValueError,"one TimeRanges"):
            restrict_time_ranges_in_to_candidates(SOURCE.replace(
                "in(id, (SELECT id FROM default.`.inner_id.timeranges.",
                "in(id, (SELECT id FROM default.`.inner_id.timeranges.abc` WHERE 1) AND "
                "in(id, (SELECT id FROM default.`.inner_id.timeranges.",1))

    def test_top_level_sql_argument_split_handles_nested_literals(self):
        self.assertEqual(top_level_comma_parts("eq(x, 'a,b'), or(in(id,(select 1,2)),not(z))"),
                         ["eq(x, 'a,b')","or(in(id,(select 1,2)),not(z))"])

if __name__=="__main__":
    unittest.main()
