"""Durable, task-bound DSH planning. Publication is owned by the platform."""
import asyncio
import os
import copy
import secrets
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from fastapi import Header
from .models import (AssemblyTaskRequest, AssemblyWorkerResult, AssemblyAnswer,
                     AssemblyContinue, AssemblyPreviewRequest, Module, Workflow)
from .scenarios import context, compile_plan, evidence_records
from .storage import canonical, digest, now, Conflict, ROOT
from .automation import rpc, failure_signature
from .evaluation import equivalent

ACTIVE={'queued','dispatch_ready','dispatching','awaiting_worker','validating'}


class Journal:
    def __init__(self,store):
        self.store=store
        with store.connect() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS assembly_tasks(id TEXT PRIMARY KEY, revision INTEGER, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assembly_events(task_id TEXT, seq INTEGER, document TEXT, PRIMARY KEY(task_id,seq));''')
            con.execute("CREATE INDEX IF NOT EXISTS assembly_status_idx ON assembly_tasks(json_extract(document,'$.status'))")

    def get(self,key):
        import json
        with self.store.connect() as con:
            row=con.execute('SELECT document FROM assembly_tasks WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        import json
        with self.store.connect() as con:rows=con.execute('SELECT document FROM assembly_tasks ORDER BY rowid DESC').fetchall()
        return [json.loads(r[0]) for r in rows]

    def active(self):
        import json
        with self.store.connect() as con:
            rows=con.execute("SELECT document FROM assembly_tasks WHERE json_extract(document,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')',tuple(ACTIVE)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def events(self,key):
        import json
        with self.store.connect() as con:rows=con.execute('SELECT document FROM assembly_events WHERE task_id=? ORDER BY seq',(key,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save(self,task,event,publication=None):
        t=copy.deepcopy(task);old=t.get('revision',0);t['revision']=old+1;t['updated_at']=now()
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if old:
                changed=con.execute('UPDATE assembly_tasks SET revision=?,document=? WHERE id=? AND revision=?',
                                    (t['revision'],canonical(t),t['id'],old))
                if changed.rowcount!=1:raise Conflict('装配任务已被用户或回调更新')
            else:con.execute('INSERT INTO assembly_tasks VALUES(?,?,?)',(t['id'],1,canonical(t)))
            if publication:
                wf,inputs,run=publication
                self.store.assert_workflow_active(con,wf.id)
                con.execute('INSERT INTO workflows VALUES(?,?,?,?)',(wf.id,1,canonical({'workflow':wf.model_dump(),'inputs':inputs}),now()))
                self.store.ensure_workflow_meta(con,wf.id,wf.name)
                con.execute('INSERT INTO runs VALUES(?,?,?)',(run['id'],canonical(run),run['created_at']))
            con.execute('INSERT INTO assembly_events VALUES(?,?,?)',(t['id'],t['revision'],canonical({'seq':t['revision'],'at':now(),'status':t['status'],**event})))
        return t


def public(t):
    return {k:v for k,v in t.items() if k not in ('token','callback_hash','modules')}


def preset_configuration():
    path=ROOT/'dsh/preset/flux-assembly/agent.cordis.yml'
    return {'preset':'flux-assembly','model':None,'model_note':'由 DSH 运行配置决定；平台尚未取得真实模型标识',
            'prompt':path.read_text(encoding='utf-8'),'prompt_hash':digest(path.read_text(encoding='utf-8')),
            'tool_hash':digest((path.parent/'flux-assembly-tools-v1.js').read_text(encoding='utf-8')),
            'reference_hash':digest((Path(__file__).parent/'assembly_reference.py').read_text(encoding='utf-8')),
            'permissions':['assembly_context','assembly_result'],'repair_limit':3,'timeout_seconds':600,
            'origins':{'scene':'platform','inputs':'user','facts':'user','prompt':'project_preset'},
            'publication':'platform_only_after_reference_pass'}


class Coordinator:
    def __init__(self,get_store,module_map,worker):
        self.get_store=get_store;self.module_map=module_map;self.worker=worker

    def journal(self):return Journal(self.get_store())

    def create(self,req):
        registry=self.module_map();packet=context(req.instruction,req.facts,registry)
        from .engine import fill_inputs,shape_dimensions
        ports={**registry['dbsrc_modulation@1'].inputs,'settings':registry['dbsrc_loss_capacitor@1'].inputs['settings']}
        values=fill_inputs(req.inputs,ports,'/inputs')
        shape_dimensions(values,ports,'/inputs')
        required={f"{b['module_id']}@{b['module_version']}" for b in packet['scene']['bindings']}
        config=preset_configuration()
        config['initial_call_limit']=req.max_calls
        packet.update(inputs=req.inputs,configuration=config,provenance=req.provenance)
        packet['context_hash']=digest(packet)
        questions={r['rule_id']:f"请明确 {r['rule_id']}；当前场景要求 {r['expected']}。不适用时请说明，不要强行确认。"
                   for r in packet['rule_report'] if r['status']=='needs_clarification'}
        status='dispatch_ready' if packet['status']=='ready' else packet['status']
        t={'id':uuid.uuid4().hex,'created_at':now(),'status':status,'request':req.model_dump(),
           'configuration':config,'context':packet,'context_revisions':[],
           'modules':[registry[k].model_dump() for k in sorted(required)],
           'questions':questions,'clarification_used':bool(questions),'answers':{},
           'generation_count':0,'repairs_used':0,'repair_limit':3,'batch':1,
           'batch_calls':0,'batch_call_limit':req.max_calls,
           'message':'上下文已冻结，等待 DSH 计划' if status=='dispatch_ready' else '场景条件需要核对；尚未调用 DSH'}
        return self.journal().save(t,{'actor':'platform','event':'context_frozen','context_hash':packet['context_hash']})

    def pause(self,t,status,message,report=None):
        t.update(status=status,message=message)
        if report is not None:t['last_report']=report
        return self.journal().save(t,{'actor':'platform','event':status,'message':message,'report':report})

    async def dispatch(self,t):
        installed=Path.home()/'.dsh/.agent-presets/flux-assembly'
        project=ROOT/'dsh/preset/flux-assembly'
        for name in ('agent.cordis.yml','flux-assembly-tools-v1.js'):
            if not (installed/name).exists() or (installed/name).read_bytes()!=(project/name).read_bytes():
                return self.pause(t,'failed','DSH 装配配置尚未安装或与本次配置不一致；未发起调用',{'code':'PRESET_MISMATCH','file':name})
        if digest((project/'agent.cordis.yml').read_text(encoding='utf-8'))!=t['configuration']['prompt_hash']:
            return self.pause(t,'failed','任务冻结后提示词已变更，请基于新配置创建任务',{'code':'CONFIG_CHANGED'})
        if digest((project/'flux-assembly-tools-v1.js').read_text(encoding='utf-8'))!=t['configuration']['tool_hash']:
            return self.pause(t,'failed','任务冻结后工具配置已变更，请新建任务',{'code':'CONFIG_CHANGED'})
        t.update(status='dispatching',generation_count=t['generation_count']+1,
                 batch_calls=t['batch_calls']+1,
                 call_id=uuid.uuid4().hex,token=secrets.token_urlsafe(32),session_id='flux-assembly-'+uuid.uuid4().hex,
                 dispatch_at=time.time(),message='正在向 DSH 提交装配子任务')
        t=self.journal().save(t,{'actor':'platform','event':'dispatch_intent','call_id':t['call_id']})
        try:
            await asyncio.to_thread(rpc,'session.create',{'sessionId':t['session_id'],'agentPreset':'flux-assembly'},t['call_id']+'-create')
            credentials={k:t[k] for k in ('call_id','token')};credentials['task_id']=t['id']
            prompt='Read assembly_context first, then submit assembly_result. Never run, register or publish. Task credentials: '+canonical(credentials)
            await asyncio.to_thread(rpc,'session.prompt',{'sessionId':t['session_id'],'mode':'queue','content':[{'type':'text','text':prompt}]},t['call_id'])
            current=self.journal().get(t['id'])
            if current['status']=='dispatching' and current.get('call_id')==t['call_id']:
                current.update(status='awaiting_worker',message='DSH 正在提出装配计划')
                self.journal().save(current,{'actor':'platform','event':'prompt_accepted'})
        except Exception as exc:
            current=self.journal().get(t['id'])
            if current['status']=='dispatching':self.pause(current,'delivery_unknown','调用送达状态不明；不会自动重发',{'error':str(exc)})

    def result(self,t,req):
        payload=req.model_dump(exclude={'token'});h=digest(payload)
        if t.get('callback_hash')==h:return {'status':'accepted','duplicate':True}
        if t['status'] not in ('dispatching','awaiting_worker','delivery_unknown'):raise Conflict('本次装配调用已结束')
        t.update(callback_hash=h,explanation=req.explanation)
        if req.kind=='plan':
            t.update(status='validating',plan=req.plan,message='平台正在编译并验证计划')
        elif req.kind=='questions':
            status='blocked' if t['clarification_used'] or not req.questions else 'needs_clarification'
            t.update(status=status,questions=req.questions,clarification_used=True,message=req.explanation or '请集中补充任务依据')
        else:t.update(status='blocked',questions=req.questions,message=req.explanation or '智能体报告关键依据不足')
        self.journal().save(t,{'actor':'agent','event':'worker_result','kind':req.kind,'plan':req.plan,'questions':req.questions,'explanation':req.explanation})
        return {'status':'accepted'}

    async def validate(self,t):
        try:
            modules={f"{m['id']}@{m['version']}":Module.model_validate(m) for m in t['modules']}
            if t['context']['evidence']!=evidence_records():raise ValueError('冻结科学依据已变更；请新建任务，不可用旧快照继续验证')
            if digest((Path(__file__).parent/'assembly_reference.py').read_text(encoding='utf-8'))!=t['configuration']['reference_hash']:
                raise ValueError('独立参考实现已变更；请基于新依据创建任务')
            req=AssemblyPreviewRequest.model_validate({**{k:v for k,v in t['request'].items() if k not in ('timeout_seconds','max_calls')},'plan':t['plan']})
            preview=compile_plan(req,modules)
            wf=Workflow.model_validate(preview['workflow']);wf.id='assembly_'+t['id'];wf.name+=' · '+t['id'][:6]
            snapshot={'workflow':wf.model_dump(),'inputs':req.inputs,'modules':t['modules']}
            actual=await asyncio.to_thread(self.worker,snapshot,t['request']['timeout_seconds'])
            if actual['status']!='succeeded':report={'stage':'execution',**actual}
            else:
                reference=await asyncio.to_thread(self.worker,{'mode':'assembly_reference','inputs':req.inputs},t['request']['timeout_seconds'])
                if reference['status']!='succeeded':report={'stage':'reference',**reference}
                elif not equivalent(actual['outputs'],reference['outputs']):
                    report={'stage':'reference','diagnostics':[{'code':'REFERENCE_MISMATCH','path':'/outputs','message':'结果与冻结旧模型的独立参考不一致'}]}
                else:
                    current=self.journal().get(t['id'])
                    if current['status']!='validating' or current.get('call_id')!=t.get('call_id'):return
                    record={'id':uuid.uuid4().hex,'created_at':now(),'finished_at':now(),'workflow_name':wf.name,
                            'output_ports':{k:p.model_dump() for k,p in wf.output_ports.items()},
                            'snapshot':snapshot,'snapshot_hash':digest(snapshot),'timeout_seconds':t['request']['timeout_seconds'],**actual}
                    validation={**preview['validation'],'execution':'passed','reference':'passed','reference_tolerance':1e-9}
                    result={'workflow':wf.model_dump(),'inputs':req.inputs,'revision':1,
                            'run':{k:v for k,v in record.items() if k!='snapshot'},'validation':validation,
                            'experience':preview['experience']}
                    current.update(status='completed',message='装配、运行及独立参考通过；已保存为新的工作流',result=result,
                                   last_report={'stage':'complete','status':'passed','validation':validation})
                    self.journal().save(current,{'actor':'platform','event':'published','workflow_id':wf.id,'run_id':record['id'],
                                               'snapshot_hash':record['snapshot_hash'],'validation':validation},(wf,req.inputs,record))
                    return
        except Exception as exc:
            diagnostic=getattr(exc,'diagnostic',None)
            report={'stage':'compile','diagnostics':[diagnostic.model_dump() if diagnostic else
                    {'code':'ASSEMBLY_SCHEMA','message':str(exc),'path':'/plan'}]}
        current=self.journal().get(t['id'])
        if current['status']!='validating' or current.get('call_id')!=t.get('call_id'):return
        current['last_report']=report
        if report.get('stage')=='reference' and any(d.get('code')=='REFERENCE_UNAVAILABLE' for d in report.get('diagnostics',[])):
            current.update(status='blocked',message='给定输入无法产生有效参考候选；修改接线不能解决，请核对输入后创建新任务')
            self.journal().save(current,{'actor':'platform','event':'reference_unavailable','report':report})
            return
        signature=failure_signature(report)
        current['repeat_count']=current.get('repeat_count',0)+1 if signature==current.get('failure_signature') else 1
        current['failure_signature']=signature
        if current['repeat_count']>=2 or current['batch_calls']>=current['batch_call_limit']:
            current.update(status='paused',message='修正已停止：同类错误连续两次或已修正三轮；候选及原因保留')
        else:current.update(status='dispatch_ready',repairs_used=current['repairs_used']+1,message='验证未通过，将诊断交给 DSH 修正')
        self.journal().save(current,{'actor':'platform','event':'validation_failed','report':report})

    async def loop(self):
        # After restart, never resend a possibly delivered prompt automatically.
        for t in self.journal().active():
            if t['status'] in ('dispatching','awaiting_worker'):
                self.pause(t,'delivery_unknown','服务恢复，原调用可能仍在执行；可接收原调用回传，不自动重发')
        while True:
            for t in self.journal().active():
                try:
                    if t['status']=='dispatch_ready':await self.dispatch(t)
                    elif t['status']=='validating':await self.validate(t)
                    elif t['status']=='awaiting_worker' and time.time()-t['dispatch_at']>600:
                        self.pause(t,'delivery_unknown','等待超过十分钟，原调用仍可回传；不自动重发')
                except Conflict:pass
                except Exception as exc:
                    current=self.journal().get(t['id'])
                    if current['status'] in ACTIVE:self.pause(current,'failed','平台阶段失败，候选保留',{'error':str(exc)})
            await asyncio.sleep(1)


def install(app,get_store,module_map,worker,problem):
    coordinator=Coordinator(get_store,module_map,worker);app.state.assembly=coordinator
    old_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        async with old_lifespan(app):
            if os.getenv('PE_DSH_ENABLED','1')=='0':
                yield
                return
            runner=asyncio.create_task(coordinator.loop())
            try:yield
            finally:
                runner.cancel()
                with suppress(asyncio.CancelledError):await runner
    app.router.lifespan_context=lifespan

    def task(key):
        t=coordinator.journal().get(key)
        if t is None:problem('TASK_NOT_FOUND','装配任务不存在',404)
        return t
    def authorized(key,call,token):
        t=task(key)
        if t.get('call_id')!=call or not secrets.compare_digest(t.get('token',''),token):problem('WORKER_CREDENTIAL','调用凭证无效',403)
        return t

    @app.get('/api/assembly-tasks')
    def listing():
        rows=coordinator.journal().all()
        return {'items':[{k:t.get(k) for k in ('id','status','created_at','message','generation_count')} for t in rows],'count':len(rows)}
    @app.get('/api/assembly-configuration')
    def configuration():return preset_configuration()
    @app.post('/api/assembly-tasks')
    def create(req:AssemblyTaskRequest):
        if os.getenv('PE_DSH_ENABLED','1')=='0':problem('DSH_DISABLED','DSH 未启用；请配置本地服务并重启平台',503)
        return public(coordinator.create(req))
    @app.get('/api/assembly-tasks/{key}')
    def read(key:str):return {**public(task(key)),'events':coordinator.journal().events(key)}
    @app.get('/api/assembly-tasks/{key}/context')
    def worker_context(key:str,call_id:str,authorization:str=Header(default='')):
        t=authorized(key,call_id,authorization.removeprefix('Bearer '))
        if t['status'] not in ('dispatching','awaiting_worker','delivery_unknown'):problem('WORKER_STATE','调用已结束',409)
        t=coordinator.journal().save(t,{'actor':'agent','event':'context_read','call_id':call_id})
        return {**t['context'],'answers':t['answers'],'previous_plan':t.get('plan'),'last_report':t.get('last_report')}
    @app.post('/api/assembly-tasks/{key}/result')
    def result(key:str,req:AssemblyWorkerResult):return coordinator.result(authorized(key,req.call_id,req.token),req)
    @app.post('/api/assembly-tasks/{key}/answer')
    def answer(key:str,req:AssemblyAnswer):
        t=task(key)
        if t['status'] not in ('needs_clarification','blocked'):problem('ANSWER_STATE','当前不接受补充回答',409)
        if not t['questions'] or set(req.answers)!=set(t['questions']) or any(not v.strip() for v in req.answers.values()):
            problem('ANSWER_REQUIRED','请回答全部列出的问题')
        t['answers'].update(req.answers)
        facts={**t['request']['facts'],**{k:v for k,v in req.answers.items() if k in {r['fact'] for r in t['context']['scene']['rules']}}}
        t['context_revisions'].append(t['context']);t['request']['facts']=facts
        modules={f"{m['id']}@{m['version']}":Module.model_validate(m) for m in t['modules']}
        packet=context(t['request']['instruction'],facts,modules)
        packet.update(inputs=t['request']['inputs'],configuration=t['configuration'],provenance=t['request'].get('provenance',{}));packet['context_hash']=digest(packet)
        t['context']=packet;t['callback_hash']=None
        t.update(status='dispatch_ready' if packet['status']=='ready' else 'blocked',message='补充依据已记录，将重新核对')
        return public(coordinator.journal().save(t,{'actor':'user','event':'answers_submitted','answers':req.answers,'context_hash':packet['context_hash']}))
    @app.post('/api/assembly-tasks/{key}/stop')
    def stop(key:str):
        t=task(key)
        if t['status'] not in ACTIVE|{'delivery_unknown'}:problem('STOP_STATE','当前任务已停止或完成',409)
        # Local cancellation is authoritative; late callbacks cannot publish.
        t['token']=secrets.token_urlsafe(32)
        t.update(status='stopped',message='用户已停止任务；晚到候选不会应用')
        return public(coordinator.journal().save(t,{'actor':'user','event':'stopped'}))
    @app.post('/api/assembly-tasks/{key}/continue')
    def resume(key:str,req:AssemblyContinue):
        t=task(key)
        if not req.approved:problem('APPROVAL_REQUIRED','需明确批准追加最多三轮')
        if t['status'] not in ('paused','failed','delivery_unknown'):problem('CONTINUE_STATE','当前不能追加修正',409)
        if t['status']=='delivery_unknown' and not req.retry_uncertain:problem('DELIVERY_UNKNOWN','需明确同意重试状态不明的调用')
        if req.refresh_configuration:
            if t['status']=='delivery_unknown':problem('CONFIG_UNCERTAIN','请先停止或核对原调用，不能在送达不明时更换配置',409)
            previous=t['configuration'];t['context_revisions'].append(t['context']);t['configuration']=preset_configuration()
            modules={f"{m['id']}@{m['version']}":Module.model_validate(m) for m in t['modules']}
            packet=context(t['request']['instruction'],t['request']['facts'],modules)
            packet.update(inputs=t['request']['inputs'],configuration=t['configuration'],provenance=t['request'].get('provenance',{}));packet['context_hash']=digest(packet)
            t['context']=packet
            config_change={'before':previous,'after':t['configuration'],'context_hash':packet['context_hash']}
        else:config_change=None
        t.update(status='dispatch_ready',repairs_used=0,repeat_count=0,batch=t['batch']+1,batch_calls=0,batch_call_limit=3,callback_hash=None,token=secrets.token_urlsafe(32))
        return public(coordinator.journal().save(t,{'actor':'user','event':'additional_repairs_approved','batch':t['batch'],'configuration_change':config_change}))
    return coordinator
