#!/usr/bin/env python3
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).parent))
from bench_membership_in import convert_join_to_in, quoted_parenthesized, diagnostic_alias

NATIVE=(
    "SELECT timeSeriesStoreTags(id, tags, '__name__', metric_name) "
    "FROM default.`.inner_id.tags.abc` ALL LEFT JOIN "
    "(SELECT id, 1 AS matched FROM default.`.inner_id.timeranges.abc` "
    "WHERE and(greaterOrEquals(max_time, toDateTime64('2026-10-19 (midnight)',3)), "
    "lessOrEquals(min_time, toDateTime64('2026-10-20',3)), "
    "equals(tupleElement(id,1),sipHash64('name')))) "
    "AS time_ranges_subquery USING (id) "
    "WHERE and(equals(metric_name, 'name'), "
    "or(equals(time_ranges_subquery.matched, 1), "
    "notEquals(tupleElement(id, 1), sipHash64('name'))))"
)

class R31RewriteTests(unittest.TestCase):
    def test_exact_nested_parentheses_and_literal_are_preserved(self):
        rewritten=convert_join_to_in(NATIVE)
        self.assertNotIn("LEFT JOIN",rewritten)
        self.assertNotIn("AS time_ranges_subquery",rewritten)
        self.assertNotIn("matched",rewritten)
        self.assertIn("in(id, (SELECT id FROM default.`.inner_id.timeranges.abc` WHERE",rewritten)
        self.assertIn("2026-10-19 (midnight)",rewritten)
        self.assertIn("notEquals(tupleElement(id, 1), sipHash64('name'))",rewritten)
        self.assertIn("timeSeriesStoreTags(id, tags, '__name__', metric_name)",rewritten)
        self.assertEqual(rewritten.count(" WHERE "),2)

    def test_diagnostic_alias_does_not_shadow_physical_id(self):
        sql=diagnostic_alias(convert_join_to_in(NATIVE))
        self.assertIn("AS r31_id FROM default.`.inner_id.tags.abc`",sql)
        self.assertIn("in(id,",sql)

    def test_failure_on_missing_legacy_exception(self):
        no_exception=NATIVE.replace("or(equals(time_ranges_subquery.matched, 1), "
                                     "notEquals(tupleElement(id, 1), sipHash64('name')))",
                                    "equals(time_ranges_subquery.matched, 1)")
        with self.assertRaisesRegex(ValueError,"exception"):
            convert_join_to_in(no_exception)

    def test_failure_on_wrong_right_flag(self):
        with self.assertRaisesRegex(ValueError,"matched=1"):
            convert_join_to_in(NATIVE.replace("SELECT id, 1 AS matched","SELECT id, 0 AS matched"))

    def test_failure_on_other_join(self):
        with self.assertRaisesRegex(ValueError,"exactly one"):
            convert_join_to_in(NATIVE.replace(" ALL LEFT JOIN "," LEFT ANY JOIN "))

    def test_parenthesis_parser_does_not_split_quoted(self):
        content="(f('this (not nested)'),and(1,2)) trailing"
        expr,end=quoted_parenthesized(content,0)
        self.assertEqual(expr,"f('this (not nested)'),and(1,2)")
        self.assertEqual(content[end:],") trailing")

if __name__=="__main__":
    unittest.main()
