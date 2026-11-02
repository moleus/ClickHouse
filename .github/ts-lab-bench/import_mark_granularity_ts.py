#!/usr/bin/env python3
"""Import identical TimeSeries rows into alternative samples mark-granularity tables."""
import argparse,hashlib,json,pathlib
from bench_bucket_cxx_abca import post

HOST="http://127.0.0.1:18191"
GRANULARITIES=(64,16,4)

def query(sql):
    _,raw=post(HOST,sql)
    return raw.decode("utf-8","replace").strip()

def internal_suffix(table):
    uid=query(f"SELECT toString(uuid) FROM system.tables WHERE database='default' AND name='{table}' FORMAT TSV")
    assert len(uid)==36,("TimeSeries UUID",table,uid)
    return uid

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--fixture",required=True)
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    manifest=json.loads(pathlib.Path(a.manifest).read_text())
    digest=hashlib.sha256()
    with pathlib.Path(a.fixture).open("rb") as f:
        for line in f:digest.update(line)
    assert digest.hexdigest()==manifest["sha256"],"source file hash mismatch"
    results={}
    for granularity in (256,)+GRANULARITIES:
        table="cost_ts" if granularity==256 else f"cost_g{granularity}"
        if granularity!=256:
            query(f"CREATE TABLE {table} ENGINE=TimeSeries SETTINGS "
                  f"recent_samples_ttl_seconds=0,samples_index_granularity={granularity}")
            block=[]
            total_bytes=0
            inserted=0
            def flush():
                nonlocal total_bytes,inserted
                if not block:return
                statement=(f"INSERT INTO {table} (metric_name,tags,samples) "
                           "FORMAT JSONEachRow\n").encode()+b"".join(block)
                post(HOST,statement.decode("utf-8"))
                inserted+=len(block)
                block.clear()
                total_bytes=0
            with pathlib.Path(a.fixture).open("rb") as stream:
                for line in stream:
                    block.append(line)
                    total_bytes+=len(line)
                    if total_bytes>=2_000_000:flush()
                flush()
            assert inserted==41000,(table,inserted)
        n=int(query(f"SELECT count() FROM {table} FORMAT TSV"))
        assert n==41000,(table,n)
        suffix=internal_suffix(table)
        internal=f"default."+chr(96)+".inner_id.samples."+suffix+chr(96)
        samples=int(query(f"SELECT sum(length(samples)) FROM {internal} FORMAT TSV"))
        assert samples==1000000,(table,samples)
        parts=query("SELECT sum(bytes_on_disk),sum(marks),count() FROM system.parts "
                    f"WHERE database='default' AND table='.inner_id.samples.{suffix}' "
                    "AND active FORMAT TSV")
        chunks=parts.split("\t")
        record={"granularity":granularity,"table":table,"suffix":suffix,
                "outer_rows":n,"samples":samples,
                "sample_parts_disk_bytes":int(chunks[0]),
                "sample_parts_marks":int(chunks[1]),"physical_parts":int(chunks[2])}
        results[granularity]=record
        print("MARK_GRANULARITY_IMPORT",json.dumps(record,separators=(",",":")),flush=True)
    pathlib.Path(a.output).write_text(json.dumps({
        "source_sha256":digest.hexdigest(),"variants":results},indent=2)+"\n")
if __name__=="__main__":main()
