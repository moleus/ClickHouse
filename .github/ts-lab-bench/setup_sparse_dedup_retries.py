#!/usr/bin/env python3
"""Negative control: exact retry changes raw AVG, FINAL repairs logical series."""
import argparse,json,pathlib
from bench_bucket_cxx_abca import post

HOST="http://127.0.0.1:18191"
def sql(s):
    _,raw=post(HOST,s)
    return raw.decode("utf-8","replace").strip()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--event-manifest",required=True)
    p.add_argument("--output",required=True)
    a=p.parse_args()
    source=json.loads(pathlib.Path(a.event_manifest).read_text())
    assert source["unique_series"]==40000 and source["one_sample_per_series_verified"]
    table="events_dedup"
    sql("CREATE TABLE events_dedup AS events_stage "
        "ENGINE=ReplacingMergeTree PARTITION BY toYYYYMM(ts) "
        "ORDER BY (metric_name,stage,project,branch,build,ts) "
        "SETTINGS index_granularity=256")
    sql("INSERT INTO events_dedup SELECT * FROM events_stage")
    n=int(sql("SELECT count() FROM events_dedup FORMAT TSV"))
    assert n==40000,n
    # Prevent background merges from hiding replay duplication during this CI test.
    sql("SYSTEM STOP MERGES events_dedup")
    sql("INSERT INTO events_dedup SELECT * FROM events_stage "
        "WHERE project='project-00' ORDER BY build LIMIT 2000")
    physical=int(sql("SELECT count() FROM events_dedup FORMAT TSV"))
    logical=int(sql("SELECT count() FROM events_dedup FINAL FORMAT TSV"))
    assert physical==42000 and logical==40000,(physical,logical)
    base=("SELECT stage,toUInt32(intDiv(toUnixTimestamp64Milli(ts),86400000)*86400+86400) AS epoch,"
          "avg(value) AS avg_v FROM {} GROUP BY stage,epoch ORDER BY stage,epoch FORMAT JSONEachRow")
    def parsed(table):
        raw=sql(base.format(table))
        return {(r["stage"],int(r["epoch"])):float(r["avg_v"])
                for x in raw.splitlines() if x.strip() for r in [json.loads(x)]}
    source_data=parsed("events_stage")
    raw_data=parsed("events_dedup")
    dedup_data=parsed("events_dedup FINAL")
    assert set(source_data)==set(raw_data)==set(dedup_data)
    differences=[{"key":k,"correct":source_data[k],"duplicate_avg":raw_data[k]}
                 for k in source_data if abs(source_data[k]-raw_data[k])>1e-10]
    assert differences,("retry negative control did not modify any mean")
    assert max(abs(source_data[k]-dedup_data[k]) for k in source_data)<1e-10
    _,parts=post(HOST,
        "SELECT sum(bytes_on_disk),sum(marks),count() FROM system.parts "
        "WHERE active AND database='default' AND table='events_dedup' FORMAT TSV")
    disk,marks,part_count=(int(x) for x in parts.decode().split("\t"))
    report={"source_sha256":source["sha256"],"physical_rows":physical,
            "logical_rows_final":logical,"replayed_rows":2000,
            "negative_control_changed_groups":len(differences),
            "negative_examples":differences[:6],
            "final_max_error_vs_unique_source":max(abs(source_data[k]-dedup_data[k])
                                                      for k in source_data),
            "disk_bytes":disk,"selected_marks":marks,"parts":part_count}
    pathlib.Path(a.output).write_text(json.dumps(report,indent=2)+"\n")
    print("EVENT_DEDUP_FIXTURE",json.dumps(report,separators=(",",":")),flush=True)

if __name__=="__main__":main()
