#!/usr/bin/env python3
"""R05 surrogate: ID-set overlap with primary-key mark ranges, not ClickHouse execution.

Synthetic daily parts contain independent uniformly random 64-bit keys.
Only the last part owns the candidate IDs. All parts have sorted (id,bucket)
keys with bucket fixed; each mark spans the interval between its first key
and the next mark's first key. This intentionally tests FALSE-positive mark
selection when no exact ID is present. No private telemetry or GitHub token.
"""
import argparse
import bisect
import json
import math
import random

SPACE = 1 << 64


def fraction_with_at_least_one_match(g, selectivity):
    """Independent per-ID Bernoulli selection (not primary-key range overlap)."""
    if g <= 0 or not (0 <= selectivity <= 1):
        raise ValueError("g must be positive and selectivity must be in [0,1]")
    return -math.expm1(g * math.log1p(-selectivity)) if selectivity < 1 else 1.0


def touched_marks(part, candidates, granularity):
    """Count marks whose first-key-to-next-first-key envelope meets the set.

    The first/last mark use full-key-space endcaps conservatively. This is an
    interval model, NOT an implementation of ClickHouse KeyCondition.
    """
    candidates = sorted(candidates)
    touched = 0
    for start in range(0, len(part), granularity):
        lo = 0 if start == 0 else part[start]
        hi = part[start + granularity] if start + granularity < len(part) else SPACE
        pos = bisect.bisect_left(candidates, lo)
        touched += int(pos < len(candidates) and candidates[pos] < hi)
    return touched


def simulate(days=14, ids_per_day=95000, granularity=256, candidates=2500, seed=20261103):
    if days < 2 or ids_per_day < 1 or granularity < 1 or not 1 <= candidates <= ids_per_day:
        raise ValueError("bad parameters")
    rng = random.Random(seed)
    parts = []
    for _ in range(days):
        unique_ids = set()
        while len(unique_ids) < ids_per_day:
            unique_ids.add(rng.getrandbits(64))
        parts.append(sorted(unique_ids))
    selected = sorted(rng.sample(parts[-1], candidates))
    results = []
    positives_total = 0
    touched_total = 0
    for i, part in enumerate(parts):
        intersect = set(part).intersection(selected)
        exact_mark_count = len({bisect.bisect_left(part, key) // granularity for key in intersect})
        envelope_count = touched_marks(part, selected, granularity)
        mark_count = (len(part) + granularity - 1) // granularity
        if envelope_count < exact_mark_count:
            raise AssertionError("range coverage must include exact matches")
        positives_total += exact_mark_count
        touched_total += envelope_count
        results.append({"part_day": i+1, "exact_ids": len(intersect), "exact_positive_marks": exact_mark_count,
                        "envelope_candidate_marks": envelope_count, "marks": mark_count})
    return {"model": "uniform-64bit-independent-daily-parts-id-first-mark-envelope",
            "not_clickhouse_measurement": True,
            "days": days, "ids_per_day": ids_per_day, "granularity_rows": granularity,
            "selected_ids_from_last_day": candidates, "seed": seed,
            "total_exact_positive_marks": positives_total,
            "total_candidate_envelope_marks": touched_total,
            "total_marks": sum(part["marks"] for part in results),
            "envelope_mark_fraction": touched_total / sum(part["marks"] for part in results),
            "per_day": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--ids-per-day", type=int, default=95000)
    parser.add_argument("--granularity", type=int, default=256)
    parser.add_argument("--candidates", type=int, default=2500)
    parser.add_argument("--seed", type=int, default=20261103)
    args = parser.parse_args()
    print(json.dumps(simulate(**vars(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
