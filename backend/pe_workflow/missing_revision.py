"""User-confirmed encoding of an already required missing/NaN output."""
import copy,json,uuid
from .models import DesignPlan,MissingOutputRevision
from .storage import digest,canonical,now,Conflict
from .engine import check_port
from .repair_routing import budget_snapshot


def revise(service,key,req):
    receipt=key+':'+digest(req.model_dump())
    with service.connect() as c:
        prior=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.missing_output_receipt')=?",(receipt,)).fetchone()
    if prior:return service.public(json.loads(prior[0]))
    old=service.get(key);service.check_revision(old,req.revision)
    if old['phase']!='formula' or old['status'] not in ('paused','blocked') or not old.get('queue_id'):raise Conflict('请使用已暂停、未注册的队列工具候选')
    q=service.completion.get(old['queue_id'])
    if service.completion.public(q)['running'] or q.get('archived'):raise Conflict('队列正在运行或已归档')
    base=service.get(q['result_design_id']) if q.get('result_design_id') else q['snapshot']
    plan=DesignPlan.model_validate(base['plan']);step=next(s for s in plan.steps if s.id==old['formula_request']['step_id'])
    if step.module_ref or req.port not in step.outputs:raise Conflict('不能修改已注册工具或未知输出')
    bag=step.outputs[req.port]
    if len(set(req.fields))!=len(req.fields) or set(req.fields)-bag.fields.keys() or req.mask in req.fields:raise Conflict('缺失字段选择无效')
    before=bag.model_dump()
    for field in req.fields:
        p=bag.fields[field]
        if p.missing_when and p.missing_when!=req.mask:raise Conflict('已有不同的缺失值策略')
        p.missing_when=req.mask
    check_port(bag,'/plan/outputs/'+req.port)
    service.audit_plan(plan)
    if not service.plan_report(base,plan)['valid']:raise Conflict('新增缺失值声明与现有接线不兼容，未保存')
    plan_child={'id':uuid.uuid4().hex,'parent_id':base['id'],'request':base['request'],'plan':plan.model_dump(),
                'phase':'plan','status':'awaiting_confirmation','revision':1,'created_at':now(),'updated_at':now(),
                'stage_budgets':budget_snapshot(base),'total_calls':base.get('total_calls',0),
                'missing_output_audit':{'before':before,'after':bag.model_dump(),'evidence':req.evidence,'encoding':'NaN represented by JSON null, governed by explicit 0/1 validity column'}}
    child=copy.deepcopy(old);child.update(id=uuid.uuid4().hex,parent_id=key,revision=1,created_at=now(),updated_at=now(),
        plan=plan.model_dump(),missing_output_receipt=receipt,missing_output_audit=plan_child['missing_output_audit'],status='paused',
        message='缺失推荐点保持 NaN 语义，JSON 使用 null；尚未调用模型，使用原剩余额度修正代码。')
    for k in ('token','call_id','callback_hash'):child.pop(k,None)
    child['previous_candidate']['module']['outputs']=copy.deepcopy(step.model_dump()['outputs'])
    from .tool_completion import formula_declarations
    child['formula_request']['declarations']=formula_declarations(step)
    if child.get('completion_draft'):child['completion_draft']['declarations']=child['formula_request']['declarations']
    child.setdefault('events',[]).append({'event':'user_confirmed_missing_output_encoding','actor':'user','at':now(),'revision':1})
    with service.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        live=json.loads(c.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()[0])
        liveq=json.loads(c.execute('SELECT document FROM tool_completion_queues WHERE id=?',(q['id'],)).fetchone()[0])
        livebase=json.loads(c.execute('SELECT document FROM workflow_designs WHERE id=?',(base['id'],)).fetchone()[0])
        service.check_revision(live,req.revision)
        if liveq['revision']!=q['revision'] or livebase['revision']!=base['revision']:raise Conflict('队列或方案已变化')
        entry=next(i for i in liveq['items'] if i['task_id']==key)
        entry.setdefault('previous_task_ids',[]).append(key);entry['task_id']=child['id']
        c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(plan_child['id'],1,canonical(plan_child)))
        c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child['id'],1,canonical(child)))
        liveq['result_design_id']=plan_child['id'];service.completion.write(c,liveq,q['revision'])
    return service.public(child)


