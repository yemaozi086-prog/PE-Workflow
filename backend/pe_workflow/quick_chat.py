"""Direct DSH conversations with atomic, versioned workflow draft delivery.

No plan/formula/bind orchestrator, automatic model retries or automatic runs.
The same executable module and graph checks still apply to delivered bundles.
"""
import asyncio
import copy
import hmac
import json
import os
import secrets
import time
import uuid
from pydantic import Field
from .models import StrictModel, Module, Workflow
from .storage import Conflict, canonical, digest, now
from .engine import check_module, validate_workflow, WorkflowError

ACTIVE = {'sending', 'working', 'delivery_unknown'}
TEMPLATE = '''请直接生成一份可测试的电力电子工作流草稿。
设计场景与计算目标：
拓扑、公式及出处：
输入量、单位与工况范围：
希望得到的结果和约束：
是否需要有限扫描、循环或子图：
物理假设和不包含的内容：
已有参考结果（可选）：

先读取平台当前能力和契约；信息充分时直接实现并保存草稿，不逐模块追问。
关键物理信息缺失时集中询问。保留所有要求的输出，不编造验收答案。
程序或接线错误请定位修复；改变物理模型、公式或约束前先说明并征求确认。'''


class Send(StrictModel):
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=60000)
    run_id: str | None = None


class Version(StrictModel):
    revision: int = Field(ge=1)


class Bundle(StrictModel):
    workflow: Workflow
    modules: list[Module] = Field(default_factory=list, max_length=200)
    summary: str = Field(min_length=1, max_length=12000)


def text_events(history, since):
    """Only public assistant text; never persist reasoning or raw tool arguments."""
    result=[]
    for row in history.get('events', []):
        e=row.get('event', row)
        if e.get('time', 0) < since * 1000 or e.get('type')!='assistant/message': continue
        message=e.get('data', {}).get('message', {})
        text='\n'.join(c.get('text','') for c in message.get('content',[]) if c.get('type')=='text')
        if text.strip(): result.append({'id':'dsh-'+str(e.get('seq')), 'role':'assistant','text':text[:60000]})
    return result


