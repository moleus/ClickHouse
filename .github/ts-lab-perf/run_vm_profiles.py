#!/usr/bin/env python3
"""VM profiling and mandatory CH/VM correctness comparison; runs after CH cases."""
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, ".github/ts-lab-bench")
from bench_bucket_cxx_abca import normalize_any, assert_same
from bench_ch_vs_vm_dense_sparse import vm_normalized

ROOT=pathlib.Path("evidence/perf-lens")
def vm_request(expr,start,end):
    p=urllib.parse.urlencode({"query":expr,"start":start,"end":end,"step":86400,"nocache":1})
    with urllib.request.urlopen("http://127.0.0.1:8430/api/v1/query_range?"+p,timeout=90) as resp:
        data=json.load(resp)
    if data.get("status")!="success":raise RuntimeError(str(data)[:700])
    return data

def load_ch(case):
    rows=json.loads((ROOT/case/"query_result.json").read_text())
    return normalize_any("\n".join(json.dumps(z) for z in rows).encode())

def load_vm(case):
    data=json.loads((ROOT/case/"query_result.json").read_text())
    return vm_normalized({"data":data})

def check(first,second,name):
    r=assert_same(first,second,name,"strict")
    return r

def capture_profile(expr,start,end,seconds=12):
    result={"enabled":True,"seconds":seconds}
    output=ROOT/"vm-cpu.pprof"
    errors=[]
    def collect():
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:8430/debug/pprof/profile?seconds={seconds}",
                timeout=seconds+20) as resp:
                raw=resp.read()
            if len(raw)<100:raise RuntimeError("empty Go CPU profile")
            output.write_bytes(raw)
            result["bytes"]=len(raw)
        except Exception as exc:errors.append(str(exc)[:600])
    thread=threading.Thread(target=collect,daemon=True);thread.start()
    time.sleep(.20)
    deadline=time.monotonic()+seconds
    calls=0
    while time.monotonic()<deadline:
        vm_request(expr,start,end)
        calls+=1
    thread.join(timeout=seconds+25)
    result["queries_during_profile"]=calls
    if errors:result["error"]=errors
    if output.exists() and os.system("command -v go >/dev/null 2>&1")==0:
        p=subprocess.run(["go","tool","pprof","-top","-nodecount=50",str(output)],
                         text=True,capture_output=True,timeout=35)
        (ROOT/"vm-cpu-top.txt").write_text(p.stdout[:30000]+"\nSTDERR:\n"+p.stderr[:4000])
        result["go_pprof_exit"]=p.returncode
    (ROOT/"vm-cpu-profile-status.json").write_text(json.dumps(result,indent=2)+"\n")
    print("PERF_VM_GO_PPROF",json.dumps(result,separators=(",",":")),flush=True)

def main():
    q=json.loads((ROOT/"query.json").read_text())
    vm_pid=pathlib.Path(os.environ["RUNNER_TEMP"],"vm.pid").read_text().strip()
    vm_path=str(pathlib.Path(os.environ["RUNNER_TEMP"],"vm/data"))
    cases=[
        ("vm_whole14",q["expr"],14*38),
        ("vm_stage14",'avg by(stage)(avg_over_time(jenkins_ci_95k_daily{stage="stage-0007"}[1d]))',14)
    ]
    failed=[]
    for name,expr,expected in cases:
        cmd=["python3",".github/ts-lab-perf/capture.py","--engine","vm",
             "--url","http://127.0.0.1:8430",
             "--pid",vm_pid,"--data-path",vm_path,"--case",name,"--promql",expr,
             "--start",str(q["start"]),"--end",str(q["end"]),"--step","86400",
             "--output",str(ROOT/name)]
        result=subprocess.run(cmd,check=False)
        if result.returncode:failed.append({"case":name,"error":"capture exit "+str(result.returncode)})
        else:
            actual=json.loads((ROOT/name/"record.json").read_text()).get("result_points")
            if actual!=expected:failed.append({"case":name,"error":f"expected {expected}, got {actual}"})
    if not failed:
        try:
            whole=load_vm("vm_whole14")
            stage=load_vm("vm_stage14")
            original=load_ch("fulltags_whole14")
            projected=load_ch("projected_whole14")
            filtered=load_ch("projected_stage14")
            checks={
                "fulltags_vs_vm":check(whole,original,"whole original"),
                "projected_vs_vm":check(whole,projected,"whole projected"),
                "stage_vs_vm":check(stage,filtered,"stage projected")
            }
            (ROOT/"numerical-parity.json").write_text(json.dumps(checks,indent=2)+"\n")
            print("PERF_NUMERICAL_PARITY",json.dumps(checks,separators=(",",":")),flush=True)
        except Exception as exc:
            failed.append({"case":"full_output_parity","error":str(exc)[:1500]})
    if not failed:
        try:capture_profile(q["expr"],q["start"],q["end"],12)
        except Exception as exc:
            (ROOT/"vm-cpu-profile-error.txt").write_text(str(exc))
            print("PERF_VM_PPROF_UNAVAILABLE",str(exc)[:500],flush=True)
    (ROOT/"vm-profiling-status.json").write_text(json.dumps({"errors":failed},indent=2)+"\n")
    if failed:
        print("PERF_VM_PROFILE_FAILURE",json.dumps(failed,separators=(",",":")),flush=True)
        raise SystemExit(1)

if __name__=="__main__":main()
