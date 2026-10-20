"""Review actual code/check dependencies without changing candidate source."""
import copy
import json
import uuid
from .models import Module, DesignPlan, DesignWorkerResult, CompletionRecovery
from .dependency_audit import audit_dependencies
from .plan_audit import validate_checks,same_checks
from .storage import digest, canonical, now, Conflict
from .repair_routing import budget_snapshot
from .engine import WorkflowError

def contract(value):
    if hasattr(value,'model_dump'):value=value.model_dump()
    if isinstance(value,dict):
        descriptive={'description','chinese_name'} if isinstance(value.get('type'),str) and ('fields' in value or 'quantity' in value) else set()
        return {k:contract(v) for k,v in value.items() if k not in descriptive}
    if isinstance(value,list):return [contract(v) for v in value]
    return value

def proposal(task):
    raw=(task.get('previous_candidate') or {}).get('module')
    if not raw:raise ValueError('没有保留的工具候选')
    module=Module.model_validate(raw)
    step=next(s for s in DesignPlan.model_validate(task['plan']).steps if s.id==task['formula_request']['step_id'])
    if step.module_ref:raise ValueError('已注册工具必须通过版本派生入口修改')
    # Only input-field omission/required changes may be reconciled here.
    if set(module.inputs)!=set(step.inputs):raise ValueError('候选改变了输入字典；须单独核对')
    if contract(module.outputs)!=contract(step.outputs):raise ValueError('候选输出声明与方案不同；须单独核对')
    if module.input_checks and not same_checks(module.input_checks,step.input_checks):raise ValueError('候选改变保护条件；须单独核对')
    for bag,p in module.inputs.items():
        old=step.inputs[bag]
        if set(p.fields)-set(old.fields):raise ValueError('候选新增输入字段：'+bag)
        if contract(p.model_dump(exclude={'fields'}))!=contract(old.model_dump(exclude={'fields'})):raise ValueError('候选改变字典声明：'+bag)
        for key,f in p.fields.items():
            if f.model_dump(exclude={'required','description','chinese_name'})!=old.fields[key].model_dump(exclude={'required','description','chinese_name'}):
                raise ValueError('候选改变字段单位、语义或类型：'+bag+'.'+key)
    module.inputs=copy.deepcopy(step.inputs)
    module.outputs=copy.deepcopy(step.outputs)
    module.input_checks=copy.deepcopy(step.input_checks)
    audit=audit_dependencies(module)
    if audit['status']=='uncertain' or audit['undeclared_fields'] or audit['unsafe_optional_fields']:
        raise ValueError('无法确定安全依赖范围：'+canonical(audit))
    consumed=set(audit['read_fields']);checked=set(audit['check_fields'])
    changes=[];roles=[]
    for bag,p in module.inputs.items():
        for key,f in list(p.fields.items()):
            address=bag+'.'+key
            role='calculation_and_check' if address in consumed&checked else 'calculation' if address in consumed else 'check' if address in checked else 'carried'
            roles.append({'field':address,'role':role})
            if role=='carried':
                del p.fields[key]
                changes.append({'field':address,'before':f.model_dump(),'after':None,'basis':'not_read_by_candidate_or_frozen_checks','effect':'仅移出本工具的输入契约；工作流和上游字段保留'})
    # A flat_dict with no remaining fields is not a valid input bag. Remove the
    # whole bag from both the tool and this step when every field was carried.
    for bag in list(module.inputs):
        if not module.inputs[bag].fields:
            del module.inputs[bag]
    new=step.model_copy(deep=True);new.inputs=copy.deepcopy(module.inputs)
    # Keep original sources, but remove mappings for fields no longer projected.
    # A source whose entire input bag was carried has no target left, so remove
    # that source binding as well.
    for bag in list(new.input_sources):
        if bag not in new.inputs:
            del new.input_sources[bag]
            continue
        new.input_sources[bag].field_map={k:v for k,v in new.input_sources[bag].field_map.items() if k in new.inputs[bag].fields}
    validate_checks(new.input_checks,new.inputs)
    if audit_dependencies(module)['status']!='passed':raise ValueError('整理后仍有字段依赖冲突')
    from .engine import fill_inputs
    for ex in task['formula_request'].get('examples',[]):fill_inputs(ex['inputs'],step.inputs,'/examples/inputs')
    return new,module,{'changes':changes,'roles':roles,'source_hash':digest({'source':module.source,'expressions':module.expressions,'adapter':module.adapter.model_dump() if module.adapter else None}),
                       'formula':step.formula_latex,'code':module.source or canonical(module.expressions),'checks':step.input_checks,'audit':audit,'example_count':len(task['formula_request'].get('examples',[])),
                       'notice':'字段访问不是公式正确性证明；请对照公式核对。保留代码、公式、保护条件与上游数据。'}


