"""Read-only recovery previews and explicitly confirmed, atomic queue revisions."""
import copy
import json
import uuid
from .storage import canonical, digest, now, Conflict
from .models import DesignPlan, CompletionRecovery, DeclarationRecovery
from .engine import WorkflowError, fail
from .repair_routing import route_failure, budget_snapshot


def preview(service, task):
    from .tool_completion import declaration_context
    step_id=(task.get('formula_request') or {}).get('step_id')
    step=next((s for s in task.get('plan',{}).get('steps',[]) if s['id']==step_id),None)
    missing=declaration_context(DesignPlan.model_validate(task['plan']).steps[
        next(i for i,s in enumerate(task['plan']['steps']) if s['id']==step_id)],task['id'],task['revision'])['missing'] if step else []
    fields=[]
    for m in missing:
        path=m['field'].split('.')
        if len(path)==3 and path[0] in ('inputs','outputs'):
            p=step[path[0]][path[1]]['fields'][path[2]]
            description=p.get('description','').strip()
            fields.append({'path':m['field'],'old':p.get('quantity',''),'proposed':description,
                           'source':f"/plan/steps/{step_id}/{path[0]}/{path[1]}/fields/{path[2]}/description" if description else None,
                           'message':m['message']})
    report=task.get('last_report') or {}
    # Compatibility for the previous internal constructor failure. Require a
    # queue draft and zero calls; never reinterpret an agent's schema failure.
    legacy_preparation=(task.get('completion_draft') and task.get('calls',0)==0
                        and report.get('code')=='DESIGN_VALIDATION'
                        and report.get('message','').startswith('1 validation error for FormulaRequest\ndeclarations\n')
                        and 'string_too_long' in report.get('message',''))
    if legacy_preparation:
        report={**report,'code':'COMPLETION_PREPARATION_FAILED','path':'/completion_draft/declarations'}
    routing=route_failure(stored_error(report),report) if report.get('code') else None
    remaining=max(0,task.get('call_limit',4)-task.get('calls',0))
    eligible=task['status'] in ('blocked','needs_clarification','paused')
    action='declarations' if eligible and task.get('completion_missing_info') and missing else 'candidate_repair' if eligible and routing and routing['automatic'] else 'keep'
    if eligible and report.get('code')=='INPUT_USAGE_REVIEW_REQUIRED' and (task.get('previous_candidate') or {}).get('module'):
        action='revalidate_candidate'
    if eligible and not missing and report.get('code')=='COMPLETION_PREPARATION_FAILED' and task.get('calls',0)==0:
        from .tool_completion import preparation_request
        try:
            preparation_request(task)
            action='preparation_retry'
        except (ValueError, KeyError, StopIteration):
            action='keep'
    if action in ('candidate_repair','preparation_retry') and (not remaining or task.get('repeat_count',0)>=2):action='stopped'
    result={'task_id':task['id'],'revision':task['revision'],'step_id':step_id,'action':action,
            'fields':fields,'missing':missing,'remaining_calls':remaining,'routing':routing,
            'calls':task.get('calls',0),'report':report,'status':task['status']}
    result['preview_hash']=digest(result)
    return result


def stored_error(report):
    # Reconstruct only the stored typed diagnostic, never arbitrary agent explanations.
    try:fail(report['code'],report.get('message',''),report.get('path','/'))
    except WorkflowError as error:return error


