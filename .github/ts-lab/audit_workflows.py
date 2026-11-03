#!/usr/bin/env python3
"""Inventory GitHub Actions workflows that can run on the shared research branch.

Without executing any workflows, classify push triggers, branch/path filters,
concurrency-group collision risks and cancellation policy. Do not auto-edit
historical workflows, which may be intentionally dormant in the shared branch.
"""
import argparse,fnmatch,json,pathlib,re
try:import yaml
except ImportError as e:raise SystemExit("PyYAML required: pip install pyyaml") from e

BRANCH="lab/ts-cbo-adaptive-20261103"
ROOT=pathlib.Path(".github/workflows")
DANGER=re.compile(r"\$\{\{\s*(?:github\.(?:ref|workflow|head_ref)|matrix\.)")
def as_list(v):
    if v is None:return []
    return v if isinstance(v,list) else [v]
def strlist(v):return [str(x) for x in as_list(v)]
def matches(patterns,name):
    return bool(patterns and any(fnmatch.fnmatchcase(name,p) for p in patterns))
def audit(file):
    doc=yaml.load(file.read_text(),Loader=yaml.BaseLoader) or {}
    triggers=doc.get("on",{})
    if isinstance(triggers,str):triggers={triggers:{}}
    if isinstance(triggers,list):triggers={k:{} for k in triggers}
    if not isinstance(triggers,dict):triggers={}
    push=triggers.get("push")
    has_push="push" in triggers
    if not isinstance(push,dict):push={}
    branches=strlist(push.get("branches"))
    ignored=strlist(push.get("branches-ignore"))
    paths=strlist(push.get("paths"))
    ignored_paths=strlist(push.get("paths-ignore"))
    enabled=has_push and (not branches or matches(branches,BRANCH)) and not matches(ignored,BRANCH)
    conc=doc.get("concurrency",{}) or {}
    if not isinstance(conc,dict):conc={}
    group=conc.get("group","")
    cancel=str(conc.get("cancel-in-progress","")).lower()
    unsafe=enabled and (cancel not in ("false","0","") or (bool(group) and "github.run_id" not in group))
    push_broad=enabled and not paths
    path_overlap=enabled and bool(paths and any(
        any(fnmatch.fnmatchcase(str(p),str(q)) for q in paths[i+1:]) for i,p in enumerate(paths)))
    reasons=[]
    if enabled and cancel not in ("false","0",""):reasons.append("can_cancel_existing_run")
    if enabled and bool(group) and "github.run_id" not in group:reasons.append("nonunique_concurrency_group")
    if push_broad:reasons.append("no_push_path_filter")
    if enabled and paths and not any(file.as_posix()==p for p in paths):
        reasons.append("trigger_is_not_self_path")
    if enabled and not group:reasons.append("no_explicit_concurrency_group")
    jobs=(doc.get("jobs",{}) or {})
    return {"path":file.as_posix(),"has_push":has_push,
            "enabled_on_shared_branch":enabled,"branches":branches,
            "paths":paths,"paths_ignored":ignored_paths,
            "push_broad":push_broad,"concurrency_group":group,
            "cancel_in_progress":cancel,
            "unsafe":unsafe,"reasons":reasons,
            "jobs":len(jobs) if isinstance(jobs,dict) else 0}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    a=p.parse_args()
    workflows=[audit(x) for x in sorted(ROOT.glob("*.y*ml"))]
    active=[x for x in workflows if x["enabled_on_shared_branch"]]
    dangers=[x for x in active if x["reasons"]]
    r={"branch":BRANCH,"workflows_total":len(workflows),
       "push_on_shared_branch":len(active),
       "push_triggered_on_shared_branch":active,
       "dangers":dangers,
       "safe_self_path_count":sum(1 for x in active if not x["reasons"])}
    path=pathlib.Path(a.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(r,indent=2,sort_keys=True)+"\n")
    print("TSLAB_WORKFLOW_AUDIT",json.dumps({"workflows_total":len(workflows),
       "push_on_shared_branch":len(active),"dangerous":len(dangers),
       "dangers":[{"path":x["path"],"why":x["reasons"]} for x in dangers]},separators=(",",":")),flush=True)
    if dangers:raise SystemExit(1)
if __name__=="__main__":main()
