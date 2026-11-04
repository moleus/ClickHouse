#!/usr/bin/env python3
"""Reduce R35/R36/R36/R35 native runs. Reject drift and incomplete or unequal data."""
import argparse
import hashlib
import json
import math
import pathlib
import statistics

def load(path):
    x = json.loads(pathlib.Path(path).read_text())
    assert x["status"] == "PASS", (path, x.get("errors"))
    return x

def check_output(phase, case):
    items = phase["queries"][case]["output"]
    seen = {}
    for item in items:
        tags = tuple(sorted(tuple(t) for t in item["tags"]))
        for timestamp, value in item["samples"]:
            k = (tags, timestamp)
            assert k not in seen, (phase["variant"], case, "duplicate", k)
            seen[k] = value
    assert len(seen) == phase["queries"][case]["expected_points"]
    return seen

def same_values(a, b):
    if a.keys() != b.keys():
        return False
    for key, value in a.items():
        other = b[key]
        if isinstance(value, float) and isinstance(other, float):
            if math.isnan(value) and math.isnan(other):
                continue
            if math.isclose(value, other, rel_tol=1e-10, abs_tol=1e-10):
                continue
        if value != other:
            return False
    return True

def main():
    p = argparse.ArgumentParser()
    p.add_argument("reports", nargs=4)
    p.add_argument("--output", required=True)
    p.add_argument("--max-drift", type=float, default=0.15)
    args = p.parse_args()
    phases = [load(x) for x in args.reports]
    assert [x["variant"] for x in phases] == ["r35", "r36", "r36", "r35"]
    assert len({x["binary_sha256"] for x in phases if x["variant"] == "r35"}) == 1
    assert len({x["binary_sha256"] for x in phases if x["variant"] == "r36"}) == 1
    assert len({x["builds_per_day"] for x in phases}) == 1
    assert all(x["parts"] == phases[0]["parts"] for x in phases), "physical parts changed"
    assert all(x["query_threads"] == phases[0]["query_threads"] for x in phases)
    cases = set(phases[0]["queries"])
    assert cases
    assert all(set(x["queries"]) == cases for x in phases)
    result = {
        "status": "ACCEPT_FOR_NEXT_GATE",
        "phase_order": ["r35","r36","r36","r35"],
        "reports": args.reports,
        "binaries": {x["variant"]: x["binary_sha256"] for x in phases},
        "builds_per_day": phases[0]["builds_per_day"],
        "parts_sha256": hashlib.sha256(json.dumps(phases[0]["parts"], sort_keys=True).encode()).hexdigest(),
        "resource_envelope": {
            "cpu_max": [x["cpu_max"] for x in phases],
            "memory_max": [x["memory_max"] for x in phases],
            "query_threads": phases[0]["query_threads"]},
        "cases": {},
    }
    for case in sorted(cases):
        baseline_output = check_output(phases[0], case)
        for phase in phases[1:]:
            assert same_values(baseline_output, check_output(phase, case)), ("output mismatch", case, phase["variant"])
        all_trials = [phase["queries"][case]["trials"] for phase in phases]
        assert all(len(trials) >= 6 for trials in all_trials)
        for trials in all_trials:
            assert len([x for x in trials if x["warmup"]]) >= 2
            assert len([x for x in trials if not x["warmup"]]) >= 4
        read_sets = {((trial["read_rows"],trial["read_bytes"])) for trials in all_trials for trial in trials}
        assert len(read_sets) == 1, ("physical read mismatch", case, sorted(read_sets))
        metrics = ("wall_ms","cpu_ms","memory_bytes","peak_observed_rss","process_cpu_ms")
        med = {metric: [statistics.median(t[metric] for t in trials if not t["warmup"]) for trials in all_trials]
               for metric in metrics}
        record = {"output_points":len(baseline_output), "read_rows":next(iter(read_sets))[0],
                  "read_bytes":next(iter(read_sets))[1], "measurements":{}}
        for name, vals in med.items():
            stock = (vals[0]+vals[3])/2
            candidate = (vals[1]+vals[2])/2
            drift = abs(vals[3]-vals[0])/stock if stock else 0.0
            record["measurements"][name] = {
                "phase_medians": vals, "r35_mean": stock, "r36_mean": candidate,
                "r36_vs_r35_ratio": candidate/stock if stock else None,
                "reduction_pct": (1-candidate/stock)*100 if stock else None,
                "r35_control_drift_pct": drift*100}
            if name in ("wall_ms","cpu_ms") and drift > args.max_drift:
                result["status"] = "INCONCLUSIVE_DRIFT"
        result["cases"][case] = record
    pathlib.Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False))
    print(json.dumps({"status":result["status"],
                      "cases":{case:{k:round(v["reduction_pct"],3)
                                     for k,v in x["measurements"].items() if k in ("wall_ms","cpu_ms","memory_bytes")}
                               for case,x in result["cases"].items()}},indent=2))
    if result["status"] != "ACCEPT_FOR_NEXT_GATE":
        raise SystemExit(2)

if __name__ == "__main__":
    main()
