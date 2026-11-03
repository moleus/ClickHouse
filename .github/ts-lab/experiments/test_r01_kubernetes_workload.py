#!/usr/bin/env python3
"""Deterministic R01 fixture and series-lifecycle contract tests."""
import argparse
import collections
import hashlib
import io
import json
import unittest

from r01_kubernetes_workload import (
    generate, node_state, sample_events, validate_config
)


def config(**overrides):
    opts = {
        "nodes": 4, "duration_seconds": 4800, "scrape_seconds": 600,
        "node_lifetime_seconds": 1200, "hpa_period_seconds": 1200,
        "base_pods": 2, "stable_services": 2,
        "seed": 17, "start": "2026-08-01T00:00:00+00:00",
        "allow_large": False
    }
    opts.update(overrides)
    return argparse.Namespace(**opts)


def render(args):
    stream = io.BytesIO()
    report = generate(args, stream)
    rows = [json.loads(line) for line in stream.getvalue().splitlines()]
    return report, rows, stream.getvalue()


class TestR01(unittest.TestCase):
    def test_byte_reproducibility_and_manifest(self):
        args = config()
        first, rows, data = render(args)
        second, rows2, data2 = render(args)
        self.assertEqual(data, data2)
        self.assertEqual(rows, rows2)
        self.assertEqual(first, second)
        self.assertEqual(first["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(first["jsonl_bytes"], len(data))
        self.assertEqual(first["sample_rows"], len(rows))

    def test_unique_series_and_sorted_time(self):
        report, rows, _ = render(config())
        keys = set()
        observed = collections.defaultdict(set)
        ts = []
        for row in rows:
            self.assertEqual(set(row), {"metric_name", "tags", "samples"})
            self.assertEqual(len(row["samples"]), 1)
            self.assertEqual(row["tags"]["cluster"], "synthetic-cluster")
            sample_time, sample_value = row["samples"][0]
            ts.append(sample_time)
            self.assertGreaterEqual(sample_value, 0)
            ident = (row["metric_name"], tuple(sorted(row["tags"].items())))
            unique_sample = (ident, sample_time)
            self.assertNotIn(unique_sample, keys)
            keys.add(unique_sample)
            observed[row["metric_name"]].add(ident)
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(report["distinct_series_by_metric"],
                         {name: len(ids) for name, ids in observed.items()})
        self.assertEqual(report["distinct_series_total"],
                         sum(map(len, observed.values())))

    def test_counters_reset_on_incarnation_only(self):
        _, rows, _ = render(config())
        prev = {}
        for row in rows:
            if row["metric_name"] == "kube_pod_info":
                self.assertEqual(row["samples"][0][1], 1.0)
                continue
            identity = (row["metric_name"], tuple(sorted(row["tags"].items())))
            current = row["samples"][0][1]
            if identity in prev:
                self.assertGreaterEqual(current, prev[identity])
            prev[identity] = current
        self.assertGreater(len(prev), 15)

    def test_staggered_node_rotation_and_hpa(self):
        args = config()
        self.assertNotEqual(node_state(0, 0, args)[1], node_state(3, 0, args)[1])
        report, rows, _ = render(args)
        self.assertGreater(report["node_turnover_count"], args.nodes)
        self.assertGreater(report["high_hpa_scrapes"], 0)
        extras = {x["tags"]["pod"] for x in rows if x["metric_name"] == "kube_pod_info"
                  and "burst-" in x["tags"]["pod"]}
        self.assertTrue(extras)
        # Unique tags for HPA replicas must not alias the baseline pod IDs.
        baseline = {x["tags"]["pod"] for x in rows if x["metric_name"] == "kube_pod_info"
                    and "burst-" not in x["tags"]["pod"]}
        self.assertFalse(extras & baseline)

    def test_services_stable_across_node_replacements(self):
        _, rows, _ = render(config())
        services = {tuple(sorted(x["tags"].items())) for x in rows
                    if x["metric_name"] == "http_requests_total"}
        self.assertEqual(len(services), 2)

    def test_zero_service_rows(self):
        report, rows, _ = render(config(stable_services=0))
        self.assertNotIn("http_requests_total", {x["metric_name"] for x in rows})
        self.assertEqual(report["distinct_series_by_metric"]["http_requests_total"], 0)

    def test_input_validation(self):
        for opts in (
            {"scrape_seconds": 0},
            {"node_lifetime_seconds": 700},
            {"hpa_period_seconds": -1},
            {"duration_seconds": 601},
            {"seed": -1},
            {"start": "2026-08-01T00:00:00+03:00"}
        ):
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                validate_config(config(**opts))

    def test_large_guard(self):
        with self.assertRaises(ValueError):
            validate_config(config(nodes=400, duration_seconds=86400*14,
                                   scrape_seconds=15, node_lifetime_seconds=10800,
                                   hpa_period_seconds=3600))
        validate_config(config(nodes=400, duration_seconds=86400*14,
                               scrape_seconds=15, node_lifetime_seconds=10800,
                               hpa_period_seconds=3600, allow_large=True))


if __name__ == "__main__":
    unittest.main()
