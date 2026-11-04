#!/usr/bin/env python3
"""R40: apply the validated R38 without-parent eligibility to exact R37 compiler source.

The R35 and R37 source-pinned helpers must run first. This last change
makes R35/R37/R38 coexist; R36/R39 collector modifications are not included.
"""
import argparse, difflib, hashlib
from pathlib import Path
PATH = "src/Storages/TimeSeries/PrometheusQueryToSQL/applyFunctionOverRange.cpp"
R37_SOURCE_BLOB = "4bee951641b963acdcf85f81abf729f4e9f78f38"
OLD = """        if (!isOneArgumentAggregationOperator(parent.operator_name) || parent.without
            || parent.children.size() != 1 || parent.children.front() != node)"""
NEW = """        if (!isOneArgumentAggregationOperator(parent.operator_name)
            || parent.children.size() != 1 || parent.children.front() != node)"""

def patch(raw):
    got = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if got != R37_SOURCE_BLOB:
        raise ValueError(f"Unexpected R37 compiler blob {got}; expected {R37_SOURCE_BLOB}")
    src = raw.decode()
    if src.count(OLD) != 1:
        raise ValueError("Expected exactly one without-parent exclusion")
    if src.count("canProjectMetricNameInjectively(") != 2:
        raise ValueError("Missing R37 projection fallback")
    return src.replace(OLD, NEW, 1)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source", default=PATH)
    p.add_argument("--out", required=True)
    p.add_argument("--apply", action="store_true")
    args=p.parse_args()
    file=Path(args.source)
    raw=file.read_bytes()
    candidate=patch(raw)
    diff="".join(difflib.unified_diff(raw.decode().splitlines(True),candidate.splitlines(True),
                                    fromfile="a/"+PATH,tofile="b/"+PATH))
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(diff)
    if args.apply:
        file.write_text(candidate)
    print("R40_PATCH_SHA256",hashlib.sha256(diff.encode()).hexdigest())
    print("R40_SOURCE_BLOB",hashlib.sha1(b"blob " + str(len(candidate.encode())).encode() + b"\0" + candidate.encode()).hexdigest())
if __name__=="__main__":
    main()
