"""Frozen, bounded concurrent tool generation and explicit, transactional partial registration."""
import copy
import json
import uuid
import threading
from pydantic import ValidationError
from .models import (DesignPlan, FormulaRequest, Module, ToolCompletionRequest,
                     ToolCompletionRegister, ToolCompletionConcurrency, DesignConfirm)
from .storage import canonical, digest, now, Conflict
from .engine import check_port, check_module, WorkflowError, fail
from .dependency_audit import audit_dependencies
from .repair_routing import budget_snapshot

ACTIVE = {'dispatch_ready', 'dispatching', 'awaiting_worker'}
IN_FLIGHT = {'dispatching', 'awaiting_worker', 'delivery_unknown'}
GLOBAL_CONCURRENCY = 10


def formula_declarations(step):
    """Bounded request summary; the frozen plan remains the full contract.

    Do not truncate large declarations or feed expanded Port defaults through
    a user-text field. A reference resolves through the existing paged step API.
    """
    full = {side: {k: p.model_dump() for k, p in getattr(step, side).items()}
            for side in ('inputs', 'outputs')}
    full.update(input_checks=step.input_checks, assumptions=step.assumptions)
    compact = {side: {k: p.model_dump(exclude_defaults=True) for k, p in getattr(step, side).items()}
               for side in ('inputs', 'outputs')}
    compact.update(input_checks=step.input_checks, assumptions=step.assumptions)
    text = canonical(compact)
    if len(text) <= 10000:
        return text
    return canonical({'declaration_source': {'section': 'step', 'step_id': step.id,
                                           'contract_hash': digest(full)},
                      'notice': '完整输入输出、保护条件和假设位于已确认步骤；使用 design_context section=step、step_id 及 next_offset 读取全部分页。不得截断或推断缺失字段。'})


def preparation_request(task):
    step = next(s for s in DesignPlan.model_validate(task['plan']).steps
                if s.id == task['completion_draft']['step_id'])
    draft = copy.deepcopy(task['completion_draft'])
    draft['declarations'] = formula_declarations(step)
    return FormulaRequest(revision=task['revision'], **draft)


def declaration_context(step, source_id, revision):
    missing = []
    if not step.formula_latex.strip():missing.append({'field':'formula','message':'缺少明确公式或原始函数'})
    if not step.evidence:missing.append({'field':'evidence','message':'缺少公式来源说明（不要求它冒充独立验证）'})
    for side in ('inputs','outputs'):
        ports=getattr(step,side)
        if not ports:missing.append({'field':side,'message':'缺少输入或输出声明，须派生方案补齐'})
        for name,port in ports.items():
            for field,value in port.fields.items():
                if not (value.quantity or value.semantic_id):
                    missing.append({'field':f'{side}.{name}.{field}','message':'字段缺少语义，须派生方案补齐'})
            try:check_port(port,f'/step/{side}/{name}')
            except WorkflowError as error:
                missing.append({'field':f'{side}.{name}','code':error.diagnostic.code,'message':error.diagnostic.message})
    declarations={'inputs':{k:p.model_dump() for k,p in step.inputs.items()},
                  'outputs':{k:p.model_dump() for k,p in step.outputs.items()},
                  'input_checks':step.input_checks,'assumptions':step.assumptions}
    return {'step_id':step.id,'title':step.title,'formula':step.formula_latex,
            'declarations':declarations,'evidence':step.evidence,'missing':missing,
            'source':{'task_id':source_id,'revision':revision,'status':'plan_requirements_to_confirm'},
            'verification':{'status':'not_provided','notice':'方案声明及来源不是独立验证结果；数值样例单独提供'}}