class Recovery:
    def __init__(self,completion):self.completion=completion;self.service=completion.service

    def task_preview(self,key):return preview(self.service,self.service.get(key))

    def queue_preview(self,key):
        q=self.completion.get(key)
        items=[preview(self.service,self.service.get(i['task_id']))|{'title':i['title']} for i in q['items']]
        result={'revision':q['revision'],'items':items,'queue_id':key}
        result['preview_hash']=digest(result)
        return result

    def recover_queue(self,key,req):
        from .designs import configuration
        q=self.completion.get(key)
        if q.get('archived') or self.service.get(q['source_id']).get('archived'):raise Conflict('队列已归档，请先恢复原任务')
        for receipt in q.get('recovery_receipts',[]):
            if receipt['revision']==req.revision and receipt['preview_hash']==req.preview_hash:return self.completion.public(q)
        view=self.queue_preview(key)
        if view['revision']!=req.revision or view['preview_hash']!=req.preview_hash:raise Conflict('恢复清单已变化，请刷新并重新核对')
        if any(i['status'] in ('dispatch_ready','dispatching','awaiting_worker','delivery_unknown') for i in view['items']):raise Conflict('仍有执行中或送达不明的调用，请先核对，不会重复发送')
        with self.service.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            live=json.loads(con.execute('SELECT document FROM tool_completion_queues WHERE id=?',(key,)).fetchone()[0])
            if live['revision']!=req.revision:raise Conflict('队列已变化，请刷新')
            for item in view['items']:
                task=json.loads(con.execute('SELECT document FROM workflow_designs WHERE id=?',(item['task_id'],)).fetchone()[0])
                if task['revision']!=item['revision']:raise Conflict('步骤已变化，请重新核对恢复清单')
                if item['action'] not in ('candidate_repair','preparation_retry','revalidate_candidate'):continue
                if task.get('configuration'):
                    task.setdefault('configuration_history',[]).append({'at':now(),'configuration':copy.deepcopy(task['configuration']),'reason':'user_confirmed_recovery'})
                if item['action']=='revalidate_candidate':
                    task.update(status='paused',policy_recheck_pending=True,message='已确认按当前检查规则复检原候选；不改接口、不调用模型、不增加额度')
                elif item['action']=='preparation_retry':
                    from .tool_completion import preparation_request
                    request=preparation_request(task)
                    task['completion_draft']['declarations']=request.declarations
                    task.update(status='completion_pending',questions={},failure_routing=item['routing'],configuration=configuration(task['call_limit']),
                                message='平台请求准备已修复；按原额度重新检查，未追加模型调用额度')
                else:
                    task.update(status='dispatch_ready',questions={},failure_routing=item['routing'],configuration=configuration(task['call_limit']),
                                message='已确认恢复；使用剩余额度修正候选，保留原公式、接口与验收依据')
                self.persist(con,task,'user_confirmed_recovery')
            live.setdefault('recovery_receipts',[]).append({'revision':req.revision,'preview_hash':req.preview_hash,'at':now()})
            live['status']='generating';self.completion.write(con,live,req.revision)
        return self.completion.public(live)

    async def recheck_pending(self,q):
        from .models import Module,DesignWorkerResult
        for item in q['items']:
            task=self.service.get(item['task_id'])
            if not task.get('policy_recheck_pending') or task['status']!='paused':continue
            with self.service.receipts.processing(task['id'],'platform_policy_recheck'):
                task=self.service.get(task['id'])
                if not task.get('policy_recheck_pending') or task['status']!='paused':continue
                task=self.service.save(task,'policy_recheck_started')
                await self.service.receive(task,DesignWorkerResult(call_id='platform_policy_recheck',token='',kind='module',module=Module.model_validate(task['previous_candidate']['module'])),revalidate=True)
                done=self.service.get(task['id']);done.pop('policy_recheck_pending',None)
                self.service.save(done,'policy_recheck_completed')
        return self.completion.public(self.completion.get(q['id']))

    @staticmethod
    def persist(con,task,event):
        old=task['revision'];task['revision']=old+1;task['updated_at']=now()
        task.setdefault('events',[]).append({'event':event,'actor':'user','at':now(),'revision':task['revision'],'status':task['status']})
        result=con.execute('UPDATE workflow_designs SET revision=?,document=? WHERE id=? AND revision=?',(task['revision'],canonical(task),task['id'],old))
        if result.rowcount!=1:raise Conflict('步骤已变化，请刷新')

    def declarations(self,key,req):
        old=self.service.get(key)
        if old.get('archived'):raise Conflict('任务已归档，请先恢复')
        fingerprint=digest(req.model_dump())
        with self.service.connect() as con:
            row=con.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.declaration_recovery_key')=?",(key+':'+fingerprint,)).fetchone()
        if row:return self.service.public(json.loads(row[0]))
        view=preview(self.service,old)
        if view['revision']!=req.revision or view['preview_hash']!=req.preview_hash:raise Conflict('声明草案已变化，请刷新')
        if view['action']!='declarations' or not old.get('queue_id'):raise Conflict('当前没有可确认的队列声明草案')
        allowed={f['path']:f for f in view['fields']}
        if not req.quantities or set(req.quantities)-allowed.keys():fail('DECLARATION_SELECTION','只能补充列出的缺失语义字段','/quantities')
        if any(not v.strip() or len(v)>1000 for v in req.quantities.values()):fail('DECLARATION_REQUIRED','请填写明确含义','/quantities')
        child=copy.deepcopy(old);child.update(id=uuid.uuid4().hex,parent_id=key,revision=1,created_at=now(),updated_at=now(),
            declaration_recovery_key=key+':'+fingerprint,stage_budgets=budget_snapshot(old))
        child.pop('token',None);child.pop('call_id',None)
        step=next(s for s in child['plan']['steps'] if s['id']==view['step_id'])
        changes=[]
        for path,value in req.quantities.items():
            side,bag,field=path.split('.');step[side][bag]['fields'][field]['quantity']=value.strip()
            changes.append({'path':path,'old':allowed[path]['old'],'new':value.strip(),'source':allowed[path]['source'],'confirmed_by':'user'})
        checked=DesignPlan.model_validate(child['plan']);self.service.audit_plan(checked)
        report=self.service.plan_report(child,checked)
        index=next(i for i,s in enumerate(checked.steps) if s.id==view['step_id'])
        # Saving a partially completed declaration is allowed, but it stays
        # blocked. Do not let the earlier lint remove this recovery path.
        if any(i['code']!='FIELD_SEMANTIC_REQUIRED' or not i['path'].startswith(f'/steps/{index}/') for i in report['issues']):
            fail('PLAN_PREFLIGHT','补充后方案检查未通过，请核对声明','/plan')
        from .tool_completion import declaration_context, formula_declarations
        current=next(s for s in checked.steps if s.id==view['step_id'])
        ctx=declaration_context(current,child['id'],1)
        child['adopted_context']=ctx
        child['completion_draft']['declarations']=formula_declarations(current)
        child['formula_request']=copy.deepcopy(child['completion_draft'])
        child['declaration_changes']=changes
        missing=ctx['missing']
        child.update(completion_missing_info=bool(missing),questions={str(i):m['field']+'：'+m['message'] for i,m in enumerate(missing)},
                     status='blocked' if missing else 'paused' if not view['remaining_calls'] or old.get('repeat_count',0)>=2 else 'completion_pending',
                     message='已派生并保存声明；仍有待补充项' if missing else '声明已确认；按原剩余额度继续，同类错误停止记录保留',failure_routing=None)
        child.setdefault('events',[]).append({'event':'user_confirmed_declaration_revision','actor':'user','at':now(),'changes':changes,'revision':1})
        q=self.completion.get(old['queue_id'])
        with self.service.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            live=json.loads(con.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()[0])
            liveq=json.loads(con.execute('SELECT document FROM tool_completion_queues WHERE id=?',(q['id'],)).fetchone()[0])
            if live['revision']!=req.revision or liveq['revision']!=q['revision']:raise Conflict('任务或队列已变化，请刷新')
            item=next((i for i in liveq['items'] if i['task_id']==key),None)
            if not item:raise Conflict('此步骤已派生，请打开最新修订')
            item.setdefault('previous_task_ids',[]).append(key);item['task_id']=child['id']
            con.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child['id'],1,canonical(child)))
            liveq['status']='generating';self.completion.write(con,liveq,q['revision'])
        return self.service.public(child)


def install(app,completion):
    recovery=Recovery(completion)
    @app.get('/api/tool-completion-queues/{key}/recovery')
    def queue_preview(key:str):return recovery.queue_preview(key)
    @app.post('/api/tool-completion-queues/{key}/recovery')
    async def recover_queue(key:str,req:CompletionRecovery):
        recovery.recover_queue(key,req)
        return await recovery.recheck_pending(completion.get(key))
    @app.get('/api/workflow-designs/{key}/declaration-recovery')
    def task_preview(key:str):return recovery.task_preview(key)
    @app.post('/api/workflow-designs/{key}/declaration-recovery')
    def declarations(key:str,req:DeclarationRecovery):return recovery.declarations(key,req)
    return recovery