def install(app,service):
    from .models import FormulaRecovery
    @app.post('/api/workflow-designs/{key}/formula-recovery')
    def recovery(key:str,req:FormulaRecovery):return recover_formula(service,key,req)
    @app.post('/api/workflow-designs/{key}/missing-output-revision')
    def confirm(key:str,req:MissingOutputRevision):return revise(service,key,req)


def recover_formula(service,key,req):
    """User-approved one-call recovery; preserves the rejected candidate verbatim."""
    from .designs import check_examples,configuration
    receipt=key+':'+digest(req.model_dump())
    with service.connect() as c:
        prior=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.formula_recovery_receipt')=?",(receipt,)).fetchone()
    if prior:return service.public(json.loads(prior[0]))
    old=service.get(key);service.check_revision(old,req.revision)
    if old['phase']!='formula' or old['status'] not in ('paused','blocked') or (old.get('last_report') or {}).get('code')!='RESULT_ENVELOPE':raise Conflict('只允许恢复已暂停的提交封装错误')
    q=service.completion.get(old['queue_id'])
    if service.completion.public(q)['running'] or q.get('archived'):raise Conflict('队列正在运行或已归档')
    step=next(s for s in old['plan']['steps'] if s['id']==old['formula_request']['step_id'])
    if step.get('module_ref'):raise Conflict('工具已注册，不能恢复生成')
    examples=[e.model_dump() for e in req.examples]
    check_examples(examples,step['inputs'],step['outputs'])
    child=copy.deepcopy(old);child.update(id=uuid.uuid4().hex,parent_id=key,revision=1,created_at=now(),updated_at=now(),status='dispatch_ready',questions={},repeat_count=0,call_limit=old.get('calls',0)+1,configuration=configuration(1),formula_recovery_receipt=receipt,message='用户批准仅追加一次，平台使用独立参考验收；候选代码由 DSH 修正')
    for k in ('token','call_id','callback_hash','worker_result_closed'):child.pop(k,None)
    child['original_formula_examples']=copy.deepcopy(old['formula_request'].get('examples',[]))
    child['formula_request']['examples']=examples
    child.setdefault('budget_history',[]).append({'at':now(),'used':old.get('calls',0),'limit':old.get('call_limit',4),'approved_additional_calls':1,'evidence':req.evidence})
    child.setdefault('answers',{})['targeted_repair']='Submit the exact required envelope as an object, with source at module root. Preserve frozen formula, contract, guards and missing-output policy. Fix only documented implementation errors and declare used transforms. Independent reference outputs remain platform-only. User recovery evidence: '+req.evidence
    child['failure_routing']={'route':'candidate_repair','automatic':True,'reason':'用户明确批准一次定向封装修复并提供独立验收依据'}
    child.setdefault('stage_budgets',{})['formula:'+step['id']]={'used':old.get('calls',0),'limit':child['call_limit']}
    child.setdefault('events',[]).append({'event':'user_approved_one_formula_recovery','actor':'user','at':now(),'revision':1})
    with service.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        live=json.loads(c.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()[0]);service.check_revision(live,req.revision)
        liveq=json.loads(c.execute('SELECT document FROM tool_completion_queues WHERE id=?',(q['id'],)).fetchone()[0])
        if liveq['revision']!=q['revision']:raise Conflict('队列已变化')
        entry=next(i for i in liveq['items'] if i['task_id']==key)
        entry.setdefault('previous_task_ids',[]).append(key);entry['task_id']=child['id']
        liveq['status']='generating'
        c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child['id'],1,canonical(child)))
        service.completion.write(c,liveq,q['revision'])
    return service.public(child)
