"""Frozen three-case real DSH evaluation. Never repairs or continues a task."""
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/'backend'))
from pe_workflow.dictionary_demo import fixture
from pe_workflow.evaluation import grade

TERMINAL={'applied','already_current','failed','paused','blocked','needs_clarification','needs_evidence','version_changed'}

def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def now():return datetime.now(timezone.utc).isoformat()
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def connection():return sqlite3.connect(ROOT/'data/workbench.sqlite')
def task(key):
    with connection() as con:row=con.execute('SELECT document FROM automation_tasks WHERE id=?',(key,)).fetchone()
    return json.loads(row[0])
def module_keys():
    with connection() as con:return [list(r) for r in con.execute('SELECT id,version FROM modules ORDER BY id,version')]

def runtime_hashes():
    paths=list((ROOT/'backend/pe_workflow').rglob('*.py'))
    paths+=list((ROOT/'dsh/preset/flux-worker').glob('*'))
    paths+=[ROOT/'bridge/flux_worker_client.py',Path(__file__).resolve()]
    installed=Path.home()/'.dsh/.agent-presets/flux-worker'
    paths+=[installed/'agent.cordis.yml',installed/'flux-worker-tools-patch-v1.js']
    return {str(p.resolve()):digest(p) for p in sorted(paths) if p.is_file()}

def freeze():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder=ROOT/'data/evaluations'/stamp;folder.mkdir(parents=True)
    _,wf,inputs=fixture();wf=wf.model_dump();prefix='eval_'+stamp.lower()
    base={'workflow':wf,'inputs':inputs,'expected_revision':0,'node_path':['loss'],'mode':'interface',
          'source_policy':'preserve','worker_protocol':'implementation_patch_v1','timeout_seconds':60}
    p=100*.001/(2*math.pi*70000*200e-9)
    expected={'loss':{'cap1_loss':p,'p_cap_total':p}}
    clear=copy.deepcopy(base);clear['workflow'].update(id=prefix+'_interface',name='固定评测 A · 明确接口变体')
    clear.update(source=(ROOT/'examples/user-upload-tests/A_clear_interface.py').read_text(encoding='utf-8'),
                 instruction='仅适配上传函数到所选电容损耗节点，保留原公式和外部接口。',
                 facts='函数文档已给出输入输出单位；明确为单组谐振电容介质损耗，按已声明取值路径、改名、单位换算和返回映射适配。')
    with connection() as con:
        old=json.loads(con.execute('SELECT document FROM automation_tasks WHERE id=?',((ROOT/'data/thermal-task-id.txt').read_text().strip(),)).fetchone()[0])
    formula=copy.deepcopy(old['request']);formula.update(worker_protocol='implementation_patch_v1',expected_revision=0)
    formula['workflow'].update(id=prefix+'_formula',name='固定评测 B · 新增温度参数与公式')
    uncertain=copy.deepcopy(base);uncertain['workflow'].update(id=prefix+'_ambiguity',name='固定评测 C · 返回语义缺口')
    uncertain.update(source=(ROOT/'examples/user-upload-tests/B_pending_output_unit.py').read_text(encoding='utf-8'),
                     instruction='适配上传函数，但返回单位和最终倍率尚未经提供者确认。不能猜测，也不能以数值碰巧匹配替代语义依据；集中追问关键缺口，未确认前不能发布。',
                     facts='输入含义和单位见函数文档。返回 raw_group/raw_total 的单位及最终 1000 的用途未确认；本轮不补充答案。')
    reference=read(ROOT/'docs/dbsrc-thermal-reference.json')
    specs=[('A','interface',clear,expected,[]),('B','formula',formula,reference['cases'][1]['expected'],formula['evaluation_cases']),('C','ambiguity',uncertain,None,[])]
    cases=[]
    for key,kind,request,answer,evals in specs:
        filename=key+'-request.json';write(folder/filename,request)
        cases.append({'id':key,'kind':kind,'request':filename,'request_hash':digest(folder/filename),'expected':answer,'evaluation_cases':evals})
    manifest={'round_id':stamp,'created_at':now(),'runtime_hashes':runtime_hashes(),'cases':cases,
              'policy':'No runtime edits, candidate hand edits, continuations or additional budget. Platform fixes require a new separately reported round.',
              'reference_label':'平台自带手算与冻结原 ENV 扩展参考，待课题组核对；不代表器件标定'}
    write(folder/'manifest.json',manifest);write(folder/'state.json',{'tasks':{}})
    (ROOT/'data/evaluation-round.txt').write_text(str(folder),encoding='utf-8');print(json.dumps({'round_id':stamp,'folder':str(folder),'cases':3}))

