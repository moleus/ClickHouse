#!/usr/bin/env python3
"""R01 public synthetic Kubernetes-like TimeSeries ingest workload.

Emits chronological JSONEachRow with metric_name, tags and samples (one per row).
This is NOT a measured production distribution. No private metrics or names.
Output is deterministic for an identical input configuration, without random state.
Use --allow-large explicitly for workloads above five million sample rows.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import pathlib
import sys

METRICS = ("node_cpu_seconds_total", "container_cpu_usage_seconds_total",
           "kube_pod_info", "http_requests_total")
DEFAULT_START = "2026-08-01T00:00:00+00:00"
MAX_DEFAULT_ROWS = 5_000_000


def validate_config(args):
    checks = {
        "nodes": args.nodes, "duration_seconds": args.duration_seconds,
        "scrape_seconds": args.scrape_seconds,
        "node_lifetime_seconds": args.node_lifetime_seconds,
        "hpa_period_seconds": args.hpa_period_seconds,
        "base_pods": args.base_pods, "stable_services": args.stable_services,
    }
    for key, value in checks.items():
        if not isinstance(value, int) or value < (0 if key == "stable_services" else 1):
            raise ValueError(key + " must be a positive integer, except stable_services >= 0")
    if (args.duration_seconds % args.scrape_seconds or
            args.node_lifetime_seconds % args.scrape_seconds or
            args.hpa_period_seconds % args.scrape_seconds):
        raise ValueError("duration, node lifetime and HPA period must be multiples of scrape seconds")
    if args.seed < 0:
        raise ValueError("seed must be nonnegative")
    begin = dt.datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    if begin.utcoffset() != dt.timedelta(0):
        raise ValueError("start must contain a UTC offset of +00:00 or Z")
    estimate = (args.duration_seconds // args.scrape_seconds) * (
        args.nodes * (4 + 2 * (args.base_pods + 1)) + args.stable_services)
    if estimate > MAX_DEFAULT_ROWS and not args.allow_large:
        raise ValueError(
            f"Potentially {estimate:,} samples, exceeds {MAX_DEFAULT_ROWS:,}. "
            "Use --allow-large and stream output to a file or pipe."
        )
    return begin, estimate


def node_state(slot, seconds, args):
    """Node slots persist; incarnations rotate every L seconds with staggered ages."""
    phase = slot * args.node_lifetime_seconds // args.nodes
    generation, age = divmod(seconds + phase, args.node_lifetime_seconds)
    return generation, age


def burst_state(seconds, args):
    """A high HPA state lasts two periods and is followed by a low state."""
    epoch, phase_age = divmod(seconds, args.hpa_period_seconds)
    segment = epoch % 4
    high = segment in (1, 2)
    burst_index = epoch // 4
    burst_start = (burst_index * 4 + 1) * args.hpa_period_seconds
    return high, burst_index, burst_start


def time_label(base, seconds):
    return (base + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S.000")


def sample_events(seconds, args):
    """Yield (family, logical identity, tags, value), in a fixed order.

    Identity tuples are exact, not hashes. Counters reset only if identity changes.
    The workload has deliberately simplified metadata and deterministic values.
    """
    hpa_high, burst_index, burst_start = burst_state(seconds, args)
    for slot in range(args.nodes):
        generation, age = node_state(slot, seconds, args)
        region = f"region-{(slot + args.seed) % 2}"
        zone = f"zone-{(slot * 7 + args.seed) % 4}"
        node = f"node-{slot:04d}-{generation:05d}"
        node_tags = {"cluster": "synthetic-cluster", "region": region,
                     "zone": zone, "node": node, "instance": node + ":9100"}
        for cpu in range(2):
            for mode in ("idle", "user"):
                tags = {**node_tags, "cpu": str(cpu), "mode": mode}
                factor = 0.25 + 0.15 * cpu + (0.3 if mode == "idle" else 0.0)
                yield (METRICS[0], ("node", slot, generation, cpu, mode),
                       tags, round(age * factor, 6))

        namespace = f"namespace-{(slot + args.seed) % 4}"
        job = f"workload-{(slot // 2 + args.seed) % 6}"
        for pod_slot in range(args.base_pods + (1 if hpa_high else 0)):
            extra = pod_slot >= args.base_pods
            # Burst index only changes after the high/low cycle. Base pods
            # retain identity until node replacement; extra pods do not.
            replica_id = burst_index if extra else -1
            pod = f"pod-{slot:04d}-{generation:05d}-{pod_slot:02d}"
            if extra:
                pod += f"-burst-{burst_index:04d}"
            tags = {"cluster": "synthetic-cluster", "region": region,
                    "zone": zone, "node": node, "namespace": namespace,
                    "job": job, "pod": pod, "container": "app"}
            identity = (slot, generation, pod_slot, replica_id)
            pod_age = min(age, seconds - burst_start) if extra else age
            yield (METRICS[1], ("container",) + identity,
                   tags, round(max(0, pod_age) * (0.08 + 0.01 * pod_slot), 6))
            # Stable gauge within pod life. Counter and gauge share labels but
            # differ in metric name and exact logical identity.
            yield (METRICS[2], ("pod_info",) + identity, tags, 1.0)

    # Long-lived service counters, independent from node and pod churn.
    for service in range(args.stable_services):
        tags = {"cluster": "synthetic-cluster",
                "region": f"region-{service % 2}",
                "namespace": f"namespace-{service % 4}",
                "job": f"frontend-{service:02d}"}
        yield (METRICS[3], ("service", service), tags,
               round(seconds * (1.0 + 0.1 * service), 6))


def generate(args, output):
    base, estimate = validate_config(args)
    digest = hashlib.sha256()
    unique_ids = {metric: set() for metric in METRICS}
    counters = collections.Counter()
    node_generations = collections.defaultdict(set)
    high_ticks = 0
    total_bytes = 0
    for seconds in range(0, args.duration_seconds, args.scrape_seconds):
        high_ticks += int(burst_state(seconds, args)[0])
        stamp = time_label(base, seconds)
        for family, identity, tags, value in sample_events(seconds, args):
            record = {"metric_name": family, "tags": tags,
                      "samples": [[stamp, value]]}
            line = (json.dumps(record, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True) + "\n").encode("utf-8")
            output.write(line)
            digest.update(line)
            total_bytes += len(line)
            counters[family] += 1
            unique_ids[family].add(identity)
            if family == METRICS[0]:
                node_generations[identity[1]].add(identity[2])
    steps = args.duration_seconds // args.scrape_seconds
    return {
        "schema_version": 1, "fixture_kind": "public_synthetic_kubernetes_v1",
        "claim_id": "R01-k8s-churn-fixture-v1",
        "source_distribution_is_measured": False,
        "seed": args.seed, "start": base.isoformat(),
        "nodes": args.nodes, "duration_seconds": args.duration_seconds,
        "scrape_seconds": args.scrape_seconds,
        "node_lifetime_seconds": args.node_lifetime_seconds,
        "base_pods": args.base_pods, "hpa_period_seconds": args.hpa_period_seconds,
        "stable_services": args.stable_services,
        "scrapes": steps, "high_hpa_scrapes": high_ticks,
        "estimated_max_rows": estimate, "samples_by_metric": dict(counters),
        "distinct_series_by_metric": {k: len(v) for k, v in unique_ids.items()},
        "distinct_series_total": sum(len(v) for v in unique_ids.values()),
        "node_generation_count": sum(len(v) for v in node_generations.values()),
        "node_turnover_count": sum(len(v) - 1 for v in node_generations.values()),
        "sample_rows": sum(counters.values()),
        "jsonl_bytes": total_bytes, "sha256": digest.hexdigest(),
        "last_sample_timestamp": time_label(base, args.duration_seconds - args.scrape_seconds),
        "sort_order": "scrape_time_then_node_slot_family_pod_then_services",
        "format": "JSONEachRow(metric_name,tags,samples)", 
        "limitations": [
            "synthetic distribution, not production evidence",
            "one sample per JSONEachRow row, not a remote-write protobuf",
            "two CPU cores per node with synthetic values",
            "fixed namespace/job assignment, simplified HPA and no histogram buckets"
        ]
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nodes", type=int, default=16)
    p.add_argument("--duration-seconds", type=int, default=21600)
    p.add_argument("--scrape-seconds", type=int, default=60)
    p.add_argument("--node-lifetime-seconds", type=int, default=10800)
    p.add_argument("--hpa-period-seconds", type=int, default=3600)
    p.add_argument("--base-pods", type=int, default=2)
    p.add_argument("--stable-services", type=int, default=4)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--output", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--allow-large", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    output = pathlib.Path(args.output)
    manifest = pathlib.Path(args.manifest)
    if output.resolve() == manifest.resolve():
        raise ValueError("output and manifest must be different files")
    validate_config(args)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fp:
        report = generate(args, fp)
    manifest.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print("R01_FIXTURE", json.dumps({
        "sha256": report["sha256"], "rows": report["sample_rows"],
        "distinct_series": report["distinct_series_total"],
        "bytes": report["jsonl_bytes"]}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        print("R01_ERROR " + str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
