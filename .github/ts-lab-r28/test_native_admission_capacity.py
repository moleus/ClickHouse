#!/usr/bin/env python3
"""Offline protocol checks for R28 native admission result classification."""
import copy
import sys
import unittest

sys.path[:0] = [
    ".github/ts-lab-r28", ".github/ts-lab-bench",
    ".github/ts-lab-perf", ".github/ts-lab-cpu",
]
from bench_native_admission_capacity import (
    ORDER, classify_error, classify_result, native_query,
)


def record(cap, outcome="COMPLETE", p50=1000.0, qps=2.0):
    valid = outcome == "COMPLETE"
    return {
        "slots": cap,
        "outcome": outcome,
        "metrics": {
            "full_valid_window": valid,
            "p50_ms": p50,
            "p95_ms": p50*1.2,
            "completed_qps": qps,
            "process_cpu_ms_per_request": 1500.0,
        },
    }


def example(baseline="OOM"):
    phases = []
    for cap in ORDER:
        p = record(cap)
        if cap == 8:
            p = record(cap, baseline)
        phases.append(p)
    return {"phases": phases, "protocol_errors": []}


class TestAdmissionClassification(unittest.TestCase):
    def test_success_when_both_capped_controls_survive(self):
        decision, controls = classify_result(example())
        self.assertEqual(decision, "ACCEPT_FOR_NEXT_GATE")
        self.assertEqual(controls["2"]["complete_phases"], 2)
        self.assertEqual(controls["4"]["complete_phases"], 2)
        self.assertEqual(controls["8"]["complete_phases"], 0)

    def test_capped_oom_is_not_reported_as_a_gain(self):
        data = example()
        data["phases"][1] = record(2, "OOM")
        decision, _ = classify_result(data)
        self.assertEqual(decision, "REJECT")

    def test_baseline_without_oom_is_inconclusive(self):
        decision, _ = classify_result(example(baseline="COMPLETE"))
        self.assertEqual(decision, "INCONCLUSIVE")

    def test_large_control_drift_rejects_effect_claim(self):
        data = example()
        data["phases"][1]["metrics"]["p50_ms"] = 2000.0
        decision, _ = classify_result(data)
        self.assertEqual(decision, "INCONCLUSIVE")

    def test_protocol_error_not_mistaken_for_oom(self):
        data = example()
        data["protocol_errors"].append({"fatal": "bad data"})
        decision, _ = classify_result(data)
        self.assertEqual(decision, "INVALID_METHOD")

    def test_full_native_query_and_single_intervention(self):
        query = native_query(1800000000)
        self.assertIn("prometheusQueryRange", query)
        self.assertIn("max_threads=2", query)
        self.assertIn("max_memory_usage=1800000000", query)
        self.assertIn("workload='all'", query)
        self.assertIn("workload_admission_timeout_ms=90000", query)
        self.assertIn("project=", query)

    def test_error_mapping(self):
        self.assertEqual(classify_error("Code: 241. MEMORY_LIMIT_EXCEEDED"), "OOM")
        self.assertEqual(classify_error("QUERY_SLOT_ACQUISITION_TIMEOUT"), "ADMISSION_TIMEOUT")
        self.assertEqual(classify_error("TOO_MANY_SIMULTANEOUS_QUERIES"), "ADMISSION_REJECT")


if __name__ == "__main__":
    unittest.main()
