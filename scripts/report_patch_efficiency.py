"""Summarize actual callbacks and persisted task timings; never edits candidates."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    con=sqlite3.connect(ROOT/'data/workbench.sqlite')
    def read(file):
        key=(ROOT/file).read_text(encoding='utf-8').strip()
        task=json.loads(con.execute('SELECT document FROM automation_tasks WHERE id=?',(key,)).fetchone()[0])
        events=[json.loads(r[0]) for r in con.execute('SELECT document FROM automation_events WHERE task_id=? ORDER BY seq',(key,))]
        return task,events
    old,old_events=read('data/thermal-task-id.txt')
    new,events=read('data/patch-efficiency-task-id.txt')
    assert new['status']=='applied' and new['generation_count']<=4
    assert new['candidate_patch']['source']==new['candidate']['source']
    size=lambda value:len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    before=[e['payload']['module'] for e in old_events if e['event']=='worker_result' and e['payload'].get('module')][-1]
    timings=[]
    for dispatch in (e for e in events if e['event']=='dispatch_intent'):
        result=next(e for e in events if e['event']=='worker_result' and e['call_id']==dispatch['call_id'])
        seconds=(datetime.fromisoformat(result['at'])-datetime.fromisoformat(dispatch['at'])).total_seconds()
        timings.append({'call_id':dispatch['call_id'],'seconds_to_callback':round(seconds,2)})
    original_revision=con.execute("SELECT MAX(revision) FROM workflows WHERE id='dbsrc_eps_single_step'").fetchone()[0]
    assert original_revision==2
    report={
        'task_id':new['id'],'status':new['status'],'worker_protocol':new['request']['worker_protocol'],
        'previous_calls':old['generation_count'],'current_calls':new['generation_count'],
        'current_repairs':new['repairs_used'],'acceptance_limit_met':True,
        'legacy_last_module_submission_bytes':size(before),'accepted_patch_submission_bytes':size(new['candidate_patch']),
        'submission_reduction_percent':round(100*(1-size(new['candidate_patch'])/size(before)),2),
        'context':json.loads((ROOT/'docs/patch-context-comparison.json').read_text(encoding='utf-8')),
        'timings':timings,'end_to_end_seconds':round((datetime.fromisoformat(new['updated_at'])-datetime.fromisoformat(new['created_at'])).total_seconds(),2),
        'original_workflow_revision_unchanged':2,'parent_module_version':1,'derived_module_version':new['candidate']['version'],
        'source_equals_dsh_patch':True,'scope':'One development run, including tool-envelope migration fixes; not a repeated success-rate or controlled latency benchmark.'}
    (ROOT/'docs/patch-efficiency-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
