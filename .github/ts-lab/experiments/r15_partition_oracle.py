#!/usr/bin/env python3
"""R15 one-sample-per-series monthly vs daily mark occupancy surrogate.

No ClickHouse execution. Uses random independent 64-bit IDs, fixed day labels,
UTC-aligned buckets. A mark is counted positive if it CONTAINS a queried day;
ClickHouse KeyCondition may select more marks. Monthly model is one merged part.
Daily model is one merged part per day. No write or merge cost is included.
"""
import argparse
import json
import math
import random


def occupancy(days, granularity, window_days):
    if not (days >= 1 and granularity >= 1 and 1 <= window_days <= days):
        raise ValueError("bad input")
    return -math.expm1(granularity * math.log1p(-window_days / days)) if window_days < days else 1.0


def simulate(days=14, series_per_day=95000, granularity=256, seed=20261103, windows=(1, 3, 7, 14)):
    if min(days, series_per_day, granularity) < 1 or any(w < 1 or w > days for w in windows):
        raise ValueError("bad parameters")
    rng = random.Random(seed)
    entries = []
    ids = set()
    for day in range(days):
        for _ in range(series_per_day):
            key = rng.getrandbits(64)
            while key in ids:
                key = rng.getrandbits(64)
            ids.add(key)
            entries.append((key, day))
    entries.sort()
    monthly_marks = [entries[i:i+granularity] for i in range(0, len(entries), granularity)]
    daily_marks_per_day = math.ceil(series_per_day / granularity)
    results = []
    for width in windows:
        positive = sum(any(day < width for _, day in mark) for mark in monthly_marks)
        daily = width * daily_marks_per_day
        results.append({"window_days": width, "monthly_positive_marks": positive,
                        "daily_partition_marks": daily,
                        "monthly_over_daily_marks_ratio": positive/daily,
                        "occupancy_uniform_approx": occupancy(days, granularity, width)})
    return {"synthetic_only": True, "no_native_clickhouse_claim": True,
            "one_merged_monthly_part": True, "one_part_per_day_in_daily_layout": True,
            "days": days, "series_per_day": series_per_day, "rows_per_mark": granularity,
            "random_seed": seed, "total_series": len(entries),
            "monthly_total_marks": len(monthly_marks),
            "daily_total_marks": daily_marks_per_day * days,
            "results": results}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--series-per-day", type=int, default=95000)
    p.add_argument("--granularity", type=int, default=256)
    p.add_argument("--seed", type=int, default=20261103)
    a = p.parse_args()
    print(json.dumps(simulate(**vars(a)), indent=2, sort_keys=True))
