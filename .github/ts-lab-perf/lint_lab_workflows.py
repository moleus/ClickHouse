#!/usr/bin/env python3
"""Low-cost CI validation of lab Python entrypoints and workflow CLI contracts.

Uses Python ast and simple YAML shell-text scanning. It is a preflight, not a
full GitHub Actions YAML or Bash parser. Unknown syntaxes are reported, not
assumed correct. Intended to run before any 1.33m-row import.
"""
import argparse,ast,json,pathlib,re,sys
ROOT=pathlib.Path.cwd()
def program_flags(path):
    tree=ast.parse(path.read_text(),filename=str(path))
    flags=set();required=set();aliases={}
    def options_from(node):
        if isinstance(node,(ast.Tuple,ast.List)):
            return [e.value for e in node.elts if isinstance(e,ast.Constant) and isinstance(e.value,str)]
        return []
    for node in ast.walk(tree):
        if isinstance(node,ast.For) and isinstance(node.target,ast.Name):
            v=options_from(node.iter)
            if v:aliases[node.target.id]=v
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute) or node.func.attr!="add_argument":
            continue
        is_required=any(k.arg=="required" and isinstance(k.value,ast.Constant) and k.value.value is True
                        for k in node.keywords)
        for arg in node.args:
            names=[]
            if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and arg.value.startswith("--"):
                names=[arg.value]
            if isinstance(arg,ast.BinOp) and isinstance(arg.op,ast.Add) and isinstance(arg.left,ast.Constant):
                if isinstance(arg.left.value,str) and arg.left.value=="--" and isinstance(arg.right,ast.Name):
                    names=["--"+k for k in aliases.get(arg.right.id,[])]
            for flag in names:
                flags.add(flag)
                if is_required:required.add(flag)
    return flags,required

def workflow_commands(text):
    records=[]
    lines=text.splitlines()
    # Recognize standalone Python CLI calls in shell run: blocks.
    for idx,line in enumerate(lines):
        match=re.search(r"(?<![-\w])python3\s+(\.github/[\w./-]+\.py)(?:\s|$)",line)
        if not match or "py_compile" in line:
            continue
        script=match.group(1)
        parts=[line[match.end():]]
        n=idx
        while n+1<len(lines) and n-idx<12 and lines[n].rstrip().endswith("\\"):
            n+=1;parts.append(lines[n])
        command=" ".join(parts)
        flags=set(re.findall(r"(?<![\w-])--[A-Za-z][A-Za-z0-9-]*",command))
        records.append({"line":idx+1,"script":script,"flags":sorted(flags)})
    return records

def validate(path):
    content=path.read_text()
    errors=[];warnings=[]
    if re.search(r"(?m)^\s+schedule\s*:",content):
        errors.append("Scheduled workflow detected; fork lab must not run cron")
    if re.search(r"(?m)^\s+cancel-in-progress:\s*true\b",content):
        errors.append("cancel-in-progress true may disrupt build caches")
    if "timeout-minutes:" not in content:
        warnings.append("Missing bounded job timeout")
    commands=workflow_commands(content)
    for c in commands:
        target=ROOT/c["script"]
        if not target.exists():
            errors.append(f"line {c['line']}: missing Python target {c['script']}")
            continue
        try:flags,req=program_flags(target)
        except (SyntaxError,OSError) as e:
            errors.append(f"line {c['line']}: invalid target {c['script']}: {str(e)[:300]}")
            continue
        missing=req-set(c["flags"])
        unknown=set(c["flags"])-flags
        if missing:
            errors.append(f"line {c['line']} {c['script']}: required CLI arguments missing: {sorted(missing)}")
        if unknown:
            errors.append(f"line {c['line']} {c['script']}: invalid CLI arguments: {sorted(unknown)}")
    return {"path":str(path.relative_to(ROOT)),"commands":commands,
            "errors":errors,"warnings":warnings}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--workflow",action="append",default=[])
    p.add_argument("--all-lab",action="store_true")
    p.add_argument("--output",required=True)
    a=p.parse_args()
    paths=[ROOT/x for x in a.workflow]
    if a.all_lab:paths+=sorted((ROOT/".github/workflows").glob("ts-lab-*.yml"))
    paths=list(dict.fromkeys(paths))
    if not paths:raise SystemExit("No workflows selected")
    reports=[]
    for path in paths:
        if not path.is_file():
            reports.append({"path":str(path),"commands":[],"errors":["workflow not found"],"warnings":[]})
        else:reports.append(validate(path))
    count=sum(len(x["errors"]) for x in reports)
    result={"schema":1,"workflow_count":len(reports),"checked_commands":sum(len(x["commands"]) for x in reports),
            "errors_total":count,"reports":reports,
            "limitations":["Not a complete YAML parser, shell expansion or Bash interpreter",
                "Dynamic Python argument construction may need dedicated tests",
                "Artifact archive structure and CLI output cannot be proven without CI fixtures"]}
    output=pathlib.Path(a.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+"\n")
    for r in reports:
        if r["errors"] or r["warnings"]:
            print("LAB_PREFLIGHT_DETAIL",json.dumps({"path":r["path"],"errors":r["errors"],
                  "warnings":r["warnings"]},separators=(",",":")),flush=True)
    print("LAB_PREFLIGHT_DONE",json.dumps({"workflows":len(paths),
          "commands":result["checked_commands"],"errors":count},separators=(",",":")),flush=True)
    if count:raise SystemExit(1)

if __name__=="__main__":main()
