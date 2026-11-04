"""R40 merged compiler: compare native results with R35/R37/R38, then plan invariants."""
import argparse,json,math,re,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'ts-lab-r35'))
from native import ROOT,START,Server,sql,sha
p=argparse.ArgumentParser();p.add_argument('variant',choices=['r35','r37','r38','r40']);args=p.parse_args()
paths={'stock':'r29-stock-o2','r35':'r35-metric-drop-o2','r37':'r37-injective-projection-o2','r38':'r38-without-deferral-o2','r40':'r40-cumulative-compiler-o2'}
binary=ROOT/paths[args.variant]/'bin/clickhouse'
report={'variant':args.variant,'binary_sha256':sha(binary),'cases':{},'errors':[]}
funcs=['avg_over_time','sum_over_time','count_over_time','min_over_time','max_over_time','stddev_over_time','stdvar_over_time','present_over_time','rate','increase','irate','delta','idelta','changes','resets','deriv']
parents=['sum','avg','min','max','count','group','stddev','stdvar']
queries={f'{parent}/{fun}':f'{parent} by(stage)({fun}(foo[30s]))' for parent in parents for fun in funcs}
queries.update({
 'no_by':'sum(avg_over_time(foo[30s]))',
 'by_metric':'sum by(__name__,stage)(avg_over_time(foo[30s]))',
 'without':'sum without(instance)(avg_over_time(foo[30s]))',
 'topk':'topk(1,avg_over_time(foo[30s]))',
 'binary':'sum by(stage)(avg_over_time(foo[30s]) + 1)',
 'subquery':'sum by(stage)(avg_over_time(foo[30s:10s]))',
 'offset':'sum by(stage)(avg_over_time(foo[30s] offset 5s))',
 'fixed_at':f'sum by(stage)(avg_over_time(foo[30s] @ {START+50}))',
 'single_regex':'sum by(stage)(avg_over_time({__name__=~"foo"}[30s]))',
 'multiple_metrics':'sum by(stage)(avg_over_time({__name__=~"foo|bar"}[30s]))',
 'explicit_exact':'sum by(stage)(avg_over_time({__name__="foo"}[30s]))',
 'contradictory_exact':'sum by(stage)(avg_over_time({__name__="foo",__name__="bar"}[30s]))',
 'missing_empty':'sum by(stage)(avg_over_time(foo{missing=""}[30s]))',
 'empty_result':'sum by(stage)(avg_over_time(foo{instance="does-not-exist"}[30s]))',
 'label_regex':'sum by(stage)(avg_over_time(foo{instance=~"a|b"}[30s]))',
 'label_negative':'sum by(stage)(avg_over_time(foo{instance!="a"}[30s]))',
 'root_range':'avg_over_time(foo[30s])',
 'last_metric_retained':'sum by(__name__,stage)(last_over_time(foo[30s]))',
 'ordinary_name_label':'sum by(stage)(label_replace(avg_over_time(foo[30s]),"__name__","new","stage",".*"))',
})
# Include broad single-metric, absent-label and high-cardinality inputs.
queries.update({
 'wide_stage': 'avg by(stage)(avg_over_time(wide[30s]))',
 'wide_stage_sum': 'sum by(stage)(sum_over_time(wide[30s]))',
 'wide_missing': 'sum by(absent_label)(avg_over_time(wide[30s]))',
 'wide_high_cardinality': 'sum by(instance)(avg_over_time(wide[30s]))',
 'wide_two_labels': 'sum by(stage,instance)(avg_over_time(wide[30s]))',
 'wide_without': 'sum without(instance)(avg_over_time(wide[30s]))',
 'wide_regex': 'sum by(stage)(avg_over_time(wide{stage=~"stage-(1|2|3)"}[30s]))',
 'wide_absent_match': 'sum by(stage)(avg_over_time(wide{stage=""}[30s]))',
})
def norm(raw):return sorted([json.loads(x) for x in raw.splitlines() if x],key=lambda x:json.dumps(x,sort_keys=True))
def equal(a,b):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return (math.isnan(a) and math.isnan(b)) or a==b or math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-10)
    if isinstance(a,dict) and isinstance(b,dict):return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    return a==b
