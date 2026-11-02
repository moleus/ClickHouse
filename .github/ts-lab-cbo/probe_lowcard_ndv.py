#!/usr/bin/env python3
"""Verify whether LowCardinality dictionary defaults skew UniqV2 NDV statistics."""
import argparse,json,pathlib,urllib.request,urllib.error
URL="http://127.0.0.1:18191/"
def sql(q):
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,data=q.encode(),method="POST"),timeout=180) as res:
            return res.read().decode()
    except urllib.error.HTTPError as ex:
        raise RuntimeError(ex.read().decode(errors="replace")[:1300]) from ex
def qry(q):
    return [json.loads(x) for x in sql(q+" FORMAT JSONEachRow").splitlines() if x.strip()]
def main():
    a=argparse.ArgumentParser();a.add_argument("--output",required=True);args=a.parse_args()
    output=pathlib.Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    report={"cases":{},"errors":[]}
    for suffix,typ in [("lc","LowCardinality(String)"),("string","String"),("lc_nullable","LowCardinality(Nullable(String))")]:
        t="tslab_ndv_default_"+suffix
        item={"type":typ}
        report["cases"][suffix]=item
        try:
            sql(f"CREATE TABLE {t} (m {typ}, value UInt32) ENGINE=MergeTree ORDER BY m")
            sql(f"INSERT INTO {t} SELECT CAST('same-value',{repr(typ)}),toUInt32(number) FROM numbers(150000)")
            sql(f"OPTIMIZE TABLE {t} FINAL")
            sql(f"ALTER TABLE {t} MATERIALIZE STATISTICS ALL SETTINGS mutations_sync=2")
            item["actual"]=qry(f"SELECT count() AS n,uniqExact(m) AS exact,"
                               f"uniqCombined64(m) AS combined FROM {t}")[0]
            item["stats"]=qry("SELECT column,statistics,estimates.cardinality AS ndv,rows "
                              "FROM system.parts_columns WHERE active AND database='default' "
                              f"AND table='{t}' AND column='m'")
            item["estimated_ndv"]=item["stats"][0]["ndv"] if item["stats"] else None
            item["error_factor"]=round(item["estimated_ndv"]/item["actual"]["exact"],4) if item["estimated_ndv"] is not None else None
        except Exception as ex:
            item["error"]=str(ex)[:1200]
            report["errors"].append({suffix:item["error"]})
    report["passed"]=not report["errors"]
    output.write_text(json.dumps(report,indent=2)+"\n")
    print("LOWCARD_NDV_RESULT",json.dumps(report,separators=(",",":")),flush=True)
    if not report["passed"]:raise SystemExit(1)
if __name__=="__main__":main()
