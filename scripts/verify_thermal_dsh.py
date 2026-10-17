"""Read-only verification of the actual platform/DSH task, never edits candidates."""
import json
from pathlib import Path
import sqlite3
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--task-file',default='data/thermal-task-id.txt');parser.add_argument('--output',default='docs/thermal-dsh-verification.json');args=parser.parse_args()
    key=(ROOT/args.task_file).read_text().strip()
    con=sqlite3.connect(ROOT/'data/workbench.sqlite')
    task=json.loads(con.execute('SELECT document FROM automation_tasks WHERE id=?',(key,)).fetchone()[0])
    if task['status']!='applied':
        print(json.dumps({'task_id':key,'status':task['status'],'message':task.get('message'),'last_report':{k:task.get('last_report',{}).get(k) for k in ('stage','diagnostics')}},ensure_ascii=False));return
    change=json.loads(con.execute('SELECT document FROM change_tasks WHERE id=?',(task['change_task_id'],)).fetchone()[0])
    packet=json.loads(con.execute('SELECT document FROM contexts WHERE id=?',(change['context_id'],)).fetchone()[0])
    before=packet['workflow'];after=task['result']['workflow']
    from pe_workflow.models import Workflow
    from pe_workflow.changes import locate
    old=Workflow.model_validate(before);new=Workflow.model_validate(after)
    _,old_node=locate(old,task['node_path']);_,new_node=locate(new,task['node_path'])
    new_version=new_node.module_version
    assert new_version!=old_node.module_version
    new_node.module_version=old_node.module_version;new_node.inputs.pop('thermal')
    assert new==old,'Unexpected unrelated workflow changes'
    candidate=json.loads(con.execute('SELECT document FROM modules WHERE id=? AND version=?',(old_node.module_id,new_version)).fetchone()[0])
    assert candidate['source']==task['candidate']['source']
    if task['request'].get('worker_protocol')=='implementation_patch_v1':assert candidate['source']==task['candidate_patch']['source']
    assert candidate['implementation']=='python_function'
    assert task['request']['source_policy']=='modify'
    for old_module in packet['registry']:
        current=json.loads(con.execute('SELECT document FROM modules WHERE id=? AND version=?',(old_module['id'],old_module['version'])).fetchone()[0])
        assert old_module==current,'An existing module was overwritten'
    reference=json.loads((ROOT/'docs/dbsrc-thermal-reference.json').read_text(encoding='utf-8'))
    cases=[]
    for actual,expected in zip(change['case_results'],reference['cases']):
        assert actual['passed']
        a=actual['outputs']['result'];e=expected['expected']['result']
        matrix_error=float(np.max(abs(np.asarray(a['efficiency_matrix'])-e['efficiency_matrix'])))
        detail_error=float(np.max(abs(np.asarray(a['detail_matrix'])-e['detail_matrix'])))
        assert matrix_error<1e-9 and detail_error<1e-9 and abs(a['reward']-e['reward'])<1e-9
        cases.append({'temperature':expected['temperature'],'reward':a['reward'],'efficiency':a['efficiency'],'matrix_max_abs_error':matrix_error,'detail_max_abs_error':detail_error})
    relation=json.loads(con.execute('SELECT document FROM module_relations WHERE id=? AND version=?',(old_node.module_id,new_version)).fetchone()[0])
    assert relation['parent_version']==old_node.module_version
    rows=con.execute("SELECT id,json_extract(document,'$.workflow_name') FROM runs WHERE json_extract(document,'$.change_task_id')=? AND json_extract(document,'$.replayed_from') IS NULL",(change['id'],)).fetchall()
    assert len(rows)==4
    record={'task_id':key,'status':'applied','dsh_calls':task.get('generation_count'),'repairs':task['repairs_used'],'module_id':old_node.module_id,'parent_version':old_node.module_version,'derived_version':new_version,'workflow_revision':task['result']['revision'],'acceptance_label':change['label'],'candidate_source_equals_dsh_submission':True,'old_modules_unchanged':True,'only_selected_node_changed':True,'runs':[{'id':r[0],'name':r[1]} for r in rows],'cases':cases}
    (ROOT/args.output).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__':main()
