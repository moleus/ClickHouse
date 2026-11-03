#!/usr/bin/env python3
"""Deterministic Kubernetes-shaped TimeSeries fixture, without production data.

One 60s node gauge + two 300s pod gauges per 3h node life, and an
additional 15s pod gauge for 1% of active node slots. Node labels rotate
every three hours, while slot/project/namespace labels remain stable.
Each JSONEachRow TimeSeries record represents one logical series with
many samples. No random state, live cluster data, or external dependencies.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib

BASE = int(dt.datetime(2026, 8, 4, tzinfo=dt.timezone.utc).timestamp())
DAY = 86400
LIFE = 3 * 3600
PER_DAY = DAY // LIFE
TIERS = {
    "node60": {"metric": "tslab_node_cpu_ratio", "step": 60, "samples": LIFE // 60},
    "pod300": {"metric": "tslab_pod_cpu_ratio", "step": 300, "samples": LIFE // 300},
    "burst15": {"metric": "tslab_pod_cpu_ratio", "step": 15, "samples": LIFE // 15},
}


def encode_time(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S.000"
    )


def make_row(tier, slot, life, substream):
    definition = TIERS[tier]
    node = f"node-{slot:04d}-life-{life:04d}"
    common = {
        "cluster": "synthetic-k8s",
        "region": "eu-north-1",
        "project": f"project-{slot % 8:02d}",
        "node": node,
        "slot": f"slot-{slot:04d}",
        "nodepool": ("general" if slot % 4 < 2 else ("compute" if slot % 4 == 2 else "memory")),
        "scrape": f"{definition['step']}s",
    }
    if tier != "node60":
        common.update({
            "namespace": f"team-{slot % 16:03d}",
            "pod": f"pod-{slot:04d}-{life:04d}-{substream}",
            "container": "service" if tier == "pod300" else "burst",
            "job": "kubelet",
        })
    else:
        common["job"] = "node-exporter"
    epoch = BASE + life * LIFE
    samples = []
    for i in range(definition["samples"]):
        sample_ts = epoch + (i + 1) * definition["step"] - 1
        # Deterministic finite gauge with nonconstant time component.
        value = round(
            0.12 + ((slot * 17 + life * 7 + substream * 11) % 71) * 0.003
            + (i % 23) * 0.00007, 7
        )
        samples.append([encode_time(sample_ts), value])
    return {
        "metric_name": definition["metric"],
        "tags": common,
        "samples": samples,
    }


def expected_counts(days, slots):
    rotations = days * PER_DAY
    bursts = sum(1 for slot in range(slots) if slot % 100 == 0)
    series_by_tier = {
        "node60": rotations * slots,
        "pod300": rotations * slots * 2,
        "burst15": rotations * bursts,
    }
    sample_by_tier = {
        k: n * TIERS[k]["samples"] for k, n in series_by_tier.items()
    }
    return series_by_tier, sample_by_tier


def generate(path, manifest_path, days, slots):
    if not (1 <= days <= 14 and 1 <= slots <= 4000):
        raise ValueError("days must be 1..14, slots must be 1..4000")
    out = pathlib.Path(path)
    manifest_file = pathlib.Path(manifest_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    sums = hashlib.sha256()
    actual_series = collections.Counter()
    actual_samples = collections.Counter()
    first_label = None
    last_label = None
    with out.open("wb") as fd:
        for life in range(days * PER_DAY):
            for slot in range(slots):
                tiers = [("node60", 0), ("pod300", 0), ("pod300", 1)]
                if slot % 100 == 0:
                    tiers.append(("burst15", 2))
                for tier, substream in tiers:
                    row = make_row(tier, slot, life, substream)
                    if not row["samples"]:
                        raise AssertionError("Empty timeseries")
                    label = (row["metric_name"], row["tags"]["node"], row["tags"].get("pod"))
                    first_label = first_label or label
                    last_label = label
                    data = (json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
                    fd.write(data)
                    sums.update(data)
                    actual_series[tier] += 1
                    actual_samples[tier] += len(row["samples"])
    expected_series, expected_samples = expected_counts(days, slots)
    if dict(actual_series) != expected_series or dict(actual_samples) != expected_samples:
        raise AssertionError("Fixture has incorrect tier distributions")
    manifest = {
        "schema_version": 1,
        "description": "Synthetic Kubernetes-shaped finite gauge TimeSeries records; not production samples",
        "source": "R01-k8s-mixed-v1",
        "base": BASE,
        "end": BASE + days * DAY,
        "days": days,
        "node_slots": slots,
        "life_seconds": LIFE,
        "rotations": days * PER_DAY,
        "cadence_seconds": {tier: d["step"] for tier, d in TIERS.items()},
        "rows_by_tier": expected_series,
        "samples_by_tier": expected_samples,
        "series": sum(expected_series.values()),
        "samples": sum(expected_samples.values()),
        "fixture_bytes": out.stat().st_size,
        "sha256": sums.hexdigest(),
        "first_label": list(first_label) if first_label else None,
        "last_label": list(last_label) if last_label else None,
    }
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--node-slots", type=int, default=400)
    args = parser.parse_args()
    result = generate(args.fixture, args.manifest, args.days, args.node_slots)
    print("R01_FIXTURE", json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
