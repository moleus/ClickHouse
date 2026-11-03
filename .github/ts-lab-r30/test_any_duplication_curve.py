#!/usr/bin/env python3
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).parent))
from bench_any_duplication_curve import LEVELS,table_name,census,target,freeze_time_ranges_before_insert


class CurveTests(unittest.TestCase):
    def test_level_names_are_frozen(self):
        self.assertEqual(LEVELS,(1,2,4,8,16))
        self.assertEqual([table_name(x) for x in LEVELS],
                         ['r30_curve_1','r30_curve_2','r30_curve_4','r30_curve_8','r30_curve_16'])
        with self.assertRaisesRegex(ValueError,"Unsupported"):
            table_name(5)

    def test_uuid_target_path_is_explicit(self):
        with patch("bench_any_duplication_curve.request",
                   return_value='{"uuid":"ab12cd34-ef56-7890-abcd-112233445566"}\n'):
            self.assertEqual(target('r30_curve_4'),
                             'default.`' + '.inner_id.timeranges.ab12cd34-ef56-7890-abcd-112233445566' + '`')

    def test_each_new_inner_table_is_frozen_before_insert(self):
        path='default.`.inner_id.timeranges.test-uuid`'
        with patch("bench_any_duplication_curve.target",return_value=path), \
             patch("bench_any_duplication_curve.request") as post:
            got=freeze_time_ranges_before_insert('r30_curve_8')
            self.assertEqual(got,path)
            post.assert_called_once_with("SYSTEM STOP MERGES "+path)

    def test_census_exact_expected_multiplier(self):
        with patch("bench_any_duplication_curve.target",
                   return_value='default.`.inner_id.timeranges.abc`'), \
             patch("bench_any_duplication_curve.request",side_effect=[
                '{"total":8192,"ids":2048}\n','{"n":4,"rows":8192}\n']):
            result=census('r30_curve_4',2048,4)
            self.assertEqual(result['multiplicity'],4)

    def test_census_rejects_background_merge_before_our_measurement(self):
        with patch("bench_any_duplication_curve.target",
                   return_value='default.`.inner_id.timeranges.abc`'), \
             patch("bench_any_duplication_curve.request",
                   return_value='{"total":2048,"ids":2048}\n'):
            with self.assertRaisesRegex(ValueError,"Unexpected TimeRanges"):
                census('r30_curve_4',2048,4)


if __name__=="__main__":unittest.main()
