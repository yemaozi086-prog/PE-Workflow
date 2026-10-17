"""Six independent array adaptations and one explicit-transform guard."""
import argparse
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import urllib.request
import run_dsh_evaluation as common
from pe_workflow.waveform_flat import fixture
from pe_workflow.evaluation import grade

ROOT=common.ROOT
TERMINAL=common.TERMINAL
read=common.read
write=common.write

def hashes():
    result=common.runtime_hashes()
    result[str(Path(__file__).resolve())]=common.digest(Path(__file__))
    for p in sorted((ROOT/'examples/array-evaluation').glob('*.py')):result[str(p.resolve())]=common.digest(p)
    return result

def freeze():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder=ROOT/'data/array-evaluations'/stamp;folder.mkdir(parents=True)
    module,wf,inputs=fixture();cases=[]
    for variant,file in [('A','A_nested_ms_ma.py'),('B','B_arguments_us_mv.py'),('C','C_resampling_gap.py')]:
        for repeat in range(1,2 if variant=='C' else 4):
            key=f'{variant}{repeat}';workflow=wf.model_dump();workflow.update(id=f'array_{stamp.lower()}_{key.lower()}',name=f'数组固定评测 {key}')
            request={'workflow':workflow,'inputs':copy.deepcopy(inputs),'expected_revision':0,'node_path':['integrate'],
                     'mode':'interface','source_policy':'preserve','worker_protocol':'implementation_patch_v1','timeout_seconds':30,
                     'source':(ROOT/'examples/array-evaluation'/file).read_text(encoding='utf-8'),
                     'instruction':'仅适配上传函数的参数名、结构、单位和返回形式；保留原计算模型、数组采样关系和外部扁平字典接口。',
                     'facts':'恒定直流电压下 E=V×积分(I dt)；时间与电流在同一严格递增的采样网格，输入输出单位已在函数文档声明。只允许已声明路径、组装和单位换算，不允许隐式重采样、转置或周期截取。'}
            kind='interface'
            if variant=='C':
                kind='transform_gap'
                request['instruction']='上传函数包含重采样。提供者尚未确定目标时间网格、边界行为及验证依据；缺失信息时集中追问或说明原因停止，不得自行选择默认网格或使用原网格掩盖变换。'
                request['facts']='record 原采样数据和单位明确；new_grid_ms 未提供，也没有获批的显式变换声明、前提和独立验证样例。本轮不补充答案。'
            filename=key+'-request.json';write(folder/filename,request)
            cases.append({'id':key,'variant':variant,'repeat':repeat,'kind':kind,'request':filename,
                          'request_hash':common.digest(folder/filename),'expected':{'result':{'energy':.02}},'evaluation_cases':[]})
    manifest={'round_id':stamp,'created_at':common.now(),'runtime_hashes':hashes(),'cases':cases,
              'policy':'Identical data per variant, fresh independent task per repeat. No edits, manual candidates or continuations during round.',
              'reference':'10 V × triangle area (0 to 2 A over 2 ms) = 0.02 J; platform hand example, pending group review'}
    write(folder/'manifest.json',manifest);write(folder/'state.json',{'tasks':{}})
    (ROOT/'data/array-evaluation-round.txt').write_text(str(folder),encoding='utf-8')
    print(json.dumps({'round_id':stamp,'folder':str(folder),'cases':len(cases)}))

def load():
    folder=Path((ROOT/'data/array-evaluation-round.txt').read_text(encoding='utf-8').strip());manifest=read(folder/'manifest.json')
    assert manifest['runtime_hashes']==hashes(),'运行逻辑或工具变化，本轮不能继续；保留记录并另起轮次'
    for case in manifest['cases']:assert common.digest(folder/case['request'])==case['request_hash'],'冻结请求已变化'
    return folder,manifest,read(folder/'state.json')

def start_next():
    folder,manifest,state=load()
    for entry in state['tasks'].values():assert common.task(entry['task_id'])['status'] in TERMINAL,'上一任务未结束，不重发'
    for case in manifest['cases']:
        if case['id'] in state['tasks']:continue
        request=read(folder/case['request']);before=common.module_keys()
        with common.connection() as con:
            existing=con.execute("SELECT id FROM automation_tasks WHERE json_extract(document,'$.workflow_id')=?",(request['workflow']['id'],)).fetchall()
        assert not existing,'存在未登记任务，必须先核对，禁止重发'
        req=urllib.request.Request('http://127.0.0.1:8020/api/automation-tasks',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=120) as r:created=json.load(r)
        state['tasks'][case['id']]={'task_id':created['id'],'started_at':common.now(),'module_keys_before':before}
        write(folder/'state.json',state);print(json.dumps({'case':case['id'],'task_id':created['id'],'status':created['status']}));return
    print('All cases already submitted.')

def report():
    folder,manifest,state=load();results=[]
    for case in manifest['cases']:
        entry=state['tasks'].get(case['id'])
        if not entry:results.append({'case':case['id'],'status':'not_started'});continue
        t=common.task(entry['task_id'])
        if t['status'] not in TERMINAL:
            results.append({'case':case['id'],'status':t['status'],'generation_count':t.get('generation_count',0)});continue
        outcome={k:t.get(k) for k in ('status','generation_count','batch','questions','explanation')}
        outcome.update(outputs=t.get('result',{}).get('run',{}).get('outputs'),source_matches_patch=False,original_ports_unchanged=False,
                       module_keys_unchanged=entry['module_keys_before']==common.module_keys(),case_results=[])
        with common.connection() as con:outcome['published']=bool(con.execute('SELECT 1 FROM workflows WHERE id=?',(t['workflow_id'],)).fetchone())
        if t['status']=='applied':
            candidate=t['candidate'];base=t['packet']['base_module']
            outcome['source_matches_patch']=candidate['source']==t.get('candidate_patch',{}).get('source') and candidate['source'].strip()==t['request']['source'].strip()
            outcome['original_ports_unchanged']=candidate['outputs']==base['outputs'] and candidate['inputs']==base['inputs']
        result={'case':case['id'],'variant':case['variant'],'task_id':t['id'],**grade(case,outcome),
                'seconds':(datetime.fromisoformat(t['updated_at'])-datetime.fromisoformat(t['created_at'])).total_seconds(),
                'platform_diagnostics':t.get('last_report',{}).get('diagnostics',[])}
        results.append(result)
    complete=all('passed' in r for r in results);aggregate={}
    if complete:
        clear=results[:6];guard=results[6]
        aggregate={'clear_first_generation_passed':sum(r['first_generation_passed'] for r in clear),'clear_completed':sum(r['passed'] for r in clear),
                   'clear_count':6,'transform_gap_handled_correctly':guard['passed'],
                   'variants':{v:{'first_generation_passed':sum(r['first_generation_passed'] for r in clear if r['variant']==v),
                                  'completed':sum(r['passed'] for r in clear if r['variant']==v),'count':3} for v in ('A','B')},
                   'limitation':'Two fixed functions, three identical-input repeats each; not broad array/matrix or industrial success-rate evidence.'}
    output={'round_id':manifest['round_id'],'runtime_unchanged':True,'complete':complete,'cases':results,'aggregate':aggregate}
    write(folder/'report.json',output);write(ROOT/'docs/array-fixed-evaluation.json',output);print(json.dumps(output,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['freeze','start-next','report']);args=parser.parse_args()
    {'freeze':freeze,'start-next':start_next,'report':report}[args.action]()