class Completion:
    def __init__(self,service):
        self.service=service
        self._tick_lock=threading.Lock()
        with service.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS tool_completion_queues(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL)')

    def get(self,key):
        with self.service.connect() as con:
            row=con.execute('SELECT document FROM tool_completion_queues WHERE id=?',(key,)).fetchone()
        if not row:fail('COMPLETION_NOT_FOUND','补齐任务不存在','/queue')
        return json.loads(row[0])

    def write(self,con,q,old):
        q['revision']=old+1;q['updated_at']=now()
        if old:
            result=con.execute('UPDATE tool_completion_queues SET revision=?,document=? WHERE id=? AND revision=?',
                               (q['revision'],canonical(q),q['id'],old))
            if result.rowcount!=1:raise Conflict('补齐队列已更新，请刷新')
        else:con.execute('INSERT INTO tool_completion_queues VALUES(?,?,?)',(q['id'],1,canonical(q)))

    def public(self,q):
        result=copy.deepcopy(q)
        result.pop('snapshot',None)
        result['concurrency']=q.get('concurrency',1)
        result['global_concurrency']=GLOBAL_CONCURRENCY
        for item in result['items']:
            task=self.service.get(item['task_id'])
            item.update(status=task['status'],calls=task.get('calls',0),
                        remaining_calls=max(0,task.get('call_limit',item['max_calls'])-task.get('calls',0)),
                        message=task.get('message',''),questions=task.get('questions',{}),
                        failure_routing=task.get('failure_routing'),validation=task.get('formula_validation'),
                        candidate=task.get('candidate'),registered=item.get('registered',False))
        result['active_count']=sum(i['status'] in IN_FLIGHT for i in result['items'])
        result['pending_count']=sum(i['status'] in ('completion_pending','dispatch_ready') for i in result['items'])
        result['total_calls']=sum(i['calls'] for i in result['items'])
        result['approved_call_limit']=sum(self.service.get(i['task_id']).get('call_limit',i['max_calls']) for i in result['items'])
        result['running']=any(i['status'] in ACTIVE or i['status']=='delivery_unknown' or (q['status']=='generating' and i['status']=='completion_pending') for i in result['items'])
        return result

    def context(self,key):
        t=self.service.get(key)
        if not t.get('plan'):raise Conflict('请先生成计算方案')
        plan=DesignPlan.model_validate(t['plan']);self.service.audit_plan(plan)
        with self.service.connect() as con:
            row=con.execute("SELECT document FROM tool_completion_queues WHERE json_extract(document,'$.source_id')=? OR json_extract(document,'$.result_design_id')=? ORDER BY rowid DESC LIMIT 1",(key,key)).fetchone()
        contexts=[declaration_context(s,key,t['revision']) for s in plan.steps if not s.module_ref]
        budgets=budget_snapshot(t)
        for ctx in contexts:ctx['budget']=budgets.get('formula:'+ctx['step_id'])
        return {'source_revision':t['revision'],'steps':contexts,
                'queue':self.public(json.loads(row[0])) if row else None}

    def adopted_extension(self,old_task,new_task):
        if not old_task.get('queue_id'):return
        q=self.get(old_task['queue_id']);old=q['revision']
        item=next((i for i in q['items'] if i['task_id']==old_task['id']),None)
        if not item:raise Conflict('扩展所属队列已变化，请刷新')
        item.setdefault('previous_task_ids',[]).append(old_task['id']);item['task_id']=new_task['id']
        with self.service.connect() as con:self.write(con,q,old)

    def control(self,key,req,pause):
        q=self.get(key)
        if q.get('archived') or self.service.get(q['source_id']).get('archived'):raise Conflict('队列已归档，请先恢复原任务')
        if q['revision']!=req.revision:raise Conflict('队列已更新，请刷新')
        if q['status']=='completed':raise Conflict('队列已完成')
        old=q['revision'];q['status']='paused' if pause else 'generating'
        with self.service.connect() as con:self.write(con,q,old)
        return self.public(q)

    def create(self,key,req):
        t=self.service.get(key);self.service.check_revision(t,req.revision)
        if t['status'] not in ('awaiting_confirmation','missing_tools'):raise Conflict('请先返回已核对的计算方案')
        plan=DesignPlan.model_validate(t['plan']);self.service.audit_plan(plan)
        report=self.service.plan_report(t,plan)
        if not report['valid']:fail('PLAN_PREFLIGHT','请先修复方案接线与声明','/plan')
        ids=[i.step_id for i in req.items]
        if len(set(ids))!=len(ids):fail('COMPLETION_DUPLICATE','不能重复选择步骤','/items')
        steps={s.id:s for s in plan.steps}
        for item in req.items:
            if item.step_id not in steps or steps[item.step_id].module_ref:
                fail('COMPLETION_SELECTION','只能选择当前缺少工具的步骤','/items/'+item.step_id)
            s=steps[item.step_id]
            if item.formula and s.formula_latex and item.formula!=s.formula_latex:
                fail('CONTRACT_CHANGE_REQUIRES_CONFIRM','已有公式不能在补齐队列中改写，请派生方案','/items/'+s.id+'/formula')
            from .designs import check_examples
            check_examples(item.examples,s.inputs,s.outputs)
        fingerprint=digest({'source_id':key,'revision':req.revision,'items':[i.model_dump() for i in req.items],'concurrency':req.concurrency})
        with self.service.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            existing=con.execute("SELECT document FROM tool_completion_queues WHERE json_extract(document,'$.fingerprint')=?",(fingerprint,)).fetchone()
            if existing:return self.public(json.loads(existing[0]))
            # A second queue must not duplicate generation or reset an existing step's budget.
            for row in con.execute("SELECT document FROM tool_completion_queues WHERE json_extract(document,'$.source_id')=?",(key,)):
                prior=json.loads(row[0])
                raise Conflict('此方案已有补齐队列，请恢复原队列或打开注册后的派生方案；额度不会重置')
            q={'id':uuid.uuid4().hex,'source_id':key,'source_revision':req.revision,'snapshot':copy.deepcopy(t),
               'plan_hash':digest(plan.model_dump()),'fingerprint':fingerprint,'created_at':now(),
               'status':'generating','concurrency':req.concurrency,'items':[],'receipts':[]}
            for item in req.items:
                step=steps[item.step_id]
                ctx=declaration_context(step,key,t['revision'])
                formula=item.formula or ctx['formula'];evidence='\n'.join(ctx['evidence'])
                if item.evidence:evidence+='\n用户补充来源：'+item.evidence
                child_plan=plan.model_dump()
                selected=next(s for s in child_plan['steps'] if s['id']==step.id)
                # Explicit missing information is a new confirmed snapshot, never a rewrite of the source.
                if not selected['formula_latex'] and item.formula:selected['formula_latex']=item.formula
                if not selected['evidence'] and item.evidence:selected['evidence']=[item.evidence]
                task={'id':uuid.uuid4().hex,'parent_id':key,'queue_id':q['id'],'created_at':now(),'revision':1,
                      'updated_at':now(),'phase':'formula','status':'completion_pending','request':copy.deepcopy(t['request']),
                      'plan':child_plan,'stage_budgets':budget_snapshot(t),'calls':0,'call_limit':item.max_calls,
                      'completion_draft':{'step_id':step.id,'formula':formula,'evidence':evidence,
                                          'declarations':formula_declarations(step),
                                          'examples':[ex.model_dump() for ex in item.examples]},
                      'adopted_context':ctx,'events':[{'event':'completion_requirements_confirmed','at':now()}]}
                task['formula_request']=copy.deepcopy(task['completion_draft'])
                prior=task['stage_budgets'].get('formula:'+step.id)
                if prior:task.update(calls=prior['used'],call_limit=prior['limit'])
                base=self.service.modules().get(step.derives_from) if step.derives_from else None
                if base:
                    occupied=con.execute('SELECT COALESCE(MAX(version),0) FROM modules WHERE id=?',(base.id,)).fetchone()[0]
                    reserved=con.execute("SELECT COALESCE(MAX(json_extract(document,'$.completion_registration_target.version')),0) FROM workflow_designs WHERE json_extract(document,'$.completion_registration_target.id')=?",(base.id,)).fetchone()[0]
                    task['completion_registration_target']={'id':base.id,'version':max(occupied,reserved)+1,'parent_version':base.version}
                con.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(task['id'],1,canonical(task)))
                q['items'].append({'step_id':step.id,'title':step.title,'task_id':task['id'],'max_calls':task['call_limit']})
            self.write(con,q,0)
        return self.public(q)

    def active_tasks(self, states):
        with self.service.connect() as con:
            rows=con.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.status') IN ("+','.join('?' for _ in states)+")",tuple(states)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def reserved_count(self):
        active=self.active_tasks(ACTIVE|{'delivery_unknown'})
        inflight=[t for t in active if t['status'] in IN_FLIGHT]
        count=len(inflight);ready={}
        for task in active:
            if task['status']!='dispatch_ready' or task.get('archived'):continue
            key=task.get('queue_id')
            if not key:count+=1
            else:ready[key]=ready.get(key,0)+1
        for key,n in ready.items():
            q=self.get(key)
            if q.get('archived') or q['status'] in ('paused','completed') or self.service.get(q['source_id']).get('archived'):continue
            used=sum(t.get('queue_id')==key for t in inflight)
            count+=min(n,max(0,q.get('concurrency',1)-used))
        return count

    def can_dispatch(self, task):
        active=self.active_tasks(IN_FLIGHT)
        if len(active)>=GLOBAL_CONCURRENCY:return False
        if task.get('queue_id'):
            q=self.get(task['queue_id'])
            if q.get('archived') or self.service.get(q['source_id']).get('archived') or q['status'] in ('paused','completed'):return False
            if sum(t.get('queue_id')==q['id'] for t in active)>=q.get('concurrency',1):return False
        return True

    def configure(self,key,req):
        q=self.get(key)
        if q.get('archived') or self.service.get(q['source_id']).get('archived'):raise Conflict('队列已归档')
        if q['status']=='completed':raise Conflict('队列已完成')
        if q['revision']!=req.revision:raise Conflict('队列已更新，请刷新')
        old=q['revision'];q['concurrency']=req.concurrency
        q.setdefault('events',[]).append({'event':'user_changed_concurrency','at':now(),'concurrency':req.concurrency})
        with self.service.connect() as con:self.write(con,q,old)
        return self.public(q)

    def start_pending(self,q,pending):
        try:
            # Recheck pause/archival immediately before preparing. Dispatch also
            # checks the live queue, so a concurrent pause cannot launch a call.
            live=self.get(q['id'])
            if live['status']!='generating' or live.get('archived'):return
            draft=pending['completion_draft']
            step=next(s for s in DesignPlan.model_validate(pending['plan']).steps if s.id==draft['step_id'])
            missing=[m for m in declaration_context(step,q['source_id'],q['source_revision'])['missing'] if m.get('code')!='UNKNOWN_UNIT']
            if missing:
                pending.update(status='blocked',completion_missing_info=True,questions={str(i):x['field']+'：'+x['message'] for i,x in enumerate(missing)},
                               message='缺少声明，尚未消耗模型调用；请核对并派生修订补齐',
                               failure_routing={'route':'needs_information','automatic':False,'reason':'缺少完整计算声明','next_action':'补充声明并确认修订'})
                self.service.save(pending,'completion_missing_information');return
            pending['status']='awaiting_confirmation'
            try:request=preparation_request(pending)
            except ValidationError as error:
                fail('COMPLETION_PREPARATION_FAILED','平台准备工具请求失败，尚未调用模型：'+str(error),'/completion_draft')
            self.service.prepare_formula(pending,request,pending['call_limit'])
        except Conflict:pass
        except Exception as error:
            current=self.service.get(pending['id'])
            if current['status']=='completion_pending':self.service.retry(current,error,allow_retry=False)

    def tick(self):
        if not self._tick_lock.acquire(blocking=False):return
        try:
            with self.service.connect() as con:
                rows=con.execute("SELECT document FROM tool_completion_queues WHERE json_extract(document,'$.status')='generating'").fetchall()
            queues=[json.loads(row[0]) for row in rows]
            # One item per queue per pass: fill available slots fairly, then
            # immediately reuse them on later ticks. Unknown delivery holds a slot.
            for _ in range(60):
                progressed=False
                for saved in queues:
                    q=self.get(saved['id'])
                    if q['status']!='generating' or q.get('archived') or self.service.get(q['source_id']).get('archived'):continue
                    tasks=[self.service.get(i['task_id']) for i in q['items']]
                    active=sum(t['status'] in ACTIVE or t['status']=='delivery_unknown' for t in tasks)
                    pending=next((t for t in tasks if t['status']=='completion_pending' and not t.get('archived')),None)
                    if not pending and not active:
                        with self.service.connect() as con:
                            q=self.get(q['id']);old=q['revision']
                            if q['status']=='generating':q['status']='review';self.write(con,q,old)
                        continue
                    if pending and active<q.get('concurrency',1) and self.reserved_count()<GLOBAL_CONCURRENCY:
                        self.start_pending(q,pending);progressed=True
                if not progressed:break
        finally:self._tick_lock.release()

    def register(self,key,req):
        if len(set(req.task_ids))!=len(req.task_ids):fail('COMPLETION_DUPLICATE','候选不可重复选择','/task_ids')
        if not req.allow_unverified:fail('EXPLICIT_SELECTION','请明确核对候选和验证范围后注册','/allow_unverified')
        q=self.get(key);chosen=set(req.task_ids)
        if q.get('archived') or self.service.get(q['source_id']).get('archived'):raise Conflict('队列已归档，请先恢复原任务')
        for receipt in q['receipts']:
            if receipt['request_revision']==req.revision and set(receipt['task_ids'])==chosen:
                return {'queue':self.public(q),'design':self.service.public(self.service.get(receipt['design_id']))}
        if q['revision']!=req.revision:raise Conflict('队列已更新，请核对最新候选')
        if self.public(q)['running']:raise Conflict('请等待本轮生成结束后集中核对注册')
        items={i['task_id']:i for i in q['items']}
        if chosen-items.keys():fail('COMPLETION_SELECTION','候选不属于本队列','/task_ids')
        tasks=[self.service.get(key) for key in req.task_ids]
        for task in tasks:
            if task['status']!='formula_candidate' or items[task['id']].get('registered'):raise Conflict('只能注册尚未关联的可核对候选')
        base=self.service.get(q['result_design_id']) if q.get('result_design_id') else q['snapshot']
        plan=copy.deepcopy(base['plan']);modules=[]
        from .registration import verify_references
        store=self.service.store()
        for task in tasks:
            m=Module.model_validate(task['candidate']);check_module(m)
            if audit_dependencies(m)['status']!='passed':raise Conflict('候选字段依赖检查未通过')
            verify_references(m.model_dump(),store.definitions(),store.catalogs(),store.rules())
            step=next(s for s in plan['steps'] if s['id']==task['formula_request']['step_id'])
            adopted=next(s for s in task['plan']['steps'] if s['id']==step['id'])
            step.update(module_ref=f'{m.id}@{m.version}',inputs=copy.deepcopy(adopted['inputs']),outputs=copy.deepcopy(adopted['outputs']),formula_latex=adopted['formula_latex'],evidence=adopted['evidence'],
                        selection_reason='用户在补齐队列中核对并注册；验证范围见任务 '+task['id'])
            modules.append((m,task))
        # One transaction: versions, lineage, the single derived plan and the queue receipt.
        child={'id':uuid.uuid4().hex,'parent_id':base['id'],'completion_queue_id':key,'request':base['request'],
               'created_at':now(),'updated_at':now(),'revision':1,'phase':'plan','status':'awaiting_confirmation',
               'plan':plan,'stage_budgets':budget_snapshot(base),'tool_evidence':[],
               'events':[{'event':'completion_partial_registration','at':now()}]}
        for item in q['items']:
            for scope,budget in budget_snapshot(self.service.get(item['task_id'])).items():
                previous=child['stage_budgets'].get(scope,{'used':0,'limit':0})
                child['stage_budgets'][scope]={'used':max(previous['used'],budget['used']),'limit':max(previous['limit'],budget['limit'])}
        with store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current=json.loads(con.execute('SELECT document FROM tool_completion_queues WHERE id=?',(key,)).fetchone()[0])
            if current['revision']!=req.revision:raise Conflict('注册已处理或队列已更新，请刷新')
            for m,task in modules:
                # Re-read inside transaction; reject concurrent edits and version collisions atomically.
                saved=json.loads(con.execute('SELECT document FROM workflow_designs WHERE id=?',(task['id'],)).fetchone()[0])
                if saved['revision']!=task['revision'] or saved['status']!='formula_candidate':raise Conflict('候选已变化，请重新核对')
                existing=con.execute('SELECT document FROM modules WHERE id=? AND version=?',(m.id,m.version)).fetchone()
                if existing and Module.model_validate(json.loads(existing[0]))!=m:raise Conflict('模块版本已被不同内容占用')
                if not existing:
                    store.persist_module_rules(con,m)
                    con.execute('INSERT INTO modules VALUES(?,?,?)',(m.id,m.version,canonical(m.model_dump())))
                    parent=task.get('registration_target',{}).get('parent_version')
                    if parent is not None:
                        if not con.execute('SELECT 1 FROM modules WHERE id=? AND version=?',(m.id,parent)).fetchone():raise Conflict('派生基准不存在')
                        relation={'parent_version':parent,'summary':'队列中明确核对注册',
                                  'acceptance_label':'独立样例通过，适用性待核对' if task['formula_request']['examples'] else '未提供独立样例，待核对','created_at':now()}
                        con.execute('INSERT INTO module_relations VALUES(?,?,?)',(m.id,m.version,canonical(relation)))
                child['tool_evidence'].append({'module_ref':f'{m.id}@{m.version}','report':task['formula_validation']})
                saved.update(status='formula_registered',revision=saved['revision']+1,message='已由用户选中注册，统一派生方案继续')
                saved['updated_at']=now();saved.setdefault('events',[]).append({'event':'user_registered_queue_candidate','actor':'user','at':now(),'queue_id':key,'revision':saved['revision']})
                con.execute('UPDATE workflow_designs SET revision=?,document=? WHERE id=?',(saved['revision'],canonical(saved),saved['id']))
                next(i for i in q['items'] if i['task_id']==task['id'])['registered']=True
            con.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child['id'],1,canonical(child)))
            q['result_design_id']=child['id'];q['receipts'].append({'request_revision':req.revision,'task_ids':req.task_ids,'design_id':child['id'],'at':now()})
            q['status']='completed' if all(i.get('registered') for i in q['items']) else 'review'
            self.write(con,q,req.revision)
        return {'queue':self.public(q),'design':self.service.public(child)}


def install(app,service):
    completion=Completion(service)
    from .completion_recovery import install as install_recovery
    completion.recovery=install_recovery(app,completion)
    from .input_usage import install as install_input_usage
    completion.input_usage=install_input_usage(app,completion)
    @app.get('/api/workflow-designs/{key}/tool-completion')
    def context(key:str):return completion.context(key)
    @app.post('/api/workflow-designs/{key}/tool-completion')
    def create(key:str,req:ToolCompletionRequest):return completion.create(key,req)
    @app.get('/api/tool-completion-queues/{key}')
    def read(key:str):return completion.public(completion.get(key))
    @app.post('/api/tool-completion-queues/{key}/concurrency')
    def concurrency(key:str,req:ToolCompletionConcurrency):return completion.configure(key,req)
    @app.post('/api/tool-completion-queues/{key}/register')
    def register(key:str,req:ToolCompletionRegister):return completion.register(key,req)
    @app.post('/api/tool-completion-queues/{key}/pause')
    def pause(key:str,req:DesignConfirm):return completion.control(key,req,True)
    @app.post('/api/tool-completion-queues/{key}/resume')
    def resume(key:str,req:DesignConfirm):return completion.control(key,req,False)
    return completion