with Server(binary,ROOT/'guard-data'):
    if args.variant=='r35':
        sql('DROP TABLE IF EXISTS guard')
        sql('CREATE TABLE guard ENGINE=TimeSeries SETTINGS recent_samples_ttl_seconds=0')
        for metric,instance,stage,values in [
            ('foo','a','s1',['1','2','2','0','4','reinterpretAsFloat64(toUInt64(9218868437227405314))',"toFloat64('nan')","toFloat64('inf')"]),
            ('foo','b','s1',['5','4','3','2','1','0','1','2']),
            ('foo','c','s2',['0','1','3','2','5','6','7','8']),
            ('bar','a','s1',['3','3','3','3','3','3','3','3'])]:
            pairs=','.join(f'(toDateTime64({START+(i+1)*10},3),toFloat64({v}))' for i,v in enumerate(values))
            sql(f"INSERT INTO guard (metric_name,tags,samples) SELECT '{metric}',map('stage','{stage}','instance','{instance}'),[{pairs}]")
        # 512 distinct full-label source groups, 38 stage values plus absence.
        sql(f"""INSERT INTO guard (metric_name,tags,samples)
        SELECT 'wide', map('stage', if(number%41=0, '', concat('stage-',toString(number%38))),
                          'instance',concat('unique-',toString(number)),
                          'project',concat('project-',toString(number%8))),
               [(toDateTime64({START+50},3),toFloat64(1+number%101))]
        FROM numbers(512)""")
    for key,expr in queries.items():
        escaped=expr.replace('\\','\\\\').replace("'","\\'")
        q=f"SELECT * FROM prometheusQueryRange(guard,'{escaped}',{START+20},{START+100},10) SETTINGS output_format_json_quote_denormals=1 FORMAT JSONEachRow"
        try:entry={'status':'OK','output':norm(sql(q))}
        except RuntimeError as e:
            m=re.search(r'Code: (\d+)',str(e));entry={'status':'ERROR','code':int(m.group(1)) if m else -1,'error':str(e)[:600]}
        entry['promql']=expr;report['cases'][key]=entry
    for key in ('avg/avg_over_time','by_metric','single_regex','without','subquery','offset','wide_stage','wide_high_cardinality','root_range','topk','binary'):
        expr=queries[key];q=f"SELECT * FROM prometheusQueryRange(guard,'{expr}',{START+20},{START+100},10)"
        plan=sql('EXPLAIN PLAN '+q);report['cases'][key]['plan_aggregations']=plan.count('Aggregating')
        report['cases'][key]['plan']=plan
if args.variant=='r40':
    references={name:json.loads((ROOT/f'guard-{name}.json').read_text())['cases'] for name in ('r35','r37','r38')}
    for key,v in report['cases'].items():
        for name,cases in references.items():
            old=cases[key]
            if v['status'] != old['status'] or (v['status']=='ERROR' and v['code'] != old['code']) or (v['status']=='OK' and not equal(v['output'],old['output'])):
                report['errors'].append((key,name))
    expected_source={
        'avg/avg_over_time':'r35',
        'by_metric':'r37', 'root_range':'r37', 'topk':'r37', 'binary':'r37',
        'without':'r38', 'wide_without':'r38',
        'single_regex':'r35', 'subquery':'r35', 'offset':'r35',
        'wide_stage':'r35', 'wide_high_cardinality':'r35',
    }
    for key,name in expected_source.items():
        actual=report['cases'][key]['plan_aggregations']
        expected=references[name][key]['plan_aggregations']
        if actual != expected:
            report['errors'].append(('plan',key,actual,expected,name))
report['status']='PASS' if not report['errors'] else 'FAIL'
(ROOT/f'guard-{args.variant}.json').write_text(json.dumps(report,indent=2,allow_nan=False))
print('GUARD',report['status'],'cases',len(report['cases']),'oracle_errors',report['errors'])
if report['errors']:raise SystemExit(1)