class UsageRecovery:
    def __init__(self,completion):self.completion=completion;self.service=completion.service

    async def recheck(self,key,revision_id):
        q=self.completion.get(key)
        for item in q['items']:
            task=self.service.get(item['task_id'])
            if task.get('usage_revision_id')!=revision_id or not task.get('usage_recheck_pending'):continue
            if task['status']!='paused':continue
            candidate=Module.model_validate(task['previous_candidate']['module'])
            task.pop('usage_recheck_pending',None)
            try:
                await self.service.receive(task,DesignWorkerResult(call_id='platform_usage_recheck',token='',kind='module',module=candidate),revalidate=True)
            except Conflict:pass  # another confirmed request already rechecked it
        return self.completion.public(self.completion.get(key))

    def preview(self,key):
        q=self.completion.get(key);base=self.service.get(q['result_design_id']) if q.get('result_design_id') else q['snapshot']
        items=[];plan=copy.deepcopy(base['plan'])
        for item in q['items']:
            t=self.service.get(item['task_id'])
            if item.get('registered') or t['status'] not in ('paused','blocked','needs_clarification'):continue
            row={'task_id':t['id'],'revision':t['revision'],'step_id':item['step_id'],'title':item['title'],
                 'used':t.get('calls',0),'remaining':max(0,t.get('call_limit',4)-t.get('calls',0)),'eligible':False}
            try:
                step,module,review=proposal(t)
                if not review['changes']:raise ValueError('无待修订的携带字段；请查看原诊断')
                index=next(i for i,s in enumerate(plan['steps']) if s['id']==step.id)
                plan['steps'][index]=step.model_dump()
                row.update(eligible=True,review=review)
            except (ValueError,KeyError,StopIteration,WorkflowError) as e:row['reason']=str(e)
            items.append(row)
        try:
            validated=DesignPlan.model_validate(plan)
            self.service.audit_plan(validated)
            report=self.service.plan_report(base,validated)
            plan=validated.model_dump()
        except (ValueError,WorkflowError) as e:
            report={'valid':False,'issues':[{'code':'INPUT_USAGE_PLAN','path':'/plan','message':str(e)}]}
        result={'queue_id':key,'revision':q['revision'],'base_id':base['id'],'base_revision':base['revision'],
                'items':items,'plan_check':report,'model_calls':0,'requires_confirmation':True}
        result['preview_hash']=digest(result)
        return result,plan

    async def confirm(self,key,req):
        q=self.completion.get(key);receipt=key+':'+digest(req.model_dump())
        if q.get('archived') or self.service.get(q['source_id']).get('archived'):raise Conflict('队列已归档')
        with self.service.connect() as c:
            existing=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.usage_recovery_receipt')=?",(receipt,)).fetchone()
        if existing:return await self.recheck(key,json.loads(existing[0])['id'])
        if self.completion.public(q)['running']:raise Conflict('请先等待当前调用结束')
        view,plan=self.preview(key)
        if req.revision!=view['revision'] or req.preview_hash!=view['preview_hash']:raise Conflict('修订清单已变化，请重新核对')
        selected=[i for i in view['items'] if i['eligible']]
        if not selected or not view['plan_check']['valid']:raise Conflict('没有可确认的修订，或接线仍未通过')
        from .designs import configuration
        base=self.service.get(view['base_id']) if q.get('result_design_id') else q['snapshot']
        self.service.check_revision(base,view['base_revision'])
        child={'id':uuid.uuid4().hex,'parent_id':base['id'],'request':base['request'],'created_at':now(),'updated_at':now(),
               'revision':1,'phase':'plan','status':'awaiting_confirmation','plan':plan,'usage_recovery_receipt':receipt,
               'usage_review':view,'calls':base.get('calls',0),'call_limit':base.get('call_limit',4),
               'total_calls':base.get('total_calls',base.get('calls',0)),'stage_budgets':budget_snapshot(base),
               'events':[{'event':'user_confirmed_input_usage','at':now(),'actor':'user','revision':1}],
               'message':'工具输入依赖修订已确认；公式、保护条件和上游数据保留。原候选待复检，不自动注册。'}
        tasks=[]
        for item in selected:
            old=self.service.get(item['task_id']);_,m,review=proposal(old)
            t=copy.deepcopy(old);t.update(id=uuid.uuid4().hex,parent_id=old['id'],revision=1,created_at=now(),updated_at=now(),
                plan=copy.deepcopy(plan),status='paused',configuration=configuration(old.get('call_limit',4)),
                previous_candidate={'kind':'module','module':m.model_dump()},usage_review=review,usage_recheck_pending=True,usage_revision_id=child['id'],
                stage_budgets=budget_snapshot(old),questions={},failure_routing=None,last_report=None)
            for k in ('token','call_id','callback_hash','candidate','formula_validation'):t.pop(k,None)
            from .tool_completion import formula_declarations
            step=next(s for s in DesignPlan.model_validate(plan).steps if s.id==item['step_id'])
            t['formula_request']['declarations']=formula_declarations(step)
            t['usage_original_examples']=copy.deepcopy(old['formula_request'].get('examples',[]))
            for ex in t['formula_request'].get('examples',[]):
                ex['inputs']={bag:{k:v for k,v in values.items() if k in step.inputs[bag].fields}
                              for bag,values in ex['inputs'].items() if bag in step.inputs}
            if t.get('completion_draft'):t['completion_draft']['declarations']=t['formula_request']['declarations']
            t.setdefault('events',[]).append({'event':'user_confirmed_input_usage','actor':'user','at':now(),'revision':1})
            tasks.append((old,t))
        with self.service.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            liveq=json.loads(c.execute('SELECT document FROM tool_completion_queues WHERE id=?',(key,)).fetchone()[0])
            if liveq['revision']!=req.revision:raise Conflict('队列已更新')
            livebase=json.loads(c.execute('SELECT document FROM workflow_designs WHERE id=?',(base['id'],)).fetchone()[0])
            if livebase['revision']!=view['base_revision']:raise Conflict('方案已更新')
            for old,t in tasks:
                live=json.loads(c.execute('SELECT document FROM workflow_designs WHERE id=?',(old['id'],)).fetchone()[0])
                if live['revision']!=old['revision']:raise Conflict('候选已更新')
                entry=next(i for i in liveq['items'] if i['task_id']==old['id'])
                entry.setdefault('previous_task_ids',[]).append(old['id']);entry['task_id']=t['id']
                c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(t['id'],1,canonical(t)))
            c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child['id'],1,canonical(child)))
            liveq['result_design_id']=child['id'];liveq['status']='review';self.completion.write(c,liveq,req.revision)
        return await self.recheck(key,child['id'])


def install(app,completion):
    recovery=UsageRecovery(completion)
    @app.get('/api/tool-completion-queues/{key}/input-usage-preview')
    def preview(key:str):return recovery.preview(key)[0]
    @app.post('/api/tool-completion-queues/{key}/input-usage-confirm')
    async def confirm(key:str,req:CompletionRecovery):return await recovery.confirm(key,req)
    return recovery
