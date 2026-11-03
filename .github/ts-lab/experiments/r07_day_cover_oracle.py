#!/usr/bin/env python3
"""R07: pure-Python exact-ID day-covering metadata sidecar oracle.

NOT a native ClickHouse query, index or performance result. All data synthetic.
Finite float gauge avg_over_time subset, literal labels, per-series evaluation.
Full IDs stay unchanged; duplicate day entries collapse before aggregation.
"""
from dataclasses import dataclass
from collections import defaultdict
import hashlib
import json
import math

DAY=86400
ALLOWED=frozenset(("job","branch","stage","project"))

class Unsupported(Exception):
    pass

@dataclass(frozen=True)
class Series:
    series_id: str
    metric: str
    labels: tuple
    samples: tuple  # ordered/unordered (epoch seconds, finite float or None stale)

def validate(series):
    ids=set()
    for s in series:
        if s.series_id in ids or not s.metric or not s.series_id:
            raise Unsupported("duplicate or empty series identity")
        ids.add(s.series_id)
        if len(dict(s.labels))!=len(s.labels) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in s.labels):
            raise Unsupported("invalid label pairs")
        times=[t for t,_ in s.samples]
        if len(set(times))!=len(times) or not times:
            raise Unsupported("empty or repeated sample timestamps")
        if any(not isinstance(t,int) or (v is not None and (not isinstance(v,(int,float)) or not math.isfinite(v))) for t,v in s.samples):
            raise Unsupported("unsupported timestamp/nonfinite gauge")
    return True

def fingerprint(series):
    """Toy trusted-revision surrogate: O(input) and NOT a fast-path technique."""
    h=hashlib.sha256()
    for s in sorted(series,key=lambda x:x.series_id):
        data=json.dumps([s.series_id,s.metric,sorted(s.labels),sorted(s.samples)],
                        separators=(",",":"),allow_nan=False).encode()
        h.update(len(data).to_bytes(8,"big"))
        h.update(data)
    return h.hexdigest()

def sampled_days(start,duration,cadence):
    if duration<=0 or cadence<=0 or cadence>duration:
        raise ValueError("positive duration and cadence required")
    return len({t//DAY for t in range(start+cadence,start+duration+1,cadence)})

def build_index(series, max_copies_per_series=2.0):
    validate(series)
    per_day=defaultdict(dict)
    for s in series:
        for t,_ in s.samples:
            per_day[t//DAY][s.series_id]=(s.metric,s.labels)
    copies=sum(map(len,per_day.values()))
    amplification=copies/len(series) if series else 0
    if amplification>max_copies_per_series:
        raise Unsupported(f"covering index copies/series={amplification:.5f} exceeds budget")
    return {"days":dict(per_day),"revision":fingerprint(series),
            "series_count":len(series),"copies":copies,
            "amplification":amplification}

def value_over_time(series,end,window):
    # PromQL's left-open range, with stale samples not included.
    values=[v for t,v in series.samples if end-window<t<=end and v is not None]
    return (math.fsum(values)/len(values)) if values else None

def base_query(series,end,window,metric,filters=None):
    validate(series)
    if window<=0:raise Unsupported("bad time range")
    filters=filters or {}
    if not set(filters).issubset(ALLOWED):
        raise Unsupported("unsupported literal filter")
    groups=defaultdict(list)
    for s in series:
        if s.metric!=metric or any(dict(s.labels).get(k)!=v for k,v in filters.items()):
            continue
        value=value_over_time(s,end,window)
        if value is not None:
            groups[dict(s.labels).get("stage","")].append(value)
    return {k:math.fsum(v)/len(v) for k,v in groups.items()}

def query_cover(index,series,end,window,metric,filters=None,verify_revision=True):
    """Overselect whole day partitions, then dedup IDs before scalar evaluation.

    Revision check deliberately hashes ALL input data, so this function is an
    equivalence oracle rather than a practical fast path. Native engine must
    supply a trusted atomic revision/sealed-part source before any speed claim.
    """
    if window<=0:raise Unsupported("bad time range")
    filters=filters or {}
    if not set(filters).issubset(ALLOWED):
        raise Unsupported("unsupported literal filter")
    if verify_revision and fingerprint(series)!=index["revision"]:
        raise Unsupported("stale sidecar after source update")
    visited=set()
    scans=0
    duplicate_day_entries=0
    for day in range((end-window)//DAY,end//DAY+1):
        content=index["days"].get(day,{})
        scans+=len(content)
        for series_id,(m,labels) in content.items():
            if m!=metric or any(dict(labels).get(k)!=v for k,v in filters.items()):
                continue
            if series_id in visited:
                duplicate_day_entries+=1
            visited.add(series_id)
    original={s.series_id:s for s in series}
    groups=defaultdict(list)
    for series_id in visited:
        s=original[series_id]
        scalar=value_over_time(s,end,window)
        if scalar is not None:
            groups[dict(s.labels).get("stage","")].append(scalar)
    return {"output":{k:math.fsum(v)/len(v) for k,v in groups.items()},
            "scanned_cover_rows":scans,"unique_candidate_ids":len(visited),
            "duplicate_day_entries_removed":duplicate_day_entries,
            "day_partitions_considered":(end//DAY-(end-window)//DAY+1)}

def approximate_row_ceiling(distinct_series,retention_days,life_hours,query_days=2):
    """Uniform starts: conditional expected metadata row counts only."""
    if distinct_series<=0 or retention_days<=0 or life_hours<0 or not 1<=query_days<=retention_days:
        raise ValueError("bad parameters")
    copies=1+life_hours/24
    baseline_meta=2*distinct_series  # TAGS and TimeRanges each scanned once
    selected_cover=distinct_series*copies*query_days/retention_days
    return {"metadata_copies_per_id":copies,
            "baseline_metadata_rows":baseline_meta,
            "expected_cover_scan_rows":selected_cover,
            "metadata_rows_ratio":baseline_meta/selected_cover,
            "does_not_include_samples_or_write_cpu":True}
