#!/usr/bin/env python3
"""Same-run A/B/A of v1 scalar AVG at selected metric cardinalities."""
import json, os, pathlib
from bench_bucket_cxx_abca import assert_same, normalize_any, pct, post

COUNTS=[210,1050,4200,16800,67200]
PHASES=["base_before","selector_scalar","base_after"]

def run(phase,manifest,output,canonical_file):
    start,end,step=(manifest[k] for k in ("query_start_epoch","query_end_epoch","step_seconds"))
    canonical=json.loads(canonical_file.read_text()) if phase!="base_before" else {}
    report={"phase":phase,"counts":{},"all_exact":True}
    qprefix="tslab_curve_"+os.environ.get("GITHUB_RUN_ID","local")+"_"+phase
    for count in COUNTS:
        metric="tslab_curve_%d"%count
        _,raw=post("http://127.0.0.1:18191",
            "SELECT count() FROM jenkins_ts WHERE metric_name='%s' FORMAT TSV"%metric)
        actual_count=int(raw.decode().strip())
        assert actual_count==count,(metric,actual_count,count)
        sql=("SELECT * FROM prometheusQueryRange(jenkins_ts,"
             "'avg by(stage)(avg_over_time(%s[1d]))',%d,%d,%d) FORMAT JSONEachRow"
             %(metric,start,end,step))
        reference=None
        durations=[]
        ids=[]
        for i in range(9):
            qid="%s_%d_%d"%(qprefix,count,i)
            ms,raw=post("http://127.0.0.1:18191",sql,qid)
            observed=normalize_any(raw)
            assert observed,(phase,metric,"empty")
            if reference is None:
                reference=observed
                if phase!="base_before":
                    expected={(tuple(tuple(kv) for kv in labels),int(t)):float(v)
                              for labels,t,v in canonical[metric]}
                    assert_same(expected,reference,metric,phase)
            else:
                assert_same(reference,observed,metric,phase)
            if i>=2:
                durations.append(ms)
                ids.append(qid)
        if phase=="base_before":
            canonical[metric]=[[list([k,v] for k,v in labels),t,v]
                               for (labels,t),v in sorted(reference.items())]
        report["counts"][str(count)]={"points":len(reference),"p50_ms":pct(durations,.5),
             "p95_ms":pct(durations,.95),"durations_ms":durations}
    post("http://127.0.0.1:18191","SYSTEM FLUSH LOGS")
    _,log=post("http://127.0.0.1:18191",
         "SELECT query_id,read_rows,read_bytes,memory_usage FROM system.query_log "
         "WHERE type='QueryFinish' AND startsWith(query_id,'%s') FORMAT JSONEachRow"%qprefix)
    entries={x["query_id"]:x for line in log.splitlines() if line.strip()
             for x in [json.loads(line)]}
    for count in COUNTS:
        metric=report["counts"][str(count)]
        rows=[entries["%s_%d_%d"%(qprefix,count,i)] for i in range(2,9)]
        metric.update({"read_rows_p50":pct([v["read_rows"] for v in rows],.5),
                       "read_bytes_p50":pct([v["read_bytes"] for v in rows],.5),
                       "memory_p95_bytes":pct([v["memory_usage"] for v in rows],.95)})
        print("TSLAB_CURVE",json.dumps({"phase":phase,"count":count,**metric},
                separators=(",",":")),flush=True)
    if phase=="base_before":
        canonical_file.write_text(json.dumps(canonical,separators=(",",":")))
    output.write_text(json.dumps(report,indent=2)+"\n")

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--phase",required=True,choices=PHASES)
    p.add_argument("--manifest",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--canonical",required=True)
    a=p.parse_args()
    run(a.phase,json.loads(pathlib.Path(a.manifest).read_text()),
        pathlib.Path(a.output),pathlib.Path(a.canonical))
