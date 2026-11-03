#!/usr/bin/env python3
"""R33 separate high-cardinality Jenkins-stage *snapshot* proxy.

The VMUI screenshot dated 2026-10-30 showed 53,205 selected series and
1,448 stage,741 app,623 build,430 app_name,85 branch,68 project,
34 jenkins_job,6 result,1 job,1 purpose unique values. This generator
reproduces EXACTLY those marginal counts as a synthetic label census.
It DOES NOT claim known joint label distribution, sample rates,
daily throughput or the precise screenshot time window.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import random
from collections import Counter,defaultdict

PROFILE="R33_VMUI_CENSUS_MARGINALS_PROXY"
METRIC="jenkins_avs_build_stage_duration"
COUNTS={
 "stage":1448,"app":741,"build":623,"app_name":430,
 "branch":85,"project":68,"jenkins_job":34,"result":6,
 "job":1,"purpose":1,
}
TOTAL_SERIES=53205
START=dt.datetime(2026,8,4,tzinfo=dt.timezone.utc)
DAYS=14
BRANCHES=("main",)+tuple(f"feature-{i:03d}" for i in range(1,85))
RESULTS=("success","failure","unstable","aborted","skipped","not_built")
# Explicit synthetic hierarchical assumptions, not production evidence:
# stage -> app -> project -> jenkins_job; app -> app_name.
# build numbers repeat across many app/job entities and are NOT unique IDs.


def stage_labels(stage_index):
    app_index=stage_index%COUNTS["app"]
    app_name_index=app_index%COUNTS["app_name"]
    project_index=app_index%COUNTS["project"]
    job_index=project_index%COUNTS["jenkins_job"]
    app=f"app-{app_index:04d}"
    app_name=f"application-{app_name_index:04d}"
    stage=f"pipeline/{app_name}/{app}/stage-{stage_index:04d}"
    return {
      "stage":stage,"app":app,"app_name":app_name,
      "project":f"project-{project_index:02d}",
      "jenkins_job":f"jenkins-job-{job_index:02d}",
      "job":"jenkins","purpose":"ci",
    }


def records(total=TOTAL_SERIES,seed=20261104,association='owner_hierarchy'):
    if not (COUNTS["stage"]<=total<=5_000_000):
        raise ValueError("Synthetic R33 census size outside bounds")
    if association not in ("owner_hierarchy","counterfactual_independent_project",
                           "counterfactual_shuffled_project"):
        raise ValueError("Unknown R33 label-dependency model")
    if association=="counterfactual_shuffled_project":
        if total!=TOTAL_SERIES:
            raise ValueError("Histogram-preserving shuffle only supports exact VMUI census")
        base=list(records(total=total,seed=seed,association="owner_hierarchy"))
        pairs=[(x["tags"]["project"],x["tags"]["jenkins_job"]) for x in base]
        random.Random(seed+6001).shuffle(pairs)
        for row,(project,job) in zip(base,pairs):
            row["tags"]["project"]=project
            row["tags"]["jenkins_job"]=job
            yield row
        return
    rng=random.Random(seed)
    for ordinal in range(total):
        # 1447 coprime to 1448, so every stage occurs exactly once
        # per full pass. Each stage has ~36-37 distinct build numbers.
        stage_index=(ordinal*1447)%COUNTS["stage"]
        cycle=ordinal//COUNTS["stage"]
        labels=stage_labels(stage_index)
        if association=="counterfactual_independent_project":
            # Counterfactual, NOT production: same marginal labels, but
            # erase deterministic stage->project association. This tests
            # whether a planner using only marginal cardinalities can know
            # the selectivity of stage AND project.
            key=f"R33-independent-project:{ordinal}".encode()
            new_project=int.from_bytes(hashlib.blake2b(key,digest_size=8).digest(),"little")%COUNTS["project"]
            labels["project"]=f"project-{new_project:02d}"
            labels["jenkins_job"]=f"jenkins-job-{new_project%COUNTS['jenkins_job']:02d}"
        build_number=(cycle+stage_index*7)%COUNTS["build"]
        labels["build"]=f"{build_number:04d}"
        # Force every branch/result to occur at least once and keep
        # main dominant otherwise. Do not couple branch to stage modulo.
        if ordinal<COUNTS["branch"]:
            branch=BRANCHES[ordinal]
        else:
            roll=rng.random()
            branch=("main" if roll<.7 else BRANCHES[1+rng.randrange(84)])
        if ordinal<COUNTS["result"]:
            result=RESULTS[ordinal]
        else:
            roll=rng.random()
            result=("success" if roll<.94 else
                    "failure" if roll<.975 else
                    "unstable" if roll<.99 else
                    "aborted" if roll<.994 else
                    "skipped" if roll<.998 else "not_built")
        labels["branch"]=branch
        labels["result"]=result
        # 14-day time distribution is a controlled modeling decision,
        # not taken from VMUI's screenshot.
        jitter=rng.randrange(DAYS*86400)
        timestamp=START+dt.timedelta(seconds=jitter)
        yield {
          "metric_name":METRIC,"tags":labels,
          "samples":[[timestamp.strftime("%Y-%m-%d %H:%M:%S.000"),
                      round(1+(stage_index%19)*.85+rng.random()*2,3)]]
        }


def generate(path,total=TOTAL_SERIES,association='owner_hierarchy'):
    path=pathlib.Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    sha=hashlib.sha256()
    seen=0
    with path.open("wb") as out:
        for row in records(total=total,association=association):
            b=(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n").encode()
            out.write(b)
            sha.update(b)
            seen+=1
    return {"schema_version":1,"profile":PROFILE,"metric_name":METRIC,
      "series":seen,"target_label_cardinality":COUNTS.copy(),
      "sha256":sha.hexdigest(),"bytes":path.stat().st_size,
      "start":int(START.timestamp()),
      "end":int((START+dt.timedelta(days=DAYS)).timestamp()),
      "days":DAYS,
      "source_description":"VMUI Oct30 2026 marginal label census only; synthetic joint distribution/time",
      "association_mode":association,
      "model_assumptions":["stage->app","app->project and app_name",
                           "project->jenkins_job","build numeric value reused across jobs",
                           "all series one sample, 14-day uniform simulated finish timestamps"]}


def validate(path,manifest):
    observed={k:set() for k in COUNTS}
    frequencies={k:Counter() for k in COUNTS}
    stage_to_owner={}
    stage_project_sets=defaultdict(set)
    cooccurrence=Counter()
    stage_counts=Counter()
    full_keys=set()
    h=hashlib.sha256()
    n=0
    with pathlib.Path(path).open("rb") as f:
        for ln in f:
            h.update(ln)
            row=json.loads(ln)
            if row["metric_name"]!=METRIC:raise ValueError("Metric identity mismatch")
            tags=row["tags"]
            if set(tags)!=set(COUNTS):raise ValueError("Unexpected label name set")
            for k in COUNTS:
                observed[k].add(tags[k])
                frequencies[k][tags[k]]+=1
            immutable=tuple(tags[k] for k in (
                ("app","app_name","project","jenkins_job")
                if manifest.get("association_mode","owner_hierarchy")=="owner_hierarchy"
                else ("app","app_name")
            ))
            stage_project_sets[tags["stage"]].add(tags["project"])
            stage_counts[tags["stage"]]+=1
            cooccurrence[(tags["stage"],tags["project"])]+=1
            prev=stage_to_owner.setdefault(tags["stage"],immutable)
            if prev!=immutable:raise ValueError("Stage changed app/project/job owner")
            key=tuple(sorted(tags.items()))
            if key in full_keys:raise ValueError("Duplicate complete series label tuple")
            full_keys.add(key)
            if len(row["samples"])!=1:raise ValueError("Expected one sample")
            n+=1
    if n!=manifest["series"]:raise ValueError(f"Count {n} vs manifest")
    if h.hexdigest()!=manifest["sha256"]:raise ValueError("Invalid fixture content hash")
    cardinalities={k:len(v) for k,v in observed.items()}
    for key,expected in COUNTS.items():
        if cardinalities[key]!=expected:
            raise ValueError(f"Marginal cardinality mismatch {key}: {cardinalities[key]} != {expected}")
    probes=[]
    for i in range(68):
        stage=stage_labels(i)["stage"]
        nominal_project=stage_labels(i)["project"]
        probes.append({
            "stage":stage,"project":nominal_project,
            "stage_count":stage_counts[stage],
            "intersection":cooccurrence[(stage,nominal_project)]
        })
    marginal_frequency_sha256={
        k:hashlib.sha256(json.dumps(sorted(values.items()),
              separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
        for k,values in frequencies.items()
    }
    return {"rows":n,"marginals":cardinalities,
            "marginal_frequency_sha256":marginal_frequency_sha256,
            "distinct_stage_owners":len(stage_to_owner),
            "distinct_full_label_keys":len(full_keys),"hash":h.hexdigest(),
            "stage_projects_min":min(len(x) for x in stage_project_sets.values()),
            "stage_projects_max":max(len(x) for x in stage_project_sets.values()),
            "stage_projects_avg":sum(len(x) for x in stage_project_sets.values())/len(stage_project_sets),
            "first_68_nominal_stage_project_intersections":{
                "stage_rows_total":sum(x["stage_count"] for x in probes),
                "pair_rows_total":sum(x["intersection"] for x in probes),
                "per_pair_sample":probes[:5]
            }}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--series",type=int,default=TOTAL_SERIES)
    p.add_argument("--association",default="owner_hierarchy",
                   choices=("owner_hierarchy","counterfactual_independent_project",
                            "counterfactual_shuffled_project"))
    args=p.parse_args()
    if args.series!=TOTAL_SERIES:
        raise ValueError("R33 snapshot proxy must use exact observed 53,205 series")
    meta=generate(args.output,args.series,association=args.association)
    meta["verification"]=validate(args.output,meta)
    pathlib.Path(args.manifest).write_text(json.dumps(meta,sort_keys=True,indent=2)+"\n")
    print("R33_VM_CENSUS_VERIFIED",json.dumps({
      "rows":meta["series"],"marginals":meta["verification"]["marginals"],
      "association":meta["association_mode"],
      "stage_project_cooccurrence":meta["verification"]["first_68_nominal_stage_project_intersections"],
      "bytes":meta["bytes"],"sha256":meta["sha256"]
    },sort_keys=True),flush=True)


if __name__=="__main__":
    main()
