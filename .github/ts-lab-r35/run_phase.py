"""One fresh-server native phase. Use stock/r35/r35/stock in sequence."""
import argparse,collections,datetime,json,os,pathlib,statistics,threading,time
from native import ROOT,START,DAY,Server,sql,rows,sha,catalog,census,freeze

def oracle(case,builds=250,days=14):
    sums=collections.defaultdict(float);counts=collections.defaultdict(int)
    for d in range(days):
        for b in range(builds):
            for stage in range(38):
                if case=='stage' and stage!=1:continue
                if case=='project' and b%8!=1:continue
                t=START+d*DAY+b*30+stage
                step=(t-START+DAY-1)//DAY
                if 1<=step<=days:
                    key=(f'stage-{stage}',START+step*DAY)
                    sums[key]+=1+(b*38+stage)%101;counts[key]+=1
    return {k:sums[k]/counts[k] for k in sums}

def decode(body):
    result={}
    for x in map(json.loads,body.splitlines()):
        tags=dict(x['tags']);assert set(tags)=={'stage'},tags
        for t,value in x['samples']:
            stamp=int(datetime.datetime.fromisoformat(t).replace(tzinfo=datetime.timezone.utc).timestamp())
            key=(tags['stage'],stamp);assert key not in result,key;result[key]=value
    return result

def procstats(pid):
    try:
        status=pathlib.Path(f'/proc/{pid}/status').read_text()
        rss=int(next(x for x in status.splitlines() if x.startswith('VmRSS:')).split()[1])*1024
        v=pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return rss,(int(v[11])+int(v[12]))/os.sysconf('SC_CLK_TCK')
    except (FileNotFoundError,StopIteration):return 0,0

p=argparse.ArgumentParser();p.add_argument('variant');p.add_argument('phase',type=int)
p.add_argument('--reps',type=int,default=6);p.add_argument('--builds',type=int,default=250)
p.add_argument('--work',default='native-data');p.add_argument('--cases',default='stage,project,whole');p.add_argument('--label',default='phase');args=p.parse_args()
paths={'stock':'r29-stock-o2','r32':'r32-onebucket-o2','r34':'r34-sparse-map-o2','r35':'r35-metric-drop-o2'}
binary=ROOT/paths[args.variant]/'bin/clickhouse'
report={'variant':args.variant,'phase':args.phase,'binary_sha256':sha(binary),'queries':{},'errors':[],
        'started_unix':time.time(),'cpu_max':pathlib.Path('/sys/fs/cgroup/cpu.max').read_text(),
        'memory_max':pathlib.Path('/sys/fs/cgroup/memory.max').read_text(),
        'query_threads':2,'builds_per_day':args.builds}
output=ROOT/f'{args.label}-{args.variant}-{args.phase}.json'
try:
    with Server(binary,ROOT/args.work) as srv:
        freeze();report['parts']=census();report['pid']=srv.proc.pid
        for case,q in catalog().items():
            if case not in args.cases.split(','):continue
            expect=oracle(case,builds=args.builds)
            info={'sql':q,'trials':[],'expected_points':len(expect)};report['queries'][case]=info
            for i in range(args.reps+2):
                qid=f'bench_{args.variant}_{args.phase}_{case}_{i}_{time.time_ns()}'
                samples=[];cost=[0];stop=threading.Event()
                def observe():
                    while not stop.is_set():
                        t=time.perf_counter();samples.append(procstats(srv.proc.pid)[0]);cost[0]+=time.perf_counter()-t;stop.wait(.05)
                obs=threading.Thread(target=observe);obs.start()
                _,cpu0=procstats(srv.proc.pid);t=time.perf_counter()
                try:body=sql(q,qid);elapsed=time.perf_counter()-t
                finally:stop.set();obs.join()
                _,cpu1=procstats(srv.proc.pid);actual=decode(body)
                assert actual.keys()==expect.keys(),(case,len(actual),len(expect))
                error=max(abs(actual[k]-expect[k]) for k in expect)
                assert error<1e-10,(case,error)
                info['trials'].append({'qid':qid,'warmup':i<2,'external_wall_ms':elapsed*1000,
                    'process_cpu_ms':(cpu1-cpu0)*1000,'peak_observed_rss':max(samples),
                    'observer_cpu_wall_ms':cost[0]*1000,'max_abs_error':error})
                info['output']=json.loads('['+','.join(body.splitlines())+']')
                output.write_text(json.dumps(report,indent=2))
            sql('SYSTEM FLUSH LOGS');time.sleep(.05)
            ids=','.join("'"+x['qid']+"'" for x in info['trials'])
            by={}
            for attempt in range(12):
                logs=rows(f"SELECT query_id,query_duration_ms,memory_usage,read_rows,read_bytes,ProfileEvents FROM system.query_log WHERE type='QueryFinish' AND query_id IN ({ids}) FORMAT JSONEachRow")
                by={x['query_id']:x for x in logs}
                if len(by)==len(info['trials']):break
                time.sleep(.25);sql('SYSTEM FLUSH LOGS')
            assert len(by)==len(info['trials']),('missing_query_logs',len(by),len(info['trials']))
            for trial in info['trials']:
                log=by[trial['qid']];trial.update({'wall_ms':log['query_duration_ms'],
                    'cpu_ms':log['ProfileEvents'].get('OSCPUVirtualTimeMicroseconds',0)/1000,
                    'memory_bytes':log['memory_usage'],'read_rows':log['read_rows'],
                    'read_bytes':log['read_bytes'],'events':log['ProfileEvents']})
            info['median']={k:statistics.median(t[k] for t in info['trials'] if not t['warmup'])
                for k in ('wall_ms','external_wall_ms','cpu_ms','memory_bytes','peak_observed_rss','process_cpu_ms')}
            output.write_text(json.dumps(report,indent=2));print(args.variant,args.phase,case,info['median'],flush=True)
        assert report['parts']==census(),'Physical user parts changed'
        report['status']='PASS'
except Exception as e:
    report['errors'].append(repr(e));report['status']='FAIL';raise
finally:output.write_text(json.dumps(report,indent=2))
