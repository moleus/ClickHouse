#!/usr/bin/env python3
"""Pure guard for R18 2x2 experiment: no server, download or fixture needed."""
import pathlib
import sys
import unittest

ROOT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import bench_value_codec_2x2 as testee


def example_ddl(codec):
    q=chr(96)
    return (
        f"CREATE TABLE default.{q}.inner_id.samples.dummy-uuid{q}\n"
        "(\n"
        "    id Tuple(UInt64,UUID),\n"
        "    samples SimpleAggregateFunction(timeSeriesGroupArray,"
        "Array(Tuple(timestamp DateTime64(3), value Float64))),\n"
        f"    CODEC {q}samples.timestamp{q} (Delta(8), T64, ZSTD(3)),\n"
        f"    CODEC {q}samples.value{q} ({codec})\n"
        ")\nENGINE = AggregatingMergeTree\n"
        "PARTITION BY toYYYYMM(toDateTime(bucket, 'UTC'))\n"
        "ORDER BY (id,bucket)\n"
    )


class TestFactorial(unittest.TestCase):
    def test_selector_dictionary_not_shadowed(self):
        self.assertIsInstance(testee.CASES,dict)
        self.assertEqual(len(testee.CASES_DEFAULT),3)
        for name in testee.CASES_DEFAULT:
            self.assertIn(name,testee.CASES)
            self.assertEqual(len(testee.CASES[name]),4)
            self.assertGreater(testee.CASES[name][3],0)

    def test_factor_pairs_complete(self):
        self.assertEqual(set(testee.CODECS),
                         {"alp_z3","alp_z1","raw_z3","raw_z1"})
        self.assertEqual(len(testee.FACTORS),4)
        self.assertEqual(
            set(v for pair in testee.FACTORS.values() for v in pair),
            set(testee.CODECS)
        )

    def test_effective_codec_parser_handles_backticks_and_delta8(self):
        normalized=[]
        for kind,codec in testee.CODECS.items():
            result=testee.verify_effective_codec_and_canonical_ddl(
                kind,example_ddl(codec)
            )
            normalized.append(result)
            self.assertIn("CODEC samples.value (FACTOR)",result)
        self.assertEqual(len(set(normalized)),1,
                         "Different columns or settings beyond codec")

    def test_wrong_effective_codec_is_fatal(self):
        with self.assertRaises(AssertionError):
            testee.verify_effective_codec_and_canonical_ddl(
                "alp_z3",example_ddl("ZSTD(3)")
            )
        with self.assertRaises(AssertionError):
            testee.verify_effective_codec_and_canonical_ddl(
                "raw_z1",example_ddl("ALP, ZSTD(1)")
            )

    def test_wrong_timestamp_codec_is_fatal(self):
        other=example_ddl("ZSTD(1)").replace(
            "Delta(8), T64, ZSTD(3)","Delta(8), T64, LZ4"
        )
        with self.assertRaises(AssertionError):
            testee.verify_effective_codec_and_canonical_ddl(
                "raw_z1",other
            )


if __name__=="__main__":
    unittest.main()
