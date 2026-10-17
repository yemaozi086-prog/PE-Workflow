"""Read-only real assembly results; never supplies or edits agent plans."""
import json
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
records=[]
for name in ('baseline','variation'):
    file=ROOT/f'data/assembly-real-{name}.json'
    if not file.exists():continue
    saved=json.loads(file.read_text(encoding='utf-8'))
    if not saved.get('id'):
        records.append({'case':name,'status':'submission_uncertain'});continue
    with urllib.request.urlopen('http://127.0.0.1:8020/api/assembly-tasks/'+saved['id'],timeout=30) as r:task=json.load(r)
    result=task.get('result',{});run=result.get('run',{});outputs=run.get('outputs',{})
    durations=[];started=None
    for event in task['events']:
        if event['event']=='dispatch_intent':started=datetime.fromisoformat(event['at'])
        elif event['event']=='worker_result' and started is not None:
            durations.append(round((datetime.fromisoformat(event['at'])-started).total_seconds(),3));started=None
    item={'case':name,'task_id':task['id'],'status':task['status'],
          'generation_count':task['generation_count'],'repairs_used':task['repairs_used'],
          'batch':task['batch'],'calls_in_current_batch':task['batch_calls'],
          'call_limit_in_current_batch':task['batch_call_limit'],
          'call_durations_seconds':durations,
          'point':task['request']['inputs']['point'],
          'context_hash':task['context']['context_hash'],'prompt_hash':task['configuration']['prompt_hash'],
          'scene_hash':task['context']['scene_hash'],
          'workflow_id':result.get('workflow',{}).get('id'), 'run_id':run.get('id'),
          'validation':result.get('validation'),
          'candidate_count':len(outputs.get('loss',{}).get('cap1_loss',[])),
          'last_diagnostics':task.get('last_report',{}).get('diagnostics',[]),
          'failures':[e.get('report',{}) for e in task['events'] if e['event']=='validation_failed'],
          'first_generated_plan_passed':task['status']=='completed' and task['generation_count']==1,
          'current_configuration_first_plan_passed':task['status']=='completed' and task['batch_calls']==1,
          'user_modified_plan':False,
          'engineering_validation':'pending_research_group_review'}
    records.append(item)
report={'scope':'DBSRC EPS two fixed roles; not arbitrary topology planning',
        'cases':records,'all_completed':len(records)==2 and all(r['status']=='completed' for r in records)}
(ROOT/'docs/assembly-real-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
