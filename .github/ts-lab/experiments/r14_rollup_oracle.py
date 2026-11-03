#!/usr/bin/env python3
"""R14 finite-float PromQL AVG-by-stage step-grid rollup oracle.

Research surrogate only. No native ClickHouse, timestamps are integer seconds.
Supports exact literal job/branch filters and predeclared evaluation times.
Does not claim full PromQL syntax, native histograms, IEEE bitwise equivalence,
regex, offset, joins, subqueries, dynamic lookback, or correction handling.
"""
from dataclasses import dataclass
from math import fsum, isfinite


@dataclass(frozen=True)
class Sample:
    ts: int
    value: float | None  # None means stale marker (not a numeric NaN).


@dataclass(frozen=True)
class Series:
    id: str
    stage: str
    job: str
    branch: str
    build: str
    samples: tuple[Sample, ...]


class Unsupported(Exception):
    pass


def series_value(series, when, *, mode, horizon):
    """Evaluate the permitted subset of instant or avg_over_time.

    Instant: choose last sample <=when if it is strictly newer than lookback.
    Range: average samples in (when-window, when] after removing stale markers.
    """
    if mode not in ("instant", "avg_over_time") or horizon <= 0:
        raise Unsupported("unsupported mode/horizon")
    matched = [s for s in series.samples if when - horizon < s.ts <= when]
    if not matched:
        return None
    if mode == "instant":
        last = max(matched, key=lambda s: s.ts)
        return last.value
    numeric = [s.value for s in matched if s.value is not None]
    return fsum(numeric) / len(numeric) if numeric else None


def supported_data(series):
    """Explicit contract: unique IDs, unique timestamps and finite floats."""
    return len({s.id for s in series}) == len(series) and all(
        len({sample.ts for sample in s.samples}) == len(s.samples)
        and all(sample.value is None or isfinite(sample.value) for sample in s.samples)
        for s in series
    )


def base_query(series, when, mode, horizon, *, job=None, branch=None, build=None):
    """Independent reference: filter labels BEFORE evaluating each ID."""
    if not supported_data(series):
        raise Unsupported("nonfinite or duplicate timestamps/IDs")
    values = {}
    for s in series:
        if job is not None and s.job != job:
            continue
        if branch is not None and s.branch != branch:
            continue
        if build is not None and s.build != build:
            continue
        v = series_value(s, when, mode=mode, horizon=horizon)
        if v is not None:
            values.setdefault(s.stage, []).append(v)
    return {stage: fsum(vs) / len(vs) for stage, vs in values.items()}


def snapshot(series, times, *, mode, horizon, source_revision):
    """Snapshot sufficient statistics per (time, stage, job, branch).

    A source_revision is an external immutable content version/watermark. The
    caller MUST reject stale snapshots after any relevant correction/insert.
    """
    if not supported_data(series):
        raise Unsupported("nonfinite or duplicate timestamps/IDs")
    if len(set(times)) != len(times):
        raise Unsupported("duplicate times")
    rows = {}
    for when in times:
        for s in series:
            v = series_value(s, when, mode=mode, horizon=horizon)
            if v is not None:
                rows.setdefault((when, s.stage, s.job, s.branch), []).append(v)
    states = {key: (fsum(vs), len(vs)) for key, vs in rows.items()}
    return {"states": states, "times": frozenset(times), "mode": mode,
            "horizon": horizon, "source_revision": source_revision}


def run_snapshot(snap, when, *, mode, horizon, source_revision,
                 job=None, branch=None, build=None):
    """Strict gate; either safe subset or FALLBACK to original execution."""
    if (snap["source_revision"] != source_revision or
            when not in snap["times"] or mode != snap["mode"] or
            horizon != snap["horizon"] or build is not None):
        raise Unsupported("must execute original query")
    groups = {}
    for (t, stage, j, b), (value_sum, count) in snap["states"].items():
        if t != when or (job is not None and j != job) or (branch is not None and b != branch):
            continue
        groups.setdefault(stage, []).append((value_sum, count))
    return {stage: fsum(part_sum for part_sum, _ in parts) / sum(n for _, n in parts)
            for stage, parts in groups.items()}


def candidate_counts(total_series_per_day=95000, stages=38, jobs=10, days=14):
    """Conditional cardinality opportunity, not measured runtime/fixture NDV."""
    if min(total_series_per_day, stages, jobs, days) <= 0:
        raise ValueError("positive inputs required")
    full = total_series_per_day * days
    upper_for_all_branch = stages * jobs * days
    return {"input_series": full, "max_all_branch_states": upper_for_all_branch,
            "max_logical_shrink_if_full_groups": full / upper_for_all_branch,
            "semantics": "per-evaluation group state, no native latency claim"}
