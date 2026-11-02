#!/usr/bin/env python3
"""Same-run original/v3/original selected metric and grid-width correctness and cost."""
import argparse,json,os,pathlib
from bench_bucket_cxx_abca import assert_same,normalize_any,pct,post

METRIC_SIZES=[210,4200,67200]
GRID_STEPS=[3,14,40]
PHASES=["base_before","selector_scalar","base_after"]

def run(a):
    manifest=json.loads(pathlib.Path(a.manifest).read_text())
    end=int(manifest["query_end_epoch"])
    step=86400
    out=pathlib.Path(a.output)
    canonical_path=pathlib.Path(a.canonical)
    ref=json.loads(canonical_path.read_text()) if a.phase!="base_before" else {}
    results={}
    prefix="v3_grid_"+os.environ.get("GITHUB_RUN_ID","local")+"_"+a.phase
    for n in METRIC_SIZES:
        metric=f"tslab_curve_{n}"
        _,cnt=post("http://127.0.0.1:18191",
                   f"SELECT count() FROM jenkins_ts WHERE metric_name='{metric}' FORMAT TSV")
        assert int(cnt.decode().strip())==n
        for e in GRID_STEPS:
            start=end-(e-1)*step
            case=f"{n}_{e}"
            query=("SELECT * FROM prometheusQueryRange(jenkins_ts,"
                   "'avg by(stage)(avg_over_time(%s[1d]))',%d,%d,%d) FORMAT JSONEachRow"
                   %(metric,start,end,step))
            trials=[]
            qids=[]
            actual_first=None
            for i in range(7):
                qid=f"{prefix}_{case}_{i}"
                ms,raw=post("http://127.0.0.1:18191",query,qid)
                parsed=normalize_any(raw)
                assert parsed,("empty",a.phase,case)
                if actual_first is None:
                    actual_first=parsed
                    if a.phase!="base_before":
                        expected={(tuple(tuple(kv) for kv in labels),int(ts)):float(val)
                                  for labels,ts,val in ref[case]}
                        assert_same(expected,actual_first,case,a.phase)
                else:
                    assert_same(actual_first,parsed,case,"same_phase")
                if i>=2:
                    trials.append(ms)
                    qids.append(qid)
            if a.phase=="base_before":
                ref[case]=[[list([k,v] for k,v in labels),ts,val]
                           for (labels,ts),val in sorted(actual_first.items())]
            results[case]={"series":n,"steps":e,"points":len(actual_first),
                           "p50_ms":pct(trials,.5),"p95_ms":pct(trials,.95),
                           "trials_ms":trials,"query_ids":qids}
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,logs=post("http://127.0.0.1:18191",
        "SELECT query_id,query_duration_ms,read_rows,read_bytes,memory_usage "
        "FROM system.query_log WHERE type='QueryFinish' AND startsWith(query_id,'%s') FORMAT JSONEachRow"%prefix)
    rows={o["query_id"]:o for line in logs.splitlines() if line.strip() for o in [json.loads(line)]}
    for case,stat in results.items():
        matched=[rows[x] for x in stat.pop("query_ids")]
        stat.update({"read_rows_p50":pct([x["read_rows"] for x in matched],.5),
                     "read_bytes_p50":pct([x["read_bytes"] for x in matched],.5),
                     "memory_p95_bytes":pct([x["memory_usage"] for x in matched],.95),
                     "server_p50_ms":pct([x["query_duration_ms"] for x in matched],.5)})
        print("V3_GRID_PHASE",json.dumps({"phase":a.phase,"case":case,**stat},separators=(",",":")),flush=True)
    if a.phase=="base_before":
        canonical_path.write_text(json.dumps(ref,separators=(",",":"))+"\n")
    out.write_text(json.dumps({"phase":a.phase,"all_equal":True,"cases":results},indent=2)+"\n")
    print("V3_GRID_PHASE_DONE",json.dumps({"phase":a.phase,"cases":len(results),"all_equal":True}),flush=True)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--phase",required=True,choices=PHASES)
    p.add_argument("--manifest",required=True)
    p.add_argument("--canonical",required=True)
    p.add_argument("--output",required=True)
    run(p.parse_args())
