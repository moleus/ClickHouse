#!/usr/bin/env python3
"""R31 candidate-restricted TimeRanges IN-set: source-bound SQL rewrite only.

This generates an additional TAGS id membership filter INSIDE the TimeRanges
IN subquery. The outer TAGS filter and the full native time-range predicate
are kept byte-for-byte. No ClickHouse production source is modified.
"""
import sys
sys.path.insert(0, ".github/ts-lab-r31")
from bench_membership_in import quoted_parenthesized


def top_level_comma_parts(s):
    """Split one AST function argument list, not nested function commas."""
    parts=[]
    begin=0
    depth=0
    quote=None
    escape=False
    index=0
    while index<len(s):
        ch=s[index]
        if quote is not None:
            if escape:escape=False
            elif ch==chr(92):escape=True
            elif ch==quote:
                if index+1<len(s) and s[index+1]==quote:index+=1
                else:quote=None
        elif ch in (chr(39),chr(34),chr(96)):quote=ch
        elif ch=="(":depth+=1
        elif ch==")":
            depth-=1
            if depth<0:raise ValueError("Invalid nested parentheses")
        elif ch=="," and depth==0:
            parts.append(s[begin:index].strip())
            begin=index+1
        index+=1
    if quote or depth:
        raise ValueError("Unbalanced SQL arguments")
    parts.append(s[begin:].strip())
    return parts


def restrict_time_ranges_in_to_candidates(full_sql):
    """Rewrite source-generated R31 IN SQL with exact same candidate TAGS predicate."""
    if not full_sql.startswith("SELECT timeSeriesStoreTags("):
        raise ValueError("Unexpected stateful TimeSeries ID SELECT")
    separator=" FROM default.`.inner_id.tags."
    if full_sql.count(separator)!=1:
        raise ValueError("Missing single native TAGS table")
    frm=full_sql.index(separator)
    wh=full_sql.find(" WHERE and(",frm)
    if wh<0:raise ValueError("Missing native outer AND predicate")
    target=full_sql[frm+len(" FROM "):wh]
    if not target.startswith("default.`.inner_id.tags.") or not target.endswith("`"):
        raise ValueError("Unexpected TAGS table path")
    where_open=wh+len(" WHERE and")
    original,where_close=quoted_parenthesized(full_sql,where_open)
    if full_sql[where_close+1:].strip():
        raise ValueError("Unexpected suffix on generated ID SELECT")
    args=top_level_comma_parts(original)
    if len(args)!=2 or not args[1].startswith("or(in(id, "):
        raise ValueError("Unknown outer filter/metric-range fallback")
    left_filter=args[0]
    if "equals(metric_name," not in left_filter or "arrayElement(tags," not in left_filter:
        raise ValueError("Probe only supports exact metric name and positive label filter")
    anchor="in(id, (SELECT id FROM default.`.inner_id.timeranges."
    if full_sql.count(anchor)!=1:
        raise ValueError("Expected one TimeRanges ID IN source")
    start=full_sql.index(anchor)+len("in(id, ")
    right,right_close=quoted_parenthesized(full_sql,start)
    if not right.startswith("SELECT id FROM default.`.inner_id.timeranges."):
        raise ValueError("Unexpected TimeRanges table")
    if right.count(" WHERE ")!=1:
        raise ValueError("Right selector is not one SQL WHERE")
    original_right_table,right_pred=right[12:].split(" WHERE ",1)
    if not original_right_table.startswith("default.`.inner_id.timeranges."):
        raise ValueError("Invalid right table")
    if "greaterOrEquals(max_time," not in right_pred or "lessOrEquals(min_time," not in right_pred:
        raise ValueError("Time window not preserved")
    extra=("in(id, (SELECT id FROM "+target+" WHERE "+left_filter+"))")
    new_right="SELECT id FROM "+original_right_table+" WHERE and("+right_pred+", "+extra+")"
    patched=full_sql[:start+1]+new_right+full_sql[right_close:]
    if patched==full_sql or patched.count("timeSeriesStoreTags")!=1:
        raise ValueError("Unexpected patched SQL")
    if patched.count("default.`.inner_id.tags.")!=2:
        raise ValueError("Expected outer TAGS and exactly one candidate subquery")
    return patched


if __name__=="__main__":
    import argparse,pathlib
    p=argparse.ArgumentParser()
    p.add_argument("source")
    p.add_argument("--output",required=True)
    a=p.parse_args()
    pathlib.Path(a.output).write_text(
        restrict_time_ranges_in_to_candidates(pathlib.Path(a.source).read_text()))
