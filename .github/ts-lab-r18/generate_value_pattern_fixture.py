#!/usr/bin/env python3
"""Deterministic R18 Float64 value-distribution holdouts.

Transforms only sample VALUE elements of a SHA-verified R01 fixture.
Metric names, tags, sample timestamps, row ordering and sample counts
remain byte-for-byte equal as Python strings. No production data.
Two held-out finite float families:
- smooth: slowly increasing rounded gauges (easy ALP/ZSTD patterns).
- entropy: full 53-bit splitmix64 pseudo-random mantissas in [0,1)
  (high entropy, intentionally hard to compress).
Use SHA-verified manifest and identical native PromQL output between
codec variants, not across value families.
"""
import argparse
import copy
import hashlib
import json
import math
import pathlib

ORIGINAL_SHA="ea9a8d43c9e58ed916f1bf43949556d8ed18169ddd14033c293af4cd54c9881d"
MASK=(1<<64)-1
FL64_INV=1.0/(1<<53)


def mix64(x):
    x=(x+0x9E3779B97F4A7C15)&MASK
    x=((x^(x>>30))*0xBF58476D1CE4E5B9)&MASK
    x=((x^(x>>27))*0x94D049BB133111EB)&MASK
    return (x^(x>>31))&MASK


def make_value(kind,row_id,sample_id):
    if kind=="smooth":
        return round(0.125+(row_id%137)*0.000011+(sample_id+1)*0.000001,7)
    seed=((row_id+1)*0xD6E8FEB86659FD93+(sample_id+1)*0xA0761D6478BD642F)&MASK
    return (mix64(seed)>>11)*FL64_INV


def generate(src,dst,parent_manifest,manifest_path,kind):
    if kind not in ("smooth","entropy"):
        raise ValueError("Unknown held-out Float64 distribution "+str(kind))
    assert pathlib.Path(src).resolve()!=pathlib.Path(dst).resolve(),"Do not overwrite canonical fixture"
    base=json.loads(pathlib.Path(parent_manifest).read_text())
    h=hashlib.sha256()
    bcount=0
    checked_series=0
    sample_count=0
    min_value=float("inf")
    max_value=float("-inf")
    pathlib.Path(dst).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(manifest_path).parent.mkdir(parents=True,exist_ok=True)
    with open(src,"rb") as source,open(dst,"wb") as target:
        for row_id,raw in enumerate(source):
            if not raw.strip():continue
            parsed=json.loads(raw)
            samples=parsed.get("samples")
            if not isinstance(samples,list) or not samples:
                raise AssertionError(("Empty samples",row_id))
            for i,sample in enumerate(samples):
                if not isinstance(sample,list) or len(sample)!=2 or not isinstance(sample[0],str):
                    raise AssertionError(("Bad source sample",row_id,i))
                value=make_value(kind,row_id,i)
                if not math.isfinite(value):raise AssertionError("Nonfinite candidate value")
                sample[1]=value
                min_value=min(min_value,value)
                max_value=max(max_value,value)
                sample_count+=1
            b=(json.dumps(parsed,separators=(",",":"),ensure_ascii=True)+"\n").encode()
            target.write(b)
            h.update(b)
            bcount+=len(b)
            checked_series+=1
    if checked_series!=base["series"] or sample_count!=base["samples"]:
        raise AssertionError(("Series/sample count differs",checked_series,sample_count))
    manifest=copy.deepcopy(base)
    manifest.update({
        "schema_version":1,
        "source":f"R18-value-family-{kind}",
        "parent_fixture_sha256":ORIGINAL_SHA,
        "value_pattern":kind,
        "sha256":h.hexdigest(),
        "fixture_bytes":bcount,
        "value_min":min_value,
        "value_max":max_value,
        "description":"Derived R18 synthetic Float64 values; original metric/tag/timestamp layout unchanged"
    })
    pathlib.Path(manifest_path).write_text(json.dumps(manifest,sort_keys=True,indent=2)+"\n")
    print("R18_VALUE_FIXTURE",json.dumps({
        "pattern":kind,"sha256":h.hexdigest(),
        "parent_sha256":ORIGINAL_SHA,"rows":checked_series,
        "samples":sample_count,"bytes":bcount,
        "min":min_value,"max":max_value
    },separators=(",",":")),flush=True)
    return manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",required=True)
    parser.add_argument("--source-manifest",required=True)
    parser.add_argument("--fixture",required=True)
    parser.add_argument("--manifest",required=True)
    parser.add_argument("--pattern",choices=("smooth","entropy"),required=True)
    args=parser.parse_args()
    source_digest=hashlib.file_digest(open(args.source,"rb"),"sha256").hexdigest()
    parent=json.loads(pathlib.Path(args.source_manifest).read_text())
    if source_digest!=ORIGINAL_SHA or parent["sha256"]!=ORIGINAL_SHA:
        raise AssertionError("Original R01 source fixture SHA mismatch")
    generate(args.source,args.fixture,args.source_manifest,args.manifest,args.pattern)


if __name__=="__main__":
    main()
