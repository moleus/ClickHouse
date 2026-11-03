"""Local R35 benchmark support. Set TS_LAB_ROOT to a dedicated scratch directory."""
import hashlib,json,os,pathlib,signal,socket,subprocess,time,urllib.request,urllib.parse,urllib.error
ROOT=pathlib.Path(os.environ.get('TS_LAB_ROOT',str(pathlib.Path(__file__).resolve().parent/'scratch'))).resolve()
PORT=18199;DAY=86400;START=1760486400

def sql(q,qid=None,timeout=180):
    params={'wait_end_of_query':1}
    if qid:params['query_id']=qid
    req=urllib.request.Request(f'http://127.0.0.1:{PORT}/?'+urllib.parse.urlencode(params),data=q.encode())
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:return r.read().decode()
    except urllib.error.HTTPError as e:raise RuntimeError(e.read().decode()[:8000]) from e

def rows(q):return [json.loads(x) for x in sql(q).splitlines() if x]

def sha(p):
    with pathlib.Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

class Server:
    def __init__(self,binary,work):
        self.binary=str(binary);self.work=pathlib.Path(work);self.proc=None;self.log=None
    def __enter__(self):
        with socket.socket() as probe:
            probe.settimeout(.2)
            if probe.connect_ex(('127.0.0.1',PORT))==0:raise RuntimeError('Benchmark port is already occupied')
        self.work.mkdir(parents=True,exist_ok=True);cfg=self.work/'config.xml'
        cfg.write_text(f'''<clickhouse>
<logger><level>warning</level><console>true</console></logger>
<listen_host>127.0.0.1</listen_host><http_port>{PORT}</http_port><tcp_port>19099</tcp_port>
<path>{self.work/'data'}/</path><tmp_path>{self.work/'tmp'}/</tmp_path>
<max_server_memory_usage>2850000000</max_server_memory_usage><max_thread_pool_size>256</max_thread_pool_size>
<background_schedule_pool_size>16</background_schedule_pool_size>
<background_message_broker_schedule_pool_size>4</background_message_broker_schedule_pool_size>
<background_distributed_schedule_pool_size>4</background_distributed_schedule_pool_size>
<users><default><password></password><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota></default></users>
<profiles><default><allow_experimental_time_series_table>1</allow_experimental_time_series_table><enable_time_series_aggregate_functions>1</enable_time_series_aggregate_functions><max_threads>2</max_threads><max_memory_usage>1900000000</max_memory_usage><log_queries>1</log_queries></default></profiles>
<quotas><default><interval><duration>3600</duration></interval></default></quotas>
<query_log><database>system</database><table>query_log</table><flush_interval_milliseconds>250</flush_interval_milliseconds></query_log>
<default_profile>default</default_profile><default_database>default</default_database>
</clickhouse>''')
        self.log=open(self.work/'server.log','ab');env=os.environ.copy();env['CLICKHOUSE_WATCHDOG_ENABLE']='0'
        self.proc=subprocess.Popen([self.binary,'server','--config-file='+str(cfg)],stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
        try:
            for _ in range(100):
                if self.proc.poll() is not None:raise RuntimeError((self.work/'server.log').read_text()[-8000:])
                try:
                    if sql('SELECT 1',timeout=1).strip()=='1':return self
                except Exception:pass
                time.sleep(.1)
            raise RuntimeError('Server startup timeout')
        except BaseException:
            self.__exit__(None,None,None);raise
    def __exit__(self,*exc):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid,signal.SIGTERM)
            try:self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:os.killpg(self.proc.pid,signal.SIGKILL);self.proc.wait(timeout=10)
        if self.log:self.log.close()

def census():return rows("SELECT table,name,rows,marks,bytes_on_disk FROM system.parts WHERE active AND database='default' ORDER BY table,name FORMAT JSONEachRow")

def freeze():
    for x in rows("SELECT name FROM system.tables WHERE database='default' AND startsWith(name,'.inner_id.') FORMAT JSONEachRow"):
        sql('SYSTEM STOP MERGES `'+x['name']+'`')

def create_sparse(builds=250,days=14):
    sql('''CREATE TABLE sparse ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0
SAMPLES INNER COLUMNS (id Tuple(UInt64,UUID))
TAGS INNER COLUMNS (id Tuple(UInt64,UUID) DEFAULT tuple(sipHash64(metric_name),reinterpretAsUUID(sipHash128(tags))),metric_name LowCardinality(String),tags Map(LowCardinality(String),String),INDEX tags_idx tags TYPE text(tokenizer='keyValuePairs') GRANULARITY 100000000)
TIME RANGES INNER COLUMNS (id Tuple(UInt64,UUID))''')
    for day in range(days):
        sql(f'''INSERT INTO sparse (metric_name,tags,samples)
SELECT 'ci_duration',map('build',toString({day}*{builds}+intDiv(number,38)),
'stage',concat('stage-',toString(number%38)),
'project',concat('project-',toString(intDiv(number,38)%8)),
'branch',concat('branch-',toString(intDiv(intDiv(number,38),8)%13)),
'job',concat('job-',toString(intDiv(intDiv(number,38),104)%5))),
[(toDateTime64({START}+{day}*86400+intDiv(number,38)*30+number%38,3),toFloat64(1+number%101))]
FROM numbers({builds*38})''',timeout=300)
    for x in rows("SELECT name FROM system.tables WHERE database='default' AND startsWith(name,'.inner_id.') FORMAT JSONEachRow"):
        sql('OPTIMIZE TABLE `'+x['name']+'` FINAL',timeout=300)
    freeze();return builds*38*days

def catalog(days=14):
    out={}
    for case,selector in {'stage':'{stage="stage-1"}','project':'{project="project-1"}','whole':''}.items():
        expr=f'avg by(stage)(avg_over_time(ci_duration{selector}[1d]))'
        out[case]=f"SELECT * FROM prometheusQueryRange(sparse,'{expr}',{START+DAY},{START+days*DAY},86400) SETTINGS max_threads=2,log_queries=1 FORMAT JSONEachRow"
    return out

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--builds',type=int,default=250);p.add_argument('--work',default='native-data');args=p.parse_args()
    with Server(ROOT/'r29-stock-o2/bin/clickhouse',ROOT/args.work):
        print('inserted',create_sparse(args.builds),flush=True)
        (ROOT/args.work/'census.json').write_text(json.dumps(census(),indent=2))
