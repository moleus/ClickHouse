#!/usr/bin/env python3
"""Fast unit tests of lab pipeline graph summarizer; no ClickHouse required."""
import sys
sys.path[:0] = [".github/ts-lab-cpu"]
from bench_native_pipeline_graph import graph_summary

def node(i, parents, work, input_wait, output_wait, name):
    return {"id":i,"parent_ids":parents,"plan_step_name":"step-"+name,
            "name":name,"elapsed_us":work,
            "input_wait_elapsed_us":input_wait,
            "output_wait_elapsed_us":output_wait,
            "input_rows":12,"output_rows":10}

rows=[node(1,[],100,0,4,"source"),
      node(2,[1],80,3,0,"join"),
      node(3,[1],20,0,40,"filter"),
      node(4,[2,3],40,8,0,"sink")]
result=graph_summary(rows)
assert result["processors"]==4
assert result["edges"]==4
assert result["structural_longest_sum_work_us"]==220
assert [x["id"] for x in result["critical_chain_not_wall"]]==[1,2,4]
assert result["top_work_stages"][0]["name"]=="source"
assert result["top_input_starved"][0]["name"]=="sink"
assert result["top_output_stalled"][0]["name"]=="filter"
for broken in (rows+[rows[0]], [node(1,[2],100,0,0,"a"),node(2,[1],30,0,0,"b")]):
    try: graph_summary(broken)
    except AssertionError:pass
    else:raise AssertionError("Duplicate ID or cycle was accepted")
print("TSGRAPH_UNIT_TESTS_PASS")
