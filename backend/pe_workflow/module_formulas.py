"""Read-only formula provenance; never changes immutable module documents."""
import json
from .models import Module
from .storage import digest


def records(service, workflow_id=''):
    modules=service.modules();items={};nodes={}
    with service.connect() as con:
        rows=con.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.status')='formula_registered' OR json_extract(document,'$.workflow.id')=? ORDER BY rowid",(workflow_id,)).fetchall()
    for row in rows:
        task=json.loads(row[0]);plan=task.get('plan') or {}
        if task.get('status')=='formula_registered' and task.get('candidate'):
            try:
                candidate=Module.model_validate(task['candidate']);ref=f'{candidate.id}@{candidate.version}'
                if ref not in modules or candidate.model_dump()!=modules[ref].model_dump():continue
                step=next(s for s in plan.get('steps',[]) if s['id']==task.get('formula_request',{}).get('step_id'))
                if step.get('formula_latex'):
                    items[ref]={'module_ref':ref,'formula_latex':step['formula_latex'],'source_task':task['id'],
                                'step_id':step['id'],'source':'registered_plan','contract_hash':digest(candidate.model_dump())}
            except (ValueError,KeyError,StopIteration):continue
        if workflow_id and task.get('workflow',{}).get('id')==workflow_id:
            saved={n['id']:f"{n.get('module_id')}@{n.get('module_version')}" for n in task['workflow']['nodes'] if n['kind']=='compute'}
            for step in plan.get('steps',[]):
                ref=step.get('module_ref')
                if ref and saved.get(step['id'])==ref and step.get('formula_latex'):
                    nodes[step['id']]={'module_ref':ref,'formula_latex':step['formula_latex'],'source_task':task['id'],
                                       'step_id':step['id'],'source':'workflow_plan'}
    return {'workflow_id':workflow_id,'items':items,'nodes':nodes,'notice':'公式来自已保存的方案声明；代码是实际实现。展示公式不等于验证公式与代码等价。'}


def install(app,service):
    @app.get('/api/module-formulas')
    def read(workflow_id:str=''):return records(service,workflow_id)
