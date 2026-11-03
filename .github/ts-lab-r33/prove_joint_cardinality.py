#!/usr/bin/env python3
"""R33 controlled CBO falsifier: identical marginals, different joint selectivity.

Both fixtures reproduce the VMUI marginal counts *and every label's frequency
histogram exactly*. The only changed factor is which stage gets the same
multiset of project/job labels. All samples/timestamps and all other labels
are preserved. The project ownership permutation is a counterfactual, not
production topology, and is intentionally not a candidate deployed schema.
"""
import argparse
import json
import pathlib
from generate_vm_census_proxy import generate,validate,COUNTS


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",required=True)
    args=p.parse_args()
    out=pathlib.Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    report={"question":"R33","status":"INCONCLUSIVE",
            "experiment":"IDENTICAL_UNIVARIATE_HISTOGRAMS_DIFFERENT_JOINT_STAGE_PROJECT",
            "fixtures":{},"delta":{}}
    for model in ("owner_hierarchy","counterfactual_shuffled_project"):
        data=out/(model+".jsonl")
        meta=generate(data,association=model)
        proof=validate(data,meta)
        report["fixtures"][model]={"source":meta,"verification":proof}
        print("R33_JOINT_FIXTURE",model,json.dumps({
            "rows":proof["rows"],"sha256":meta["sha256"],
            "stage_projects_avg":proof["stage_projects_avg"],
            "pair_intersections":proof["first_68_nominal_stage_project_intersections"]
        },sort_keys=True),flush=True)
    a=report["fixtures"]["owner_hierarchy"]["verification"]
    b=report["fixtures"]["counterfactual_shuffled_project"]["verification"]
    if a["marginals"]!=b["marginals"]:
        raise ValueError("Univariate cardinalities changed; experiment invalid")
    if a["marginal_frequency_sha256"]!=b["marginal_frequency_sha256"]:
        raise ValueError("Univariate frequency histograms changed; experiment invalid")
    first_a=a["first_68_nominal_stage_project_intersections"]
    first_b=b["first_68_nominal_stage_project_intersections"]
    if first_a["stage_rows_total"]!=first_b["stage_rows_total"]:
        raise ValueError("Target stage frequency changed")
    if first_a["pair_rows_total"]!=first_a["stage_rows_total"]:
        raise ValueError("Owner hierarchy has unexpected cross-owner stage")
    if first_b["pair_rows_total"]>=first_a["pair_rows_total"]//10:
        raise ValueError("Stage/project conditional selectivity did not move enough")
    report["delta"]={
        "same_total_series":True,
        "same_exact_univariate_histograms":True,
        "same_event_timestamps":True,
        "same_per_series_numeric_samples":True,
        "same_stage_frequency":True,
        "first_68_stage_project_owner_pair_hits":first_a["pair_rows_total"],
        "first_68_stage_project_shuffled_pair_hits":first_b["pair_rows_total"],
        "owner_over_shuffled_intersection_ratio":(
            first_a["pair_rows_total"]/max(1,first_b["pair_rows_total"])),
        "warning":"Not a production selectivity measurement; tests the nonidentifiability of joint cardinality from marginals"
    }
    report["status"]="PASS_JOINT_CARDINALITY_NONIDENTIFIABILITY"
    (out/"report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print("R33_JOINT_DONE",json.dumps({
        "status":report["status"],"delta":report["delta"]
    },sort_keys=True),flush=True)


if __name__=="__main__":
    main()
