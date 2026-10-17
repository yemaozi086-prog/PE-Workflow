"""Durable platform-owned orchestration. Worker candidates never publish directly."""
import asyncio
import os
import copy
import json
import secrets
import time
import uuid
import urllib.request
from contextlib import suppress, asynccontextmanager
from fastapi import Header
from .models import AutomationRequest, AutomationAnswer, AutomationContinue, AutomationResult, Workflow
from .storage import canonical, digest, now, Conflict
from .changes import locate

ACTIVE={'queued','preparing','dispatch_ready','creating_session','dispatching','awaiting_worker','validating','applying'}


class Journal:
    def __init__(self,store):
        self.store=store
        with store.connect() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS automation_tasks(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS automation_events(task_id TEXT, seq INTEGER, document TEXT NOT NULL, PRIMARY KEY(task_id,seq));
              CREATE TABLE IF NOT EXISTS automation_runner(id INTEGER PRIMARY KEY, owner TEXT, expires REAL);''')
            con.execute("CREATE INDEX IF NOT EXISTS automation_status_idx ON automation_tasks(json_extract(document,'$.status'))")

    def lease(self,owner,release=False):
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if release:
                con.execute('DELETE FROM automation_runner WHERE owner=?',(owner,));return True
            row=con.execute('SELECT owner,expires FROM automation_runner WHERE id=1').fetchone()
            if row and row[0]!=owner and row[1]>time.time():return False
            con.execute('INSERT OR REPLACE INTO automation_runner VALUES(1,?,?)',(owner,time.time()+60));return True

    def get(self,key):
        with self.store.connect() as con:
            row=con.execute('SELECT document FROM automation_tasks WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        with self.store.connect() as con:rows=con.execute('SELECT document FROM automation_tasks ORDER BY rowid DESC').fetchall()
        return [json.loads(r[0]) for r in rows]

    def active(self):
        with self.store.connect() as con:
            rows=con.execute("SELECT document FROM automation_tasks WHERE json_extract(document,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')',tuple(ACTIVE)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def summaries(self):
        with self.store.connect() as con:
            rows=con.execute("SELECT json_remove(document,'$.token','$.request','$.packet','$.result','$.anchors','$.candidate','$.candidate_patch','$.last_report') FROM automation_tasks ORDER BY rowid DESC").fetchall()
        return [json.loads(r[0]) for r in rows]

    def events(self,key):
        with self.store.connect() as con:rows=con.execute('SELECT document FROM automation_events WHERE task_id=? ORDER BY seq',(key,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save(self,task,event):
        previous=task.get('revision',0);task=copy.deepcopy(task);task['revision']=previous+1;task['updated_at']=now()
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if previous:
                cursor=con.execute('UPDATE automation_tasks SET revision=?,document=? WHERE id=? AND revision=?',(task['revision'],canonical(task),task['id'],previous))
                if cursor.rowcount!=1:raise Conflict('自动化任务状态已改变')
            else:con.execute('INSERT INTO automation_tasks VALUES(?,?,?)',(task['id'],1,canonical(task)))
            con.execute('INSERT INTO automation_events VALUES(?,?,?)',(task['id'],task['revision'],canonical({'seq':task['revision'],'at':now(),'status':task['status'],**event})))
        return task


def public(task):
    return {k:v for k,v in task.items() if k not in ('token','request','packet')}


def rpc(method,payload,call_id):
    body={'type':'client-request','rpcId':call_id,'method':method,'payload':payload}
    req=urllib.request.Request(os.getenv('PE_DSH_URL','http://127.0.0.1:3080').rstrip('/')+'/api/'+method,data=canonical(body).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=20) as response:result=json.load(response)
    if not result.get('result',{}).get('ok'):raise RuntimeError(canonical(result.get('result',{}).get('error',result)))
    return result['result']['value']


def failure_signature(report):
    return digest({'stage':report.get('stage'),'errors':sorted((d.get('code',''),d.get('path','')) for d in report.get('diagnostics',[]))})


def original_contract_errors(base,candidate):
    """Report every change to frozen ports; never repair the candidate itself."""
    from .models import Module
    from .changes import physical_declaration
    try:
        before=physical_declaration(Module.model_validate(base).model_dump())
        after=physical_declaration(Module.model_validate(candidate).model_dump())
    except (ValueError,TypeError):return []
    errors=[]
    def compare(expected,actual,path):
        if isinstance(expected,dict) and isinstance(actual,dict):
            for key in sorted(expected.keys()|actual.keys()):
                compare(expected.get(key),actual.get(key),path+'/'+key)
        elif expected!=actual:
            errors.append({'code':'FROZEN_PORT_CONTRACT','path':path,
                           'message':f'恢复原声明：expected={canonical(expected)}，actual={canonical(actual)}；公式修改不得改变已有端口的物理契约'})
    for side in ('inputs','outputs'):
        for name,port in before[side].items():compare(port,after[side].get(name),'/module/'+side+'/'+name)
    return errors


def materialize_patch(packet,raw):
    from .models import ImplementationPatch,Module
    patch=ImplementationPatch.model_validate(raw)
    authorized=packet.get('authorized_added_ports',{})
    if len(set(patch.added_inputs))!=len(patch.added_inputs) or set(patch.added_inputs)!=set(authorized):
        raise ValueError('added_inputs 必须恰好列出已授权的新输入名称；其他契约变更需单独授权')
    base=copy.deepcopy(packet['base_module'])
    if set(authorized)&set(base['inputs']):raise ValueError('新输入不得覆盖原端口')
    base['inputs'].update(copy.deepcopy(authorized))
    base.update(version=packet['next_module_version'],implementation='python_function',
                source=patch.source,entrypoint=patch.entrypoint,adapter=patch.adapter.model_dump(by_alias=True),
                expressions={},builtin_id=None,builtin_hash=None,provenance=patch.provenance)
    if patch.description:base['description']=patch.description
    return Module.model_validate(base).model_dump(by_alias=True)


def request_observed(history,call_id):
    if isinstance(history,dict):return history.get('rpcId')==call_id or any(request_observed(v,call_id) for v in history.values())
    if isinstance(history,list):return any(request_observed(v,call_id) for v in history)
    return False


class Coordinator:
    def __init__(self,app,get_store,module_map,worker):
        self.app=app;self.get_store=get_store;self.module_map=module_map;self.worker=worker
        self.runner=None;self.stopping=False

    def journal(self):return Journal(self.get_store())

    async def internal(self,path,data):
        # Invoke the same typed endpoint used by the manual tools, without network or duplicating validation.
        for route in self.app.routes:
            if getattr(route,'path',None)==path and 'POST' in getattr(route,'methods',set()):
                import inspect
                annotation=inspect.signature(route.endpoint).parameters['req'].annotation
                args=annotation.model_validate(data)
                value=route.endpoint(args)
                return await value if inspect.isawaitable(value) else value
        raise RuntimeError('Internal route missing: '+path)

    async def apply(self,key,request):
        from .models import ChangeApplyRequest
        for route in self.app.routes:
            if getattr(route,'path',None)=='/api/change-tasks/{task_id}/apply':return await route.endpoint(key,ChangeApplyRequest.model_validate(request))
        raise RuntimeError('Apply route missing')

    def pause(self,t,status,message,**details):
        t.update(status=status,message=message,**details)
        return self.journal().save(t,{'event':'paused','message':message,'report':t.get('last_report')})

    def version_conflict(self,t):
        req=t['request'];current=self.get_store().workflow(t['workflow_id'])
        current_revision=current['revision'] if current else 0
        if current_revision==req['expected_revision']:return None
        before=locate(Workflow.model_validate(req['workflow']),t['node_path'])[1].model_dump()
        try:after=locate(Workflow.model_validate(current['workflow']),t['node_path'])[1].model_dump() if current else None
        except ValueError:after=None
        report={'stage':'prepare','diagnostics':[{'code':'REVISION_CONFLICT','path':'/expected_revision','message':f"任务引用 r{req['expected_revision']}，当前工作流为 r{current_revision}；重试旧版本不能解决冲突。"}],
                'expected_revision':req['expected_revision'],'current_revision':current_revision,'target_changed':before!=after,'target_before':before,'target_after':after}
        return self.pause(t,'version_changed','工作流版本冲突；请核对下方差异后读取最新版本重新准备。',last_report=report)

    def handle_error(self,t,exc):
        from fastapi import HTTPException
        if isinstance(exc,HTTPException):
            detail=exc.detail if isinstance(exc.detail,dict) else {'code':'HTTP_ERROR','message':str(exc.detail),'path':'/'}
            if detail.get('code')=='REVISION_CONFLICT':
                conflict=self.version_conflict(t)
                if conflict:return conflict
            return self.pause(t,'version_changed' if detail.get('code')=='STALE_CONTEXT' else 'failed',detail.get('message','平台执行失败'),last_report={'stage':'platform','diagnostics':[detail]})
        return self.pause(t,'failed','平台阶段执行失败；保留历史与原工作流。',last_report={'error':str(exc),'exception_type':type(exc).__name__})

    async def anchors(self,t):
        req=t['request'];wf=Workflow.model_validate(req['workflow']);graph,node=locate(wf,req['node_path']);base=self.module_map()[f'{node.module_id}@{node.module_version}']
        result=await asyncio.to_thread(self.worker,{'workflow':wf.model_dump(),'inputs':req['inputs'],'modules':[m.model_dump() for m in self.module_map().values()]},req['timeout_seconds'])
        if result['status']!='succeeded':raise ValueError('原工作流不能运行：'+canonical(result.get('diagnostics',[])))
        target='/'.join(p for p in req['node_path'] if p not in ('body','cases'))
        samples=[{'inputs':r['inputs'],'expected':r['outputs'],'tolerance':1e-8} for r in result['trace'] if '/'.join(p for p in r['path'].split('/') if not p.isdigit())==target]
        unique={digest(s['inputs']):s for s in samples}
        # Probe original module at one nearby valid point, never obtain reference answers from the candidate.
        if len(unique)<2 and samples:
            args=copy.deepcopy(samples[0]['inputs'])
            for bag,value in args.items():
                fields=value.items() if isinstance(value,dict) else [(None,value)]
                for field,v in list(fields):
                    if isinstance(v,(int,float)) and not isinstance(v,bool):
                        changed=copy.deepcopy(args)
                        if field is None:changed[bag]=v*1.05 if v else .01
                        else:changed[bag][field]=v*1.05 if v else .01
                        probe=Workflow(schema_version=wf.schema_version,id='reference_probe',name='冻结原模型参考',inputs=base.inputs,nodes=[{'id':'model','module_id':base.id,'module_version':base.version,'inputs':{k:{'source':'input','port':k} for k in base.inputs}}],outputs={k:{'source':'node','node':'model','port':k} for k in base.outputs},output_ports=base.outputs)
                        check=await asyncio.to_thread(self.worker,{'workflow':probe.model_dump(),'inputs':changed,'modules':[base.model_dump()]},req['timeout_seconds'])
                        if check['status']=='succeeded':unique[digest(changed)]={'inputs':changed,'expected':check['outputs'],'tolerance':1e-8}
                        if len(unique)>=2:break
                if len(unique)>=2:break
        return list(unique.values())[:12]

    async def prepare(self,t):
        conflict=self.version_conflict(t)
        if conflict:return conflict
        duplicate=await self.finish_if_current(t)
        if duplicate:return duplicate
        req=t['request'];anchors=req.get('anchors',[])
        if req['mode']=='interface':anchors=await self.anchors(t)
        if len(anchors)<2:
            return self.pause(t,'needs_evidence','缺少至少两组独立验收样例；追加修正次数不能替代验收依据。',questions={'anchors':'请提供至少两组覆盖全部模块输出的输入、预期结果及容差。'})
        data={k:v for k,v in req.items() if k not in ('source','mode','facts','source_policy','worker_protocol')};data['anchors']=anchors
        prepared=await self.internal('/api/change-tasks',data)
        if prepared.get('status')!='awaiting_candidate':return self.pause(t,'failed','基准任务准备失败',last_report=prepared)
        t.update(change_task_id=prepared['id'],packet=prepared['context'],anchors=anchors,status='dispatch_ready',message='上下文与验收依据已冻结，等待 DSH 生成候选。')
        return self.journal().save(t,{'event':'context_frozen','context_id':prepared['context_id']})

    async def finish_if_current(self,t):
        req=t['request']
        if req['mode']!='interface' or req.get('facts','').strip() or req.get('added_bindings'):return None
        current=self.get_store().workflow(t['workflow_id'])
        if not current or current['revision']!=req['expected_revision']:return None
        node=locate(Workflow.model_validate(req['workflow']),t['node_path'])[1]
        module=self.module_map()[f'{node.module_id}@{node.module_version}']
        if module.source.strip()!=req['source'].strip():return None
        was_active=t['status'] in ('creating_session','dispatching','awaiting_worker')
        for old in self.journal().all():
            result=old.get('result')
            if old['status']=='applied' and result and digest(result['workflow'])==digest(req['workflow']) and digest(result['inputs'])==digest(req['inputs']):
                t['result']=result;break
        t.update(status='already_current',message=f'上传函数已是当前 {module.id}@{module.version} 的实现，无需再次生成或发布。如需测试另一种接口，请上传不同的函数。',last_report={'stage':'deduplication','diagnostics':[{'code':'ALREADY_CURRENT','message':'当前节点已使用同一源码；未创建新的模块或工作流版本。','path':'/source'}]})
        t=self.journal().save(t,{'event':'already_current','module_ref':f'{module.id}@{module.version}'})
        if was_active:
            try:
                await asyncio.to_thread(rpc,'session.cancel',{'sessionId':t['session_id']},t['call_id']+'-cancel-duplicate')
                t['duplicate_call_cancelled']=True
            except Exception as exc:t['cancellation_error']=str(exc)
            t=self.journal().save(t,{'event':'duplicate_call_cancel','accepted':t.get('duplicate_call_cancelled',False)})
        return t

    async def monitor(self,t):
        try:
            history=await asyncio.to_thread(rpc,'session.history',{'sessionId':t['session_id'],'maxMessages':3},t['call_id']+'-progress')
            events=[r.get('event',{}) for r in history.get('events',[])]
            context_read=any(e.get('type')=='tool/call' and e.get('data',{}).get('name')=='flux_worker_context' for e in events)
            chunks=sum(e.get('type')=='assistant/chunk' for e in events)
            stage='已读取任务上下文，正在生成适配候选' if context_read else '模型正在输出' if chunks else '等待模型响应'
            progress={'checked_at':now(),'checked_epoch':time.time(),'stage':stage,'observed_chunks':chunks,'online':True}
        except Exception as exc:progress={'checked_at':now(),'checked_epoch':time.time(),'stage':'暂时无法读取 DSH 状态；不自动重发','online':False,'error':str(exc)}
        current=self.journal().get(t['id'])
        if current['status']=='awaiting_worker' and current.get('call_id')==t['call_id']:
            current['progress']=progress;return self.journal().save(current,{'event':'worker_progress','progress':progress})

    def worker_packet(self,t):
        packet=copy.deepcopy(t['packet'])
        # Freeze the complete packet for audit, but send only relevant catalog rows to the model.
        names=set()
        def collect(value):
            if isinstance(value,dict):
                ref=value.get('catalog_ref')
                if ref:names.add(ref['name'])
                for child in value.values():collect(child)
            elif isinstance(value,list):
                for child in value:collect(child)
        collect(packet['base_module'])
        packet['parameter_catalogs']=[{**c,'rows':[r for r in c['rows'] if r['name'] in names], 'selection':'current module catalog references; original row_count is total catalog size'} for c in packet['parameter_catalogs']]
        packet.pop('registry',None)
        packet.pop('baseline',None)
        packet['evaluation_cases']=[{'name':c['name'],'added_bindings':c['added_bindings'],'tolerance':c['tolerance'],'expected':'Platform-held full-workflow reference, not supplied to generator.'} for c in packet.get('evaluation_cases',[])]
        if packet['base_module'].get('implementation')=='trusted_builtin':
            samples=packet.get('target_samples',[])
            if len(samples)>12:
                indexes=sorted({round(i*(len(samples)-1)/11) for i in range(12)})
                packet['target_samples']=[samples[i] for i in indexes]
            packet['trusted_builtin_adaptation']={
                'rule':'The base is platform-owned scientific code. Return a new python_function module with builtin_id=null and builtin_hash=null. Never invent or modify a trusted builtin entry.',
                'scope':'Adapt only the selected scalar function. Batch traversal and independent downstream constraint nodes stay unchanged.',
                'rejection':'valid=0 identifies an inert rejected candidate. Preserve this flag and all declared output fields; padding is not a physically valid zero-loss calculation.',
                'sample_selection':f'{len(packet.get("target_samples",[]))} representative samples from {len(samples)} frozen executions; acceptance anchors remain separate.'}
        packet['acceptance_label']='冻结原模型在有效输入下产生的参考结果；仅用于接口等价回归' if t['request']['mode']=='interface' else packet['acceptance_label']
        packet.update(task_kind='automated_upload_adaptation',source=t['request']['source'],uploaded_source=t['request']['source'],mode=t['request']['mode'],facts=t['request'].get('facts',''),answers=t.get('answers',{}),latest_user_supplement=t.get('last_supplement'),last_report=t.get('last_report'),previous_candidate=t.get('candidate'),attempt=t['attempt'])
        if packet.get('last_report'):
            packet['last_report']={k:v for k,v in packet['last_report'].items() if k in ('status','stage','diagnostics','error')}
        if t['request'].get('source_policy')=='modify' and t.get('candidate'):
            packet['original_contract_differences']=original_contract_errors(packet['base_module'],t['candidate'])
        packet['adapter_format']={
            'rule':'arguments and returns MUST be objects, never arrays. Each value is a Mapping with exactly one source: from path or assemble object. Argument keys are Python parameter names; return keys are external output port names. Every argument path MUST begin with inputs; every return path MUST begin with return. Do not add port or source keys.',
            'whole_dictionary_example':{'arguments':{'function_parameter':{'from':['inputs','external_input_port']}},'returns':{'external_output_port':{'from':['return','returned_dictionary_key']}}},
            'nested_and_units_example':{'arguments':{'nested_parameter':{'assemble':{'frequency_khz':{'from':['inputs','electrical','fs'],'conversion':{'from_unit':'Hz','to_unit':'kHz'}}}}},'returns':{'loss':{'assemble':{'power':{'from':['return',0,'power_mw'],'conversion':{'from_unit':'mW','to_unit':'W'}}}}}}}
        # No arbitrary project files or hidden evaluator answers are exposed through worker tools.
        packet['rules'].update(response='Call flux_worker_result exactly once with candidate, questions, or blocked. Never register, apply or run.',source='Preserve uploaded source exactly; adapt with explicit adapter. Unsupported source must be reported, never silently change physics.',uncertainty='Batch all missing semantics once. Still unresolved after answers: return blocked. Latest_user_supplement is newer evidence than historical unknown answers or pending source documentation; explicit confirmation can resolve uncertainty, but cannot authorize changing the target node.',publication='Only the platform owns validation and publication.')
        if t['request'].get('source_policy')=='modify':
            packet['source_policy']='modify'
            packet['task_kind']='selected_formula_version_derivation'
            packet['rules']['source']='User explicitly authorized formula modification. Uploaded source is a baseline reference, not immutable candidate source. Write a new controlled pure Python function implementing the stated new formula; publish only through the platform as the specified new version. Preserve all unrelated physics and invalid-candidate guards.'
        if t['request'].get('worker_protocol')=='implementation_patch_v1':
            from .models import ImplementationPatch
            # The audit packet remains complete. The generator receives only the
            # selected interface, evidence and diagnostics, not the full graph.
            def compact(value):
                if isinstance(value,dict):return {k:compact(v) for k,v in value.items() if v is not None and v!='' and v!={} and v!=[]}
                if isinstance(value,list):return [compact(v) for v in value]
                return value
            for key in ('workflow','inputs','candidate_schema','parameter_definitions','naming_rules','source','template_specification','original_contract_differences'):packet.pop(key,None)
            packet['base_module']=compact(packet['base_module'])
            packet['target_samples']=packet.get('target_samples',[])[:3]
            packet['worker_protocol']='implementation_patch_v1'
            packet['patch_schema']=ImplementationPatch.model_json_schema(by_alias=True)
            packet['previous_patch']=t.get('candidate_patch')
            packet.pop('previous_candidate',None)
            packet['authorized_added_ports']=compact(packet.get('authorized_added_ports',{}))
            packet['rules']['response']='Submit kind=candidate with patch, never module. Only source, entrypoint, adapter, provenance, optional description, added_inputs are allowed. The platform copies frozen original contracts and the exact authorized new ports, assigns id/version and lineage. Contract changes require a separately authorized task; do not submit inputs/outputs/id/version in patch.'
            packet['execution_plan']=['Generate the bounded implementation patch','Platform constructs candidate from frozen contracts','Platform validates, runs all reference cases, then publishes atomically']
            packet.pop('trusted_builtin_adaptation',None)
        return packet

    async def dispatch(self,t):
        t.update(generation_count=t.get('generation_count',0)+1,status='creating_session',call_id=uuid.uuid4().hex,token=secrets.token_urlsafe(32),session_id='flux-'+uuid.uuid4().hex,dispatch_at=time.time(),message='正在创建专用 DSH 会话并提交任务。')
        t=self.journal().save(t,{'event':'dispatch_intent','call_id':t['call_id'],'attempt':t['attempt']})
        try:
            await asyncio.to_thread(rpc,'session.create',{'sessionId':t['session_id'],'agentPreset':'flux-worker'},t['call_id']+'-create')
            t.update(status='dispatching');t=self.journal().save(t,{'event':'session_created','session_id':t['session_id']})
            prompt='Execute one Flux worker task. First call flux_worker_context, then flux_worker_result. Do not guess missing evidence. Task credentials: '+canonical({'task_id':t['id'],'call_id':t['call_id'],'token':t['token']})
            await asyncio.to_thread(rpc,'session.prompt',{'sessionId':t['session_id'],'mode':'queue','content':[{'type':'text','text':prompt}]},t['call_id'])
            current=self.journal().get(t['id'])
            if current['status']=='dispatching':
                current.update(status='awaiting_worker',message='DSH 正在生成适配候选；平台等待结构化回传。');self.journal().save(current,{'event':'prompt_accepted'})
        except Exception as exc:
            current=self.journal().get(t['id'])
            if current['status'] in ('creating_session','dispatching'):self.pause(current,'delivery_unknown','DSH 调用未确认完成；不会自动重发。可核对会话后批准重试。',last_report={'error':str(exc)})

    async def validate(self,t):
        candidate=t.get('candidate',{});packet=t['packet']
        from pydantic import ValidationError
        from .models import Module
        schema_errors=None
        if t['request'].get('worker_protocol')=='implementation_patch_v1':
            try:
                if t.get('candidate_protocol_error'):raise ValueError(t['candidate_protocol_error'])
                candidate=materialize_patch(packet,t.get('candidate_patch'))
                t['candidate']=candidate
                t=self.journal().save(t,{'event':'patch_materialized','patch_hash':digest(t['candidate_patch']),'candidate_hash':digest(candidate)})
            except (ValidationError,ValueError,TypeError) as exc:
                schema_errors=[{'code':'PATCH_CONTRACT','path':'/patch','message':str(exc)}]
        try:
            if not schema_errors:Module.model_validate(candidate)
        except ValidationError as exc:schema_errors=[{'code':'SCHEMA_ERROR','path':'/module/'+('/'.join(str(p) for p in e['loc'])),'message':e['msg']} for e in exc.errors()]
        # Formula authors need all independently detectable errors together,
        # rather than spending one repair round on each mechanical layer.
        formula_errors=[]
        if t['request'].get('source_policy')=='modify' and (not schema_errors or t['request'].get('worker_protocol')!='implementation_patch_v1'):
            from .expressions import analyze_source
            from .models import Port
            from .changes import physical_declaration
            formula_errors.extend(original_contract_errors(packet['base_module'],candidate))
            if candidate.get('implementation') in ('python_function','python_subset') and isinstance(candidate.get('source'),str):
                analysis=analyze_source(candidate['source'],candidate.get('entrypoint'))
                if not analysis.get('supported'):
                    formula_errors.extend([{**d,'code':'CONTROLLED_SOURCE_SYNTAX','path':'/module/source'} for d in analysis.get('diagnostics',[])])
            for key,expected in packet.get('authorized_added_ports',{}).items():
                try:
                    actual=Port.model_validate(candidate.get('inputs',{}).get(key,{})).model_dump()
                    if physical_declaration(actual)!=physical_declaration(expected):
                        formula_errors.append({'code':'ADDED_INPUT_CONTRACT','path':'/module/inputs/'+key,'message':f'请完整复制 authorized_added_ports.{key} 的物理声明（含 unit_definition），不允许改变字段名、单位或语义'})
                except (ValidationError,TypeError,ValueError):
                    formula_errors.append({'code':'ADDED_INPUT_CONTRACT','path':'/module/inputs/'+key,'message':f'缺少或无法解析已声明的新输入 {key}'})
        if formula_errors:
            schema_errors=(schema_errors or [])+formula_errors
        if schema_errors:
            report={'status':'rejected','stage':'schema','diagnostics':schema_errors}
        elif t['request'].get('source_policy','preserve')=='preserve' and candidate.get('source','').strip()!=t['request']['source'].strip():
            report={'status':'rejected','stage':'source','diagnostics':[{'code':'UPLOADED_SOURCE_CHANGED','path':'/module/source','message':'上传适配须保留原函数；只允许通过 adapter 改写接口'}]}
        else:
            t.update(status='applying',message='正在检查候选、验收样例和完整工作流；通过后原子应用。');t=self.journal().save(t,{'event':'validation_started','candidate_hash':digest(candidate)})
            try:report=await self.apply(t['change_task_id'],{'context_id':self.get_store().task(t['change_task_id'])['context_id'],'module':candidate,'added_bindings':packet['authorized_added_bindings']})
            except Exception as exc:
                from fastapi import HTTPException
                details=exc.detail if isinstance(exc,HTTPException) else {'code':'INTERNAL_VALIDATION','message':str(exc),'path':'/'}
                report={'status':'rejected','stage':'platform','diagnostics':[details]}
        t=self.journal().get(t['id']);t['last_report']=report
        if report.get('status')=='applied':
            t.update(status='applied',result={k:report[k] for k in ('workflow','inputs','revision','run')},message='候选与完整工作流通过验证，已原子发布。')
        else:
            codes={d.get('code') for d in report.get('diagnostics',[])}
            if codes & {'REVISION_CONFLICT','STALE_CONTEXT'}:
                return self.pause(t,'version_changed','上下文已改变，候选保留；读取最新版本重新验证后才能应用。')
            signature=failure_signature(report);t['repeat_count']=t.get('repeat_count',0)+1 if t.get('failure_signature')==signature else 1;t['failure_signature']=signature
            if t['repeat_count']>=2 or t['repairs_used']>=t['repair_limit']:
                t.update(status='paused',message='修正预算耗尽或同类错误连续出现两次；旧工作流保持不变。')
            else:t.update(status='dispatch_ready',attempt=t['attempt']+1,repairs_used=t['repairs_used']+1,message='候选未通过，平台已将诊断交给下一轮 DSH 修正。')
        return self.journal().save(t,{'event':'candidate_checked','report':report,'attempt':t['attempt']})

    async def recover(self,t):
        if t['status'] in ('creating_session','dispatching','awaiting_worker','validating','applying'):
            duplicate=await self.finish_if_current(t)
            if duplicate:return duplicate
        if t['status'] in ('applying','validating'):
            # Existing change application is idempotent and checks committed task first.
            return await self.validate(t)
        if t['status']=='preparing':return await self.prepare(t)
        if t['status'] in ('creating_session','dispatching','awaiting_worker'):
            try:
                history=await asyncio.to_thread(rpc,'session.history',{'sessionId':t['session_id'],'maxMessages':100},t['call_id']+'-reconcile')
                if request_observed(history,t['call_id']) and time.time()-t['dispatch_at']<600:
                    t.update(status='awaiting_worker');return self.journal().save(t,{'event':'delivery_observed'})
                return self.pause(t,'delivery_unknown','未能确定调用完成状态，或已等待超过十分钟；保留会话，用户决定是否重试。')
            except Exception as exc:return self.pause(t,'delivery_unknown','无法核对 DSH 会话；不会自动重复生成。',last_report={'error':str(exc)})

    async def step(self,t):
        if t['status']=='queued':
            t.update(status='preparing');t=self.journal().save(t,{'event':'analysis_started'});await self.prepare(t)
        elif t['status']=='dispatch_ready':await self.dispatch(t)
        elif t['status']=='validating':await self.validate(t)

    async def loop(self):
        owner=uuid.uuid4().hex;journal=self.journal()
        while not self.stopping and not journal.lease(owner):await asyncio.sleep(1)
        runner=asyncio.current_task()
        async def heartbeat():
            while not self.stopping:
                await asyncio.sleep(10)
                try:
                    if not journal.lease(owner):runner.cancel();return
                except Exception:runner.cancel();return
        pulse=asyncio.create_task(heartbeat())
        try:await self.run_loop()
        finally:
            pulse.cancel()
            with suppress(asyncio.CancelledError):await pulse
            journal.lease(owner,release=True)

    async def run_loop(self):
        for t in self.journal().active():
            if t['status'] in ACTIVE and t['status'] not in ('queued','dispatch_ready'):
                try:await self.recover(t)
                except Conflict:pass
                except Exception as exc:
                    current=self.journal().get(t['id'])
                    if current and current['status'] in ACTIVE:self.handle_error(current,exc)
        ticks=0
        while not self.stopping:
            for t in self.journal().active():
                try:
                    if t['status'] in ('queued','dispatch_ready','validating'):await self.step(t)
                    elif t['status']=='awaiting_worker' and ticks%20==0 and time.time()-t['dispatch_at']>600:await self.recover(t)
                    elif t['status']=='awaiting_worker' and time.time()-t.get('progress',{}).get('checked_epoch',0)>15:await self.monitor(t)
                except Conflict:pass
                except Exception as exc:
                    current=self.journal().get(t['id'])
                    if current and current['status'] in ACTIVE:self.handle_error(current,exc)
            ticks+=1;await asyncio.sleep(1)


def install(app,get_store,module_map,worker,problem):
    coordinator=Coordinator(app,get_store,module_map,worker);app.state.automation=coordinator
    def journal():return coordinator.journal()
    def task(key):
        t=journal().get(key)
        if not t:problem('TASK_NOT_FOUND','自动化任务不存在',404)
        return t
    def authorized(key,call_id,token):
        t=task(key)
        if t.get('call_id')!=call_id or not secrets.compare_digest(t.get('token',''),token):problem('WORKER_CREDENTIAL','任务或调用凭证不匹配',403)
        return t

    @asynccontextmanager
    async def lifespan(app):
        coordinator.stopping=False
        if os.getenv('PE_DSH_ENABLED','1')=='0':
            yield
            return
        coordinator.runner=asyncio.create_task(coordinator.loop())
        try:yield
        finally:
            coordinator.stopping=True
            coordinator.runner.cancel()
            with suppress(asyncio.CancelledError):await coordinator.runner
    app.router.lifespan_context=lifespan

    @app.get('/api/automation-tasks')
    def list_tasks():return journal().summaries()

    @app.post('/api/automation-tasks')
    def create(req:AutomationRequest):
        if os.getenv('PE_DSH_ENABLED','1')=='0':problem('DSH_DISABLED','DSH 未启用；请配置本地服务并重启平台',503)
        from .expressions import analyze_source
        analysis=analyze_source(req.source)
        if not analysis.get('supported'):problem('SOURCE_UNSUPPORTED',canonical(analysis))
        graph,node=locate(req.workflow,req.node_path)
        if module_map()[f'{node.module_id}@{node.module_version}'].contract_version!='3.0':problem('AUTOMATION_V3','自动上传首版只接受 v3 字典模块')
        t={'id':uuid.uuid4().hex,'status':'queued','created_at':now(),'request':req.model_dump(),'workflow_id':req.workflow.id,'node_path':req.node_path,'instruction':req.instruction,'mode':req.mode,'attempt':0,'generation_count':0,'repairs_used':0,'repair_limit':3,'batch':1,'clarification_used':False,'message':'等待分析与冻结上下文'}
        return public(journal().save(t,{'event':'created','source_hash':digest(req.source),'analysis':analysis}))

    @app.get('/api/automation-tasks/{key}')
    def read(key:str):return {**public(task(key)),'events':journal().events(key)}

    @app.get('/api/automation-tasks/{key}/context')
    def context(key:str,call_id:str,authorization:str=Header(default='')):
        t=authorized(key,call_id,authorization.removeprefix('Bearer '))
        if t['status'] not in ('dispatching','awaiting_worker','delivery_unknown'):problem('WORKER_STATE','本次调用已结束',409)
        return coordinator.worker_packet(t)

    @app.post('/api/automation-tasks/{key}/result')
    def result(key:str,req:AutomationResult):
        t=authorized(key,req.call_id,req.token);payload=req.model_dump(exclude={'token'});hash_=digest(payload)
        if t.get('callback_hash')==hash_:return {'status':'accepted','duplicate':True}
        # A timeout leaves this call uncertain, not cancelled. Its credential
        # remains valid until a replacement call rotates it.
        if t['status'] not in ('dispatching','awaiting_worker','delivery_unknown'):problem('CALL_FINISHED','本轮已结束或存在不同结果',409)
        t.update(callback_hash=hash_,explanation=req.explanation)
        if req.kind=='questions':
            if t['clarification_used'] or not req.questions:t.update(status='blocked',message='集中追问后仍存在关键语义缺口；补充依据后才能继续。',questions=req.questions)
            else:t.update(status='needs_clarification',clarification_used=True,questions=req.questions,message='请集中补充关键语义')
        elif req.kind=='blocked':t.update(status='blocked',message=req.explanation or '智能体报告关键依据不足',questions=req.questions)
        else:
            if t['request'].get('worker_protocol')=='implementation_patch_v1':
                t.update(status='validating',candidate={},candidate_patch=req.patch,
                         candidate_protocol_error='请把实现补丁放在工具参数顶层 patch，不是 module；本协议禁止提交 module' if req.module is not None else None)
            else:
                if req.module is None:problem('CANDIDATE_REQUIRED','缺少候选模块')
                t.update(status='validating',candidate=req.module)
        journal().save(t,{'event':'worker_result','call_id':req.call_id,'payload':payload})
        return {'status':'accepted'}

    @app.post('/api/automation-tasks/{key}/answer')
    def answer(key:str,req:AutomationAnswer):
        t=task(key)
        if t['status'] not in ('needs_evidence','needs_clarification','blocked'):problem('ANSWER_STATE','当前阶段不接收语义补充',409)
        if t['status']=='needs_clarification' and (req.answers.keys()!=t.get('questions',{}).keys() or any(not v.strip() for v in req.answers.values())):problem('ANSWER_REQUIRED','请回答本轮全部问题')
        if t['status']=='blocked':
            known=set(t.get('questions',{}))|set(t.get('answers',{}))
            if set(req.answers)-known or any(not v.strip() for v in req.answers.values()):problem('ANSWER_REQUIRED','补充回答必须对应已提出的问题，或在补充依据中填写新事实')
            changed=any(value.strip()!=t.get('answers',{}).get(key,'').strip() for key,value in req.answers.items())
            if not req.facts.strip() and not req.anchors and not changed:problem('SUPPLEMENT_REQUIRED','请提供新增依据或修正回答；原有“无法确认”回答不能直接作为新的确认依据')
        if t['status']=='needs_evidence' and len(req.anchors)<2:problem('ACCEPTANCE_REQUIRED','至少两组独立样例')
        t['last_supplement']={'at':now(),'answers':req.answers,'facts':req.facts,'anchor_count':len(req.anchors)}
        t['answers']={**t.get('answers',{}),**req.answers}
        if req.facts:t['request']['facts']+='\n'+req.facts
        if req.anchors:t['request']['anchors']=[a.model_dump() for a in req.anchors]
        t.update(status='queued' if not t.get('packet') else 'dispatch_ready',callback_hash=None)
        return public(journal().save(t,{'event':'user_answer','answers':req.answers,'facts':req.facts}))

    @app.post('/api/automation-tasks/{key}/continue')
    def resume(key:str,req:AutomationContinue):
        t=task(key)
        if not req.approved:problem('APPROVAL_REQUIRED','需用户明确批准追加尝试')
        if t['status'] not in ('paused','delivery_unknown','version_changed','failed'):problem('CONTINUE_STATE','请先补齐关键依据或等待当前执行完成',409)
        if t['status']=='delivery_unknown' and not req.retry_uncertain:problem('DELIVERY_UNKNOWN','需明确同意重试送达状态不明的请求')
        if t['status']=='version_changed':
            if not req.refresh_version:problem('REFRESH_REQUIRED','需先读取最新工作流核对差异')
            current=get_store().workflow(t['workflow_id'])
            if current:
                # Changes to the target itself need a fresh user task; preserve candidate safely.
                old_graph,old_node=locate(Workflow.model_validate(t['request']['workflow']),t['node_path'])
                new_graph,new_node=locate(Workflow.model_validate(current['workflow']),t['node_path'])
                if old_node.model_dump()!=new_node.model_dump():
                    if not req.accept_target_change:problem('TARGET_CHANGED','目标节点已更新，需核对并明确确认使用最新目标；原任务和候选保留',409)
                    old_data=old_node.model_dump();new_data=new_node.model_dump()
                    old_data.pop('module_version');new_data.pop('module_version')
                    if old_data!=new_data:problem('TARGET_REWIRED','目标模块族、接线或结构已改变，请根据新意图创建任务',409)
                t['request'].update(workflow=current['workflow'],inputs=current['inputs'],expected_revision=current['revision'])
        t.update(batch=t['batch']+1,repairs_used=0,repeat_count=0,callback_hash=None,status='queued' if t['status'] in ('version_changed','failed') or not t.get('packet') else 'dispatch_ready')
        t['attempt']+=1
        return public(journal().save(t,{'event':'user_approved_continue','batch':t['batch']}))
    return coordinator
