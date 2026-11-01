#!/usr/bin/env python3
"""Deterministic short-lived nodes for 14-day TimeSeries/VM experiments.

Generate one 3h generation at a time. One logical series per node lifetime.
No third-party dependencies; neither endpoint is contacted by this script.
"""
import argparse
import datetime as dt
import json
import time
from pathlib import Path

def cli():
    p=argparse.ArgumentParser()
    p.add_argument('--outdir',required=True)
    p.add_argument('--init',action='store_true')
    p.add_argument('--generation',type=int)
    p.add_argument('--slots',type=int,default=400)
    p.add_argument('--generations',type=int,default=112)
    p.add_argument('--samples-per-lifetime',type=int,default=720)
    p.add_argument('--sample-step',type=int,default=15)
    p.add_argument('--start-epoch',type=int,default=0)
    return p.parse_args()

def main():
    a=cli()
    out=Path(a.outdir)
    out.mkdir(parents=True,exist_ok=True)
    mp=out/'manifest.json'
    if a.init:
        base=a.start_epoch or (int(time.time())//60)*60-a.generations*a.samples_per_lifetime*a.sample_step
        meta={'slots':a.slots,'generations':a.generations,'samples_per_lifetime':a.samples_per_lifetime,
              'sample_step':a.sample_step,'start_epoch':base,
              'end_epoch':base+a.generations*a.samples_per_lifetime*a.sample_step-a.sample_step,
              'query_start_epoch':base+300,
              'query_end_epoch':base+a.generations*a.samples_per_lifetime*a.sample_step-a.sample_step,
              'series':a.slots*a.generations,
              'samples':a.slots*a.generations*a.samples_per_lifetime,
              'metric':'lab_node_network_receive_bytes_total',
              'generator':'sparse-long14-v1'}
        mp.write_text(json.dumps(meta,indent=2)+'\n')
        print(json.dumps(meta))
        return
    if a.generation is None:
        raise ValueError("Pass --init once or --generation N")
    m=json.loads(mp.read_text())
    g=a.generation
    if not 0<=g<m['generations']:
        raise ValueError((g,m['generations']))
    n=m['samples_per_lifetime']
    origin=m['start_epoch']+g*n*m['sample_step']
    stamps=[(dt.datetime.fromtimestamp(origin+j*m['sample_step'],dt.timezone.utc)
              .strftime('%Y-%m-%d %H:%M:%S.000'),(origin+j*m['sample_step'])*1000)
            for j in range(n)]
    ch=out/'ch-batch.jsonl'
    vm=out/'vm-batch.prom'
    metric=m['metric']
    with ch.open('w') as clickhouse,vm.open('w') as victoria:
        for slot in range(m['slots']):
            labels={'instance':f'node-{g:03d}-{slot:03d}',
                    'slot':f'{slot:03d}','project':f'project-{slot%32:02d}',
                    'region':'east' if slot%2==0 else 'west'}
            text_labels=','.join(k+'="'+v+'"' for k,v in labels.items())
            offset=1000+slot*100+g
            samples=[]
            for j,(timestamp,tsms) in enumerate(stamps):
                value=offset+15.0*(j+1)
                samples.append([timestamp,value])
                victoria.write(f'{metric}{{{text_labels}}} {value:.1f} {tsms}\n')
            clickhouse.write(json.dumps({'metric_name':metric,'tags':labels,'samples':samples},separators=(',',':'))+'\n')
    print(json.dumps({'generation':g,'series':m['slots'],'samples':m['slots']*n,
                      'clickhouse_bytes':ch.stat().st_size,'victoria_bytes':vm.stat().st_size,
                      'generation_start':origin,'generation_end':stamps[-1][1]//1000}))

if __name__=='__main__':
    main()