def load():
    folder=Path((ROOT/'data/evaluation-round.txt').read_text(encoding='utf-8').strip())
    manifest=read(folder/'manifest.json');assert manifest['runtime_hashes']==runtime_hashes(),'协议或运行源码已改变；本轮不可继续，必须保留失败并新开轮次'
    for case in manifest['cases']:assert digest(folder/case['request'])==case['request_hash'],'用例请求已改变'
    return folder,manifest,read(folder/'state.json')

def start_next():
    folder,manifest,state=load()
    for entry in state['tasks'].values():assert task(entry['task_id'])['status'] in TERMINAL,'上一用例尚未结束；禁止并行或重发'
    for case in manifest['cases']:
        if case['id'] in state['tasks']:continue
        request=read(folder/case['request']);before=module_keys()
        req=urllib.request.Request('http://127.0.0.1:8020/api/automation-tasks',data=json.dumps(request).encode(),headers={'Content-Type':'application/json'})
        # No blind retry: an uncertain HTTP response needs reconciliation first.
        with urllib.request.urlopen(req,timeout=120) as r:created=json.load(r)
        state['tasks'][case['id']]={'task_id':created['id'],'started_at':now(),'module_keys_before':before}
        write(folder/'state.json',state);print(json.dumps({'case':case['id'],'task_id':created['id'],'status':created['status']}));return
    print('All cases already submitted.')

def report():
    folder,manifest,state=load();results=[]
    for case in manifest['cases']:
        entry=state['tasks'].get(case['id'])
        if not entry:results.append({'case':case['id'],'status':'not_started'});continue
        t=task(entry['task_id'])
        if t['status'] not in TERMINAL:
            results.append({'case':case['id'],'status':t['status'],'generation_count':t.get('generation_count',0)});continue
        outcome={k:t.get(k) for k in ('status','generation_count','batch','questions','explanation')}
        outcome.update(outputs=t.get('result',{}).get('run',{}).get('outputs'),source_matches_patch=False,original_ports_unchanged=False,
                       published=t['status']=='applied',module_keys_unchanged=entry['module_keys_before']==module_keys(),case_results=[])
        with connection() as con:outcome['published']=bool(con.execute('SELECT 1 FROM workflows WHERE id=?',(t['workflow_id'],)).fetchone())
        if t['status']=='applied':
            candidate=t['candidate'];base=t['packet']['base_module'];patch=t.get('candidate_patch',{})
            outcome['source_matches_patch']=candidate['source']==patch.get('source')
            outcome['original_ports_unchanged']=candidate['outputs']==base['outputs'] and all(candidate['inputs'].get(k)==v for k,v in base['inputs'].items())
            with connection() as con:change=json.loads(con.execute('SELECT document FROM change_tasks WHERE id=?',(t['change_task_id'],)).fetchone()[0])
            outcome['case_results']=change.get('case_results',[])
        result={'case':case['id'],'task_id':t['id'],**grade(case,outcome)}
        result['seconds']=(datetime.fromisoformat(t['updated_at'])-datetime.fromisoformat(t['created_at'])).total_seconds()
        result['platform_diagnostics']=t.get('last_report',{}).get('diagnostics',[])
        results.append(result)
    complete=all('passed' in r for r in results)
    aggregate={}
    if complete:
        clear=[r for r in results if r['case']!='C']
        aggregate={'clear_task_first_generation_rate':sum(r['first_generation_passed'] for r in clear)/len(clear),
                   'clear_task_completion_rate':sum(r['passed'] for r in clear)/len(clear),
                   'uncertainty_handled_correctly':results[2]['passed'],
                   'overall_first_correct_response_rate':sum(r['first_generation_passed'] for r in results)/len(results),
                   'sample_size':3,'limitation':'One frozen development round; not a statistically stable success-rate estimate.'}
    output={'round_id':manifest['round_id'],'runtime_unchanged':True,'complete':complete,'cases':results,'aggregate':aggregate}
    write(folder/'report.json',output);write(ROOT/'docs/dsh-fixed-evaluation.json',output);print(json.dumps(output,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['freeze','start-next','report']);args=parser.parse_args()
    {'freeze':freeze,'start-next':start_next,'report':report}[args.action]()