class QuickChat:
    def __init__(self, get_store, rpc):
        self.get_store=get_store; self.rpc=rpc; self.locks={}; self.poll_locks={}; self.jobs=set()
        self.initialize()

    def initialize(self):
        with self.get_store().connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS quick_chats(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)')

    def get(self, key):
        self.initialize()
        with self.get_store().connect() as c: row=c.execute('SELECT document FROM quick_chats WHERE id=?',(key,)).fetchone()
        if not row: raise ValueError('对话不存在')
        return json.loads(row[0])

    def save(self, t):
        old=t['revision']; t=copy.deepcopy(t); t.update(revision=old+1,updated_at=now())
        with self.get_store().connect() as c:
            if old:
                r=c.execute('UPDATE quick_chats SET revision=?,document=? WHERE id=? AND revision=?',(t['revision'],canonical(t),t['id'],old))
                if r.rowcount!=1: raise Conflict('对话已更新，请刷新后重试')
            else: c.execute('INSERT INTO quick_chats VALUES(?,?,?)',(t['id'],t['revision'],canonical(t)))
        return t

    def create(self):
        self.initialize()
        key=uuid.uuid4().hex
        return self.save({'id':key,'revision':0,'title':'新的工作流对话','workflow_id':'quick_'+key,
            'status':'idle','messages':[],'turns':0,'artifact_revision':0,'created_at':now()})

    def public(self,t):
        return {k:v for k,v in t.items() if k not in ('token','pending_bundle','session_created','started','last_poll','turn_id','staged_modules','staged_workflow','staged_summary','feedback')}

    def auth(self,key,body):
        t=self.get(key)
        if not isinstance(body.get('token'),str) or not hmac.compare_digest(body['token'],t.get('token','')) or body.get('turn_id')!=t.get('turn_id'):
            raise Conflict('本轮凭证已失效；不会接受旧结果')
        if t['status'] not in ACTIVE: raise Conflict('本轮已关闭，不接受迟到结果')
        return t

    def context(self,t,section,reference=None):
        from .finite_control import control_spec
        from .design_guidance import array_logic_repair
        store=self.get_store()
        if section=='index':
            return {'workflow_id':t['workflow_id'],'current_revision':t['artifact_revision'],
                'sections':['schemas','rules','modules','module','current','diagnostics','run','run_trace','example'],
                'contract':'quick-chat-v1','notice':'Direct conversation. Read schemas and rules, then submit one complete bundle. No plan stages. A saved draft is not numerical validation.'}
        if section=='schemas':return {'bundle':Bundle.model_json_schema()}
        if section=='rules':
            from .expressions import syntax_manifest
            from .numeric import catalog
            return {'controls':control_spec(),'array_logic':array_logic_repair(),'syntax':syntax_manifest(),'numeric_functions':catalog(),
                'implementation':'Prefer contract_version=3.0, named flat_dict ports, python_subset def compute(inputs), inputs[bag][field], return {output_port: {field:value}}. No adapter for python_subset. No filesystem/network/eval/importlib.',
                'identity':'Use workflow_id from index. New module ids MUST start with workflow_id + "_". Revisions of owned modules are immutable and sequential. Existing library modules may be reused by exact id@version but never overwritten.',
                'inputs':'Runtime values are supplied by users later. Do not install guessed numeric defaults. count/index/control values use entry_type=control, unit=1, quantity empty. Text needs a meaningful description/semantic_id. Use declared physical units.',
                'repair':'Read current bundle and diagnostics; change only erroneous code or bindings. Preserve unchanged nodes, formula requirements, units, outputs and protections. Ask user before changing physical assumptions or expected answers.',
                'checks':'quick_check is optional, maximum 3 per human message, same diagnostic twice stops checks. Failed submission retains candidate and exact diagnostics. No automatic new model turns.',
                'submission':'quick_submit saves a draft immediately to the library. Even a structurally invalid graph can be saved with explicit diagnostics; unsafe module code cannot be installed. Do not claim tests or physical validation. Do not run or register through any other tool.'}
        if section=='modules':return [{'id':m['id'],'version':m['version'],'name':m['name']} for m in store.modules()]
        if section=='module':return next((m for m in store.modules() if f"{m['id']}@{m['version']}"==reference),{'error':'MODULE_NOT_FOUND'})
        if section=='current':
            saved=store.workflow(t['workflow_id'])
            with store.connect() as c:row=c.execute('SELECT document FROM workflow_drafts WHERE id=?',(t['workflow_id'],)).fetchone()
            return {'saved':saved,'canvas_draft':json.loads(row[0]) if row else None,'pending_bundle':t.get('pending_bundle'),'staged_modules':t.get('staged_modules',{}),'staged_workflow':t.get('staged_workflow'), 'owned_modules':[m for m in store.modules() if m['id'].startswith(t['workflow_id']+'_')]}
        if section=='diagnostics':
            feedback=t.get('feedback');summary=None
            if feedback:
                failed=[r for r in feedback.get('trace',[]) if r.get('status')=='failed' or r.get('diagnostic')]
                summary={k:feedback.get(k) for k in ('id','status','diagnostics','snapshot_hash')}
                summary.update(failed_nodes=failed[:10],failed_node_count=len(failed),trace_count=len(feedback.get('trace',[])),
                    output_names=list((feedback.get('outputs') or {}).keys()),
                    exact_data_sections={'run':'Frozen inputs, full outputs, workflow and modules','run_trace':'Complete trace; paged JSON text'},
                    notice='先按错误位置定位；完整运行与逐点追踪仍保留，可按需读取，不把全部循环数据默认塞入诊断。')
            return {'diagnostics':t.get('diagnostics',[]),'feedback':summary}
        if section=='run':return {k:v for k,v in (t.get('feedback') or {}).items() if k!='trace'}
        if section=='run_trace':return (t.get('feedback') or {}).get('trace',[])
        if section=='example':
            from .dictionary_demo import fixture
            modules,wf,inputs=fixture()
            return {'modules':[m.model_dump() for m in modules],'workflow':wf.model_dump(),'example_inputs':inputs}
        raise ValueError('未知上下文分区')

    def check(self,t,raw):
        bundle=Bundle.model_validate(raw); wf=bundle.workflow; store=self.get_store()
        if wf.id!=t['workflow_id']:raise ValueError('必须保留本对话的 workflow_id')
        if len({m.id for m in bundle.modules})!=len(bundle.modules):raise ValueError('一次只提交每个模块的一个版本')
        registry={f"{m['id']}@{m['version']}":Module.model_validate(m) for m in store.modules()}
        for m in bundle.modules:
            if not m.id.startswith(t['workflow_id']+'_'):raise ValueError('新模块必须使用本对话的命名空间；外部模块只能引用')
            key=f'{m.id}@{m.version}'
            if key in registry and registry[key].model_dump()!=m.model_dump():raise Conflict('已有模块版本不可覆盖：'+key)
            latest=max((v.version for v in registry.values() if v.id==m.id),default=0)
            if key not in registry and m.version!=latest+1:raise Conflict(f'{m.id} 需提交版本 {latest+1}')
            if m.implementation=='trusted_builtin':raise ValueError('快速对话不能新增内置信任代码')
            from .registration import verify_references
            verify_references(m.model_dump(),store.definitions(),store.catalogs(),store.rules())
            check_module(m);registry[key]=m
        from .registration import verify_references
        verify_references(wf.model_dump(),store.definitions(),store.catalogs(),store.rules())
        report=validate_workflow(wf,registry,None)
        return bundle,report

    def deliver(self,t,bundle,report):
        """All owned module revisions, graph revision and chat receipt commit together."""
        store=self.get_store(); wf=bundle.workflow
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT revision FROM quick_chats WHERE id=?',(t['id'],)).fetchone()
            if row[0]!=t['revision']:raise Conflict('对话已改变；旧提交未保存')
            store.assert_workflow_active(c,wf.id)
            current=c.execute('SELECT COALESCE(MAX(revision),0) FROM workflows WHERE id=?',(wf.id,)).fetchone()[0]
            if current!=t['artifact_revision']:raise Conflict('工作流已在画布更新，请先在对话中读取最新版本，不能覆盖')
            draft=c.execute('SELECT revision,document FROM workflow_drafts WHERE id=?',(wf.id,)).fetchone()
            if (draft[0] if draft else 0)!=t.get('base_draft_revision',0):raise Conflict('发送后画布又有编辑；旧提交不会覆盖，请读取最新画布后继续')
            for m in bundle.modules:
                row=c.execute('SELECT document FROM modules WHERE id=? AND version=?',(m.id,m.version)).fetchone()
                if row and json.loads(row[0])!=m.model_dump():raise Conflict('模块版本已改变')
                if not row:
                    latest=c.execute('SELECT COALESCE(MAX(version),0) FROM modules WHERE id=?',(m.id,)).fetchone()[0]
                    if m.version!=latest+1:raise Conflict('模块版本已改变，请读取当前版本')
                    c.execute('INSERT INTO modules VALUES(?,?,?)',(m.id,m.version,canonical(m.model_dump())))
                    c.execute('INSERT INTO module_relations VALUES(?,?,?)',(m.id,m.version,canonical({'parent_version':latest or None,'task_id':t['id'],'summary':bundle.summary,'acceptance_label':'快速生成草稿，未做独立数值验证'})))
            previous=store.workflow(wf.id) if current else None
            inputs=json.loads(draft[1]).get('inputs',{}) if draft else previous['inputs'] if previous else {}
            revision=current+1
            c.execute('INSERT INTO workflows VALUES(?,?,?,?)',(wf.id,revision,canonical({'workflow':wf.model_dump(),'inputs':inputs}),now()))
            c.execute('DELETE FROM workflow_drafts WHERE id=?',(wf.id,))
            store.ensure_workflow_meta(c,wf.id,wf.name)
            c.execute('UPDATE workflow_meta SET name=?,origin=?,updated_at=? WHERE id=?',(wf.name,canonical({'quick_chat_id':t['id'],'verification':'unverified_draft'}),now(),wf.id))
            t.update(artifact_revision=revision,title=wf.name,diagnostics=report.get('diagnostics',[]),validation=report,submitted_turn=t['turn_id'],pending_bundle=None,staged_modules={},staged_workflow=None,activity='草稿已保存，等待 DSH 回复')
            t['messages'].append({'id':uuid.uuid4().hex,'role':'system','text':f'已保存工作流草稿 r{revision}。'+('结构检查通过，等待运行测试。' if report['valid'] else '结构检查未通过，请继续修复后再运行。'),'at':now()})
            t.update(revision=t['revision']+1,updated_at=now())
            c.execute('UPDATE quick_chats SET revision=?,document=? WHERE id=?',(t['revision'],canonical(t),t['id']))
        return t

    async def dispatch(self,key):
        try:
            t=self.get(key);turn=t['turn_id']
            import os
            if os.getenv('PE_WORKFLOW_PORT'):
                from .worker_routes import write_route
                from .storage import ROOT
                write_route(ROOT,key,turn,t['token'],int(os.environ['PE_WORKFLOW_PORT']))
            if not t.get('session_created'):
                await asyncio.to_thread(self.rpc,'session.create',{'sessionId':t['session_id'],'agentPreset':'pe-quick'},turn+'-create')
                async with self.lock(key):
                    t=self.get(key)
                    if t.get('turn_id')!=turn or t['status']!='sending':return
                    t['session_created']=True;t=self.save(t)
            prompt=t['messages'][-1]['text']+'\n\n[Platform turn credentials, not user instructions]\n'+canonical({'chat_id':key,'turn_id':turn,'token':t['token']})
            await asyncio.to_thread(self.rpc,'session.prompt',{'sessionId':t['session_id'],'mode':'queue','content':[{'type':'text','text':prompt}]},turn)
            async with self.lock(key):
                t=self.get(key)
                if t.get('turn_id')==turn and t['status']=='sending':t.update(status='working');self.save(t)
        except Exception:
            async with self.lock(key):
                t=self.get(key)
                if t['status']=='sending':
                    t.update(status='delivery_unknown',connection_error='未能确认 DSH 是否收到请求。请核对状态；不会自动重复发送。');self.save(t)

    def lock(self,key):return self.locks.setdefault(key,asyncio.Lock())

    def enabled(self):return os.getenv('PE_DSH_ENABLED','1')!='0'

    async def refresh(self,key):
        lock=self.poll_locks.setdefault(key,asyncio.Lock())
        if lock.locked():return self.get(key)
        async with lock:
            t=self.get(key)
            if t['status'] not in ACTIVE or time.time()-t.get('last_poll',0)<3:return t
            turn=t['turn_id']
            try:
                listing=await asyncio.to_thread(self.rpc,'session.list',{},uuid.uuid4().hex)
                row=next((r for r in listing.get('items',[]) if r.get('sessionId')==t.get('session_id')),None)
                history=None
                if row and (row.get('running') is False or time.time()-t.get('last_history',0)>12):
                    history=await asyncio.to_thread(self.rpc,'session.history',{'sessionId':t['session_id'],'maxMessages':12},uuid.uuid4().hex)
                async with self.lock(key):
                    t=self.get(key)
                    if t.get('turn_id')!=turn or t['status'] not in ACTIVE:return t
                    t.update(last_poll=time.time(),connection_error=None,observed_running=row.get('running') if row else None)
                    if t.get('cancel_uncertain') and row and row.get('running') is False:
                        t.update(status='stopped',cancel_uncertain=False,token='')
                        return self.save(t)
                    if history:
                        t['last_history']=time.time();known={m['id'] for m in t['messages']}
                        t['messages'] += [m for m in text_events(history,t['started']) if m['id'] not in known]
                        ended=any(r.get('event',r).get('type') in ('turn/end','turn/error') and r.get('event',r).get('time',0)>=t['started']*1000 for r in history.get('events',[]))
                        if row.get('running') is False and ended:
                            t.update(status='idle',token='')
                            if not any(m['role']=='assistant' for m in text_events(history,t['started'])) and t.get('submitted_turn')!=turn:
                                t['messages'].append({'id':uuid.uuid4().hex,'role':'system','text':'DSH 已结束本轮，但没有返回文字或工作流。你可以继续说明要求；不会自动重试。'})
                        elif row.get('running') is False and time.time()-t['started']>30:
                            t.update(status='delivery_unknown',connection_error='DSH 当前未报告运行，但尚未观察到本轮结束事件。请核对或停止本轮，不自动重发。')
                    return self.save(t)
            except Exception:
                async with self.lock(key):
                    t=self.get(key)
                    if t.get('turn_id')==turn and t['status'] in ACTIVE:
                        t.update(last_poll=time.time(),connection_error='暂时无法读取 DSH 状态；不代表已停止。可稍后核对，或停止接收本轮结果。');t=self.save(t)
                    return t


