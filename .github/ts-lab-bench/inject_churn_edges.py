#!/usr/bin/env python3
"""Add deterministic short-lived gap/reset and late-sample series to CH and VM.

This experiment uses the exact same input points for both systems. No duplicate
timestamps are written. Data is injected after the bulk-ingest validation.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import urllib.parse
import urllib.request


def post(base,payload,params=None):
    url=base+('/?'+urllib.parse.urlencode(params) if params else '')
    with urllib.request.urlopen(urllib.request.Request(url,data=payload.encode()),timeout=180) as r:
        return r.read().decode()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    meta=json.loads(pathlib.Path(args.manifest).read_text())
    base=meta['start_epoch']
    metric=meta['metric']
    # Values reset after the long gap. Each logical series has a different lifetime.
    cases=[
      ('edge-gap-old',[(600,100),(615,115),(630,130),(645,145),(720,160),
                       (735,175),(1500,180),(1515,195),(3600,210),
                       (3615,225),(5400,5),(5415,20),(6900,35),(6915,50),(6930,65)],{1500,5415}),
      ('edge-gap-new',[(10800,100),(10815,115),(10830,130),(10845,145),
                       (11200,160),(11215,175),(13000,5),(13015,20),
                       (15000,35),(15015,50),(18000,65),(18015,80),(18030,95)],{11200}),
    ]
    sent=[]
    all_samples=0
    for phase in ('initial','late'):
        ch=[]
        vm=[]
        for name,samples,late_offsets in cases:
            labels={'instance':name,'project':'project-00','region':'east'}
            pairs=[]
            for offset,value in samples:
                if (offset in late_offsets)!=(phase=='late'):
                    continue
                instant=base+offset
                timestamp=dt.datetime.fromtimestamp(instant,dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.000')
                pairs.append([timestamp,float(value)])
                lab=','.join(f'{k}="{v}"' for k,v in labels.items())
                vm.append(f'{metric}{{{lab}}} {float(value)} {instant*1000}\n')
            if pairs:
                ch.append(json.dumps({'metric_name':metric,'tags':labels,'samples':pairs},separators=(',',':')))
                all_samples+=len(pairs)
        for table in ('long_ts',):
            post('http://127.0.0.1:18191','\n'.join(ch)+'\n',{
                'allow_experimental_time_series_table':1,
                'wait_end_of_query':1,
                'query':f'INSERT INTO {table} (metric_name,tags,samples) FORMAT JSONEachRow'})
        post('http://127.0.0.1:8430/api/v1/import/prometheus',''.join(vm))
        sent.append({'phase':phase,'samples':sum(len(x.split('samples":[')[-1].split(']]')[0].split('],[')) for x in ch),
                     'ch_sha256':hashlib.sha256(('\n'.join(ch)+'\n').encode()).hexdigest(),
                     'vm_sha256':hashlib.sha256(''.join(vm).encode()).hexdigest()})
    query="SELECT name FROM system.tables WHERE database='default' AND startsWith(name,'.inner_id.samples.') FORMAT TSVRaw"
    name=post('http://127.0.0.1:18191',query).strip()
    if len(name.splitlines())!=1:
        raise AssertionError(('internal sample tables',name))
    q='SELECT sum(length(samples)) FROM default.`'+name.replace('`','``')+'`'
    actual=int(post('http://127.0.0.1:18191',q).strip())
    expected=meta['samples']+all_samples
    if actual!=expected:
        raise AssertionError(('CH sample count',actual,expected))
    status={'input_series':2,'new_samples':all_samples,'ch_total_samples':actual,
            'reset_and_gaps':True,'late_out_of_order_insert':True,
            'phases':sent}
    pathlib.Path(args.output).write_text(json.dumps(status,indent=2)+'\n')
    print('EDGE_INJECTION',json.dumps(status),flush=True)


if __name__=='__main__':
    main()
