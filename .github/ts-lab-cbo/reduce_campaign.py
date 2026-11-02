#!/usr/bin/env python3
"""Reduce verified CI JOIN results. Never mix empty-window or failed trials."""
import argparse,collections,json,math,pathlib,statistics
def keyfile(p):
    return json.loads(p.read_text())
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True)
    parser.add_argument("--out",required=True)
    a=parser.parse_args()
    root=pathlib.Path(a.root)
    out=pathlib.Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    data=[];rejected=[]
    for path in sorted(root.rglob("result/report.json")):
        j=keyfile(path)
        series=j.get("fixture",{}).get("series")
        typ=j.get("workload","")
        if not (j.get("passed") and series and j.get("cases")):
            rejected.append({"path":str(path),"reason":"job did not pass"})
            continue
        for case,details in j["cases"].items():
            vv=details.get("variants",{})
            merge=vv.get("merge",{});hashv=vv.get("hash",{})
            if not merge.get("parity") or not hashv.get("parity"):
                rejected.append({"path":str(path),"case":case,"reason":"parity missing"})
                continue
            mr=merge.get("trials",[])
            hr=hashv.get("trials",[])
            if not mr or not hr or any(int(t.get("rows",0))==0 for t in mr+hr):
                rejected.append({"path":str(path),"case":case,"reason":"empty result"})
                continue
            a_ms=merge.get("median_ms");b_ms=hashv.get("median_ms")
            if not a_ms or not b_ms:continue
            data.append({"type":typ,"series":series,"case":case,
                         "rows":mr[0]["rows"],"merge_ms":a_ms,"hash_ms":b_ms,
                         "speedup_merge":round(b_ms/a_ms,3),
                         "speedup_hash":round(a_ms/b_ms,3),
                         "merge_cpu_ms":merge.get("median_cpu_ms"),
                         "hash_cpu_ms":hashv.get("median_cpu_ms"),
                         "merge_peak_memory":merge.get("peak_query_memory"),
                         "hash_peak_memory":hashv.get("peak_query_memory"),
                         "automatic_plan":next((s.strip() for s in vv.get("auto",{}).get("plan","").splitlines()
                                                if "Algorithm:" in s),"unknown"),
                         "source_file":str(path)})
    data.sort(key=lambda r:(r["type"],r["case"],r["series"]))
    by=collections.defaultdict(list)
    for x in data:by[(x["type"],x["case"])].append(x)
    crossings=[]
    for (typ,case),arr in by.items():
        for prev,curr in zip(arr,arr[1:]):
            if (prev["speedup_merge"]-1)*(curr["speedup_merge"]-1) < 0:
                crossings.append({"workload":typ,"case":case,
                                  "between_series":[prev["series"],curr["series"]],
                                  "warning":"Not a fitted threshold; experimental crossover only"})
    result={"status":"observations","sources":len(list(root.rglob("result/report.json"))),
            "valid_cases":len(data),"rejected":rejected,"rows":data,"crossings":crossings,
            "limitations":["Synthetic workloads, not measured production traffic",
                           "Fixed algorithm-order cache bias; interleaved A/B/B/A pending",
                           "SQL direct JOIN can be rewritten to INNER by optimizer",
                           "Native TimeSeries has different tag-registration semantics",
                           "No winner can be inferred from a single cardinality"]}
    out.write_text(json.dumps(result,indent=2)+"\n")
    lines=["# Corrected ClickHouse CBO JOIN campaign","",
           f"Validated comparisons: {len(data)}; rejected: {len(rejected)}.","",
           "Caution: direct SQL JOIN, not native PromQL. Do not include the original empty-window run.","",
           "| Workload | Series | Case | Rows | Merge ms | Hash ms | Winner | Merge speedup |",
           "|---|---:|---|---:|---:|---:|---|---:|"]
    for item in data:
        lines.append(f"| {item['type']} | {item['series']:,} | {item['case']} | "
                     f"{item['rows']:,} | {item['merge_ms']:.2f} | {item['hash_ms']:.2f} | "
                     f"{'merge' if item['speedup_merge']>1 else 'hash'} | "
                     f"{item['speedup_merge']:.2f}x |")
    lines.extend(["","## Crossovers between tested cardinalities",""])
    for x in crossings:
        lines.append(f"- {x['workload']} / {x['case']}: between {x['between_series'][0]:,} "
                     f"and {x['between_series'][1]:,} series, not a calibrated policy.")
    lines.extend(["","## Rejected evidence",""])
    for x in rejected:lines.append(f"- {x}")
    lines.extend(["","## Limitations",""])
    for note in result["limitations"]:lines.append(f"- {note}")
    out.with_suffix(".md").write_text("\n".join(lines)+"\n")
    print("CBO_REDUCED",json.dumps({"valid_cases":len(data),
                                  "rejected":len(rejected),"crossings":crossings}),flush=True)
    if len(data)<30: raise SystemExit("Too few validated comparisons")
if __name__=="__main__":
    main()
