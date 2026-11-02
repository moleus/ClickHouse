#!/usr/bin/env python3
"""Measure PromQL label-filter patterns from PINNED PUBLIC Grafana dashboard JSON.

Counts dashboard PANEL definitions, NOT real executions; no private dashboard data.
For multi-selectors, counts each label-matcher occurrence. The extractor is
heuristic and emits parse-quality warnings, not a full PromQL grammar proof.
"""
import argparse,collections,hashlib,json,pathlib,re
LABEL=re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*(\"(?:[^\"\\]|\\.)*\")")
METRIC=re.compile(r"(?<![\w:])([a-zA-Z_:][a-zA-Z0-9_:]*)\s*\{")
VARIABLE=re.compile(r"\$\{[^{}]+\}|\$[a-zA-Z_][a-zA-Z_0-9]*")
SELECTOR=re.compile(r"\{([^{}]*)\}")
def read_targets(obj):
    # Only top-level Grafana panel targets and template variables: avoid values in JSON metadata.
    if isinstance(obj,dict):
        for target in obj.get("targets",[]):
            if isinstance(target,dict) and isinstance(target.get("expr"),str):
                yield "panel",target["expr"]
        templating=obj.get("templating")
        if isinstance(templating,dict):
            for item in templating.get("list",[]):
                if not isinstance(item,dict):continue
                q=item.get("query")
                if isinstance(q,str):yield "variable",q
                elif isinstance(q,dict) and isinstance(q.get("query"),str):
                    yield "variable",q["query"]
        for name,x in obj.items():
            if name not in ("targets","templating"):
                yield from read_targets(x)
    elif isinstance(obj,list):
        for x in obj:yield from read_targets(x)
def classify(expr):
    cleaned=VARIABLE.sub("VARIABLE",expr)
    matches=[];possible=0
    for selector in SELECTOR.findall(cleaned):
        possible+=1
        matches.extend(LABEL.findall(selector))
    matched_names=METRIC.findall(cleaned)
    label_names=[m[0] for m in matches if m[0]!="__name__"]
    return {"named_metric_count":len(matched_names),"filter_groups":possible,
            "labels":label_names,
            "operators":[(m[0],m[1]) for m in matches],
            "has_label_matcher":bool(matches),
            "has_name_filter":bool(matched_names or "__name__" in [m[0] for m in matches]),
            "possible_unsupported":possible>0 and not matches}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--dashboards",required=True)
    p.add_argument("--source-repo",required=True)
    p.add_argument("--source-commit",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    root=pathlib.Path(a.dashboards)
    files=sorted(root.rglob("*.json"))
    all_targets=[];details=[];parse_warnings=[]
    for path in files:
        try:d=json.loads(path.read_text())
        except (ValueError,UnicodeError) as exc:
            parse_warnings.append({"file":path.name,"error":str(exc)[:200]})
            continue
        doc=hashlib.sha256(path.read_bytes()).hexdigest()
        perfile=[]
        for typ,expr in read_targets(d):
            c=classify(expr)
            row={"kind":typ,"file":str(path.relative_to(root)),"formula_sha256":hashlib.sha256(expr.encode()).hexdigest(),
                 **c}
            all_targets.append(row);perfile.append(row)
        details.append({"file":str(path.relative_to(root)),"sha256":doc,"promql_queries":len(perfile)})
    counters=collections.Counter();bycase=collections.Counter();panel_presence=collections.Counter()
    for target in all_targets:
        if target["kind"]!="panel":continue
        bycase["total_panel_target_exprs"]+=1
        if target["has_name_filter"]:bycase["contains_explicit_metric_name"]+=1
        if target["has_label_matcher"]:bycase["has_at_least_one_matcher"]+=1
        if target["possible_unsupported"]:bycase["parse_warnings"]+=1
        present=set()
        for name,op in target["operators"]:
            if name!="__name__":
                counters[(name,op)]+=1
                present.add(name)
        for name in present:
            panel_presence[name]+=1
            bycase["with_any_"+name]+=1
    label_totals=collections.Counter()
    for (name,op),count in counters.items():label_totals[name]+=count
    n=bycase["total_panel_target_exprs"]
    if n<20:raise RuntimeError("Need at least 20 real public panel PromQL targets")
    recommendations=[]
    for name,c in label_totals.most_common(25):
        recommendations.append({"label":name,"occurrences":c,"panels_containing_label":panel_presence[name],
                "share_of_panel_target_exprs":round(panel_presence[name]/n,4),
                "exact":counters[(name,"=")],"regex":counters[(name,"=~")],
                "negative":counters[(name,"!=")]+counters[(name,"!~")]})
    out=pathlib.Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    result={"schema":1,"public_repo":a.source_repo,"public_source_commit":a.source_commit,
            "dashboard_json_files":len(files),"parsed_dashboards":len(details),
            "panel_queries":n,"other_query_variables":len(all_targets)-n,
            "statistical_scope":"panel definitions, not access counts, latency, traffic or all deployments",
            "regex_parser_limit":"metric selectors without braces, variable expansion, function arguments and regex escaping can be missed",
            "summary":bycase,"label_frequency":recommendations,
            "file_hashes":details,"warnings":parse_warnings,
            "hypotheses":["metric name first: default CH already supports via hash ID prefix",
                          "rank labels by observed query frequency AND series selectivity, not frequency alone",
                          "compare region/job/stage/project prefix order under concurrent ingestion and all-selectors"]}
    out.write_text(json.dumps(result,indent=2)+"\n")
    md=["# Public Grafana PromQL label-filter frequency","",
        f"Source: {a.source_repo} @ {a.source_commit}",
        f"Panels with PromQL target expressions: {n}. Dashboard JSON files: {len(files)}.",
        "",
        "These percentages are per defined panel, not per user query. They **cannot** establish a 99% production query distribution.",
        "The parser is heuristic; variables can expand into different metric names and labels.","",
        "| Label | Matcher occurrences | Panels containing label | Fraction of panels | Equality | Regex |",
        "|---|---:|---:|---:|---:|---:|"]
    for item in recommendations:
        md.append(f"| {item['label']} | {item['occurrences']} | {item['panels_containing_label']} | {100*item['share_of_panel_target_exprs']:.1f}% | {item['exact']} | {item['regex']} |")
    (out.with_suffix(".md")).write_text("\n".join(md)+"\n")
    print("PUBLIC_PROMQL_CORPUS",json.dumps({"source":a.source_repo,"commit":a.source_commit,
        "dashboard_files":len(files),"panels":n,"named_fraction":round(bycase["contains_explicit_metric_name"]/n,3),
        "labels":recommendations[:15]},separators=(",",":")),flush=True)
if __name__=="__main__":main()