def install(app,get_store,problem):
    from .automation import rpc
    service=QuickChat(get_store,rpc);app.state.quick_chat=service

    @app.get('/api/quick-chats')
    def listing():
        service.initialize()
        with get_store().connect() as c:rows=c.execute('SELECT document FROM quick_chats ORDER BY rowid DESC LIMIT 100').fetchall()
        return {'items':[{k:t[k] for k in ('id','title','status','artifact_revision','workflow_id','updated_at')} for t in map(lambda r:json.loads(r[0]),rows)],'template':TEMPLATE}

    @app.post('/api/quick-chats')
    def create():return service.public(service.create())

    @app.get('/api/quick-chats/{key}')
    async def read(key:str):
        try:return service.public(await service.refresh(key))
        except ValueError as e:problem('QUICK_CHAT_NOT_FOUND',str(e),404)

    @app.post('/api/quick-chats/{key}/messages')
    async def send(key:str,req:Send):
        if not service.enabled():problem('DSH_DISABLED','DSH 连接未启用，请在本机配置中启用后再发送',409)
        async with service.lock(key):
            t=service.get(key)
            if t['revision']!=req.revision:raise Conflict('对话已更新，请刷新后再发送')
            if t['status'] in ACTIVE:raise Conflict('DSH 本轮尚未结束，请等待或先停止本轮')
            feedback=None
            if req.run_id:
                with get_store().connect() as c:r=c.execute('SELECT document FROM runs WHERE id=?',(req.run_id,)).fetchone()
                if not r:raise ValueError('运行记录不存在')
                run=json.loads(r[0]);wf=(run.get('snapshot') or {}).get('workflow') or run.get('workflow') or {}
                if wf.get('id')!=t['workflow_id']:raise Conflict('该运行不属于此对话工作流')
                feedback={k:run.get(k) for k in ('id','status','outputs','diagnostics','snapshot_hash')}
                feedback['inputs']=(run.get('snapshot') or {}).get('inputs')
                feedback['trace']=run.get('trace',[])
                feedback['snapshot']=run.get('snapshot')
            current=get_store().workflow(t['workflow_id'])
            if current:t['artifact_revision']=current['revision']
            with get_store().connect() as c:row=c.execute('SELECT revision FROM workflow_drafts WHERE id=?',(t['workflow_id'],)).fetchone()
            t['base_draft_revision']=row[0] if row else 0
            t.update(status='sending',turns=t['turns']+1,turn_id=uuid.uuid4().hex,token=secrets.token_urlsafe(32),
                started=time.time(),last_poll=0,last_history=0,checks=0,check_signatures=[],feedback=feedback,connection_error=None,activity='正在提交本轮消息',observed_running=None)
            if t.get('cancel_uncertain'):
                raise Conflict('上一会话是否已取消尚未确认，请先核对 DSH 状态；不会重复排队')
            t.setdefault('session_id','pe-quick-'+key)
            t['messages'].append({'id':uuid.uuid4().hex,'role':'user','text':req.text,'at':now(),'run_id':req.run_id})
            if t['turns']==1:t['title']=req.text[:50]
            t=service.save(t)
            job=asyncio.create_task(service.dispatch(key));service.jobs.add(job);job.add_done_callback(service.jobs.discard)
            return service.public(t)

    @app.post('/api/quick-chats/{key}/stop')
    async def stop(key:str,req:Version):
        async with service.lock(key):
            t=service.get(key)
            if t['revision']!=req.revision:raise Conflict('状态已改变，请重新核对')
            t.update(status='delivery_unknown',token='',cancel_uncertain=True,last_poll=0);t=service.save(t)
        try:
            await asyncio.to_thread(service.rpc,'session.cancel',{'sessionId':t.get('session_id')},uuid.uuid4().hex)
        except Exception:
            async with service.lock(key):
                t=service.get(key);t.update(status='delivery_unknown',cancel_uncertain=True,connection_error='已停止接收旧结果，但 DSH 取消未确认。请核对状态，暂不重复发送。');t=service.save(t)
        return service.public(t)

    @app.post('/api/quick-chats/{key}/tool')
    async def tool(key:str,req:dict):
        async with service.lock(key):
            t=service.auth(key,req);op=req.get('operation')
            if op=='context':
                t['activity']='DSH 正在读取平台规范或当前工作流';service.save(t)
                value=canonical(service.context(t,req.get('section','index'),req.get('module_ref')))
                offset=max(0,int(req.get('offset',0)));page=value[offset:offset+18000]
                return {'json_text':page,'next_offset':offset+len(page) if offset+len(page)<len(value) else None}
            if t.get('submitted_turn')==t['turn_id']:raise Conflict('本轮草稿已提交，请回复用户，不要再次提交')
            raw=req.get('bundle')
            if op=='draft':
                action=req.get('action')
                if action in ('put_module','put_workflow'):
                    try:
                        if action=='put_module':
                            m=Module.model_validate(req.get('module'))
                            if not m.id.startswith(t['workflow_id']+'_'):raise ValueError('模块必须使用本对话命名空间')
                            t.setdefault('staged_modules',{})[m.id]=m.model_dump()
                            if len(t['staged_modules'])>200:raise ValueError('本对话最多暂存 200 个模块')
                        else:
                            w=Workflow.model_validate(req.get('workflow'))
                            if w.id!=t['workflow_id']:raise ValueError('必须保留本对话工作流 ID')
                            t.update(staged_workflow=w.model_dump(),staged_summary=str(req.get('summary') or 'DSH 生成的待测试草稿')[:12000])
                        t['activity']='DSH 正在组装草稿（尚未保存到工作流库）';service.save(t)
                        return {'status':'staged','module_count':len(t.get('staged_modules',{})),'has_workflow':bool(t.get('staged_workflow')),'notice':'仅暂存，尚未注册或保存；继续上传其余产物后 check/submit。'}
                    except ValueError as e:return {'status':'not_staged','diagnostics':[{'code':'ARTIFACT_SCHEMA','message':str(e)}]}
                if action not in ('check','submit'):raise ValueError('未知草稿操作')
                op=action
                raw={'workflow':t.get('staged_workflow'),'modules':list(t.get('staged_modules',{}).values()),'summary':t.get('staged_summary','待测试草稿')}
            if op not in ('check','submit'):raise ValueError('未知工具操作')
            if t.get('submitted_turn')==t['turn_id']:raise Conflict('本轮草稿已提交，请回复用户，不要再次提交')
            if op=='check' and (t.get('checks',0)>=3 or any(t.get('check_signatures',[]).count(s)>=2 for s in t.get('check_signatures',[]))):
                return {'status':'check_limit','message':'本轮预检已到停止条件。请向用户说明，不要继续试探。'}
            try:
                bundle,report=await asyncio.to_thread(service.check,t,raw)
                if op=='submit':
                    t=service.deliver(t,bundle,report)
                    return {'status':'saved','workflow_id':t['workflow_id'],'revision':t['artifact_revision'],'validation':report,'notice':'草稿已进入工作流库；没有运行，也没有数值验证。请用中文向用户说明下一步。'}
            except (ValueError,WorkflowError,Conflict) as e:
                report={'valid':False,'diagnostics':[e.diagnostic.model_dump() if isinstance(e,WorkflowError) else {'code':'BUNDLE_ERROR','path':'/bundle','message':str(e)}]}
                if op=='submit':t['pending_bundle']=raw
            t.update(diagnostics=report.get('diagnostics',[]))
            t['activity']='候选检查完成，等待 DSH 继续处理' if op=='check' else '草稿未保存，诊断已返回 DSH'
            if op=='check':
                t['checks']=t.get('checks',0)+1
                if not report['valid']:t.setdefault('check_signatures',[]).append(digest(report['diagnostics']))
            service.save(t)
            return {'status':'checked' if op=='check' else 'not_saved','validation':report,'checks_used':t.get('checks',0),'notice':'按诊断修改候选，不改变用户的公式、假设或验收答案。'}
    return service
