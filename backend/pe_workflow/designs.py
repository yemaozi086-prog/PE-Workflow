"""Requirement-first design. Agent proposes; platform validates and publishes drafts.
No operating-point values are requested until the saved workflow is run.
"""
import asyncio,copy,json,os,secrets,time,uuid,re
from contextlib import asynccontextmanager,suppress
from pathlib import Path
from fastapi import Header
from .models import (DesignRequest,DesignRevision,DesignConfirm,DesignAnswer,DesignWorkerResult,
                     DesignPlan,FormulaRequest,Module,Workflow,Node,Binding,DesignContinue,Port,ExtensionProposal,ExtensionDecision)
from .storage import ROOT,canonical,digest,now,Conflict
from .engine import fail,validate_workflow,check_module,check_port,fill_inputs,validate_value,WorkflowError
from .automation import rpc
from .evaluation import equivalent
from . import numeric
from .design_guidance import adapter_repair
from .dependency_audit import audit_dependencies

PRESET=ROOT/'dsh/preset/flux-design'
ACTIVE={'dispatch_ready','dispatching','awaiting_worker'}

def check_examples(examples,inputs,outputs):
    inputs={k:Port.model_validate(v) if isinstance(v,dict) else v for k,v in inputs.items()}
    outputs={k:Port.model_validate(v) if isinstance(v,dict) else v for k,v in outputs.items()}
    seen={}
    for index,example in enumerate(examples):
        ex=example.model_dump() if hasattr(example,'model_dump') else example
        path=f'/examples/{index}'
        try:
            fill_inputs(ex['inputs'],inputs,path+'/inputs')
            if set(ex['outputs'])!=set(outputs):
                fail('EXAMPLE_OUTPUTS','预期结果必须覆盖全部输出字典',path+'/outputs')
            for key,port in outputs.items():validate_value(ex['outputs'][key],port,path+'/outputs/'+key)
            normalized=fill_inputs(ex['inputs'],inputs,path+'/inputs')
            fingerprint=canonical(normalized)
            if fingerprint in seen:
                first,expected=seen[fingerprint]
                if not equivalent(ex['outputs'],expected):fail('EXAMPLE_CONTRADICTION',f'第 {index+1} 组与第 {first+1} 组输入完全相同，但预期结果不同；请核对输入或预期结果，不要让智能体修改代码',path+'/outputs')
            else:seen[fingerprint]=(index,ex['outputs'])
        except WorkflowError as error:
            fail('EXAMPLE_DATA_INVALID',f'第 {index+1} 组验证样例未通过预检查：{error.diagnostic.message}；请修正样例，不需要让智能体修改代码',error.diagnostic.path,suggestion='修正或移除这组样例后重新提交；尚未追加智能体调用')

def configuration(limit=4):
    return {'prompt':(PRESET/'agent.cordis.yml').read_text(encoding='utf-8'),'preset':'flux-design-v2',
            'prompt_hash':digest((PRESET/'agent.cordis.yml').read_text(encoding='utf-8')),
            'tool_hash':digest((PRESET/'flux-design-tools.js').read_text(encoding='utf-8')),
            'provider_hash':digest({'provider':Path(__file__).read_text(encoding='utf-8'),'guidance':(Path(__file__).parent/'design_guidance.py').read_text(encoding='utf-8'),'dependency_audit':(Path(__file__).parent/'dependency_audit.py').read_text(encoding='utf-8')}),
            'engine_hash':digest((Path(__file__).parent/'engine.py').read_text(encoding='utf-8')),
            'max_calls':limit,'model':'DSH configured model; unknown to platform'}

def check_dependencies(wf,plan,registry):
    expected={s.id:set(s.dependencies) for s in plan.steps}
    def graph(g,environment):
        nodes={n.id:n for n in g.nodes};memo={}
        def resolve(b):
            if b.source=='input':return environment.get(b.port,set())
            if b.source=='node':return outputs(b.node).get(b.port,set())
            return set()
        def outputs(key):
            if key in memo:return memo[key]
            n=nodes[key]
            if n.kind=='compute':
                deps=set().union(*(resolve(b) for b in n.inputs.values()))
                if deps!=expected.get(n.id,set()):raise ValueError('实际接线依赖与确认方案不一致：'+n.id)
                result={k:{n.id} for k in registry[f'{n.module_id}@{n.module_version}'].outputs}
            elif n.body:
                env={k:resolve(n.inputs['rows' if n.kind=='batch' else 'items'] if k=='item' else n.inputs[k]) for k in n.body.inputs}
                inner=graph(n.body,env)
                result=inner if n.kind=='batch' else {'results':set().union(*inner.values())}
            elif n.cases:
                result={}
                for case in n.cases.values():
                    for port,deps in graph(case,{k:resolve(n.inputs[k]) for k in case.inputs}).items():result.setdefault(port,set()).update(deps)
            else:result={'value':set().union(*(resolve(b) for b in n.inputs.values()))}
            memo[key]=result;return result
        for key in nodes:outputs(key)
        return {k:resolve(b) for k,b in g.outputs.items()}
    graph(wf,{})

class Designs:
    def __init__(self,store,modules,worker):self.store=store;self.modules=modules;self.worker=worker
    def connect(self):
        store=self.store()
        with store.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS workflow_designs(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL)')
        return store.connect()
    def get(self,key):
        with self.connect() as c:r=c.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()
        if not r:fail('DESIGN_NOT_FOUND','计算方案不存在','/design')
        return json.loads(r[0])
    def all(self,active_only=False):
        query="SELECT document FROM workflow_designs"+(" WHERE json_extract(document,'$.status') IN ('dispatch_ready','dispatching','awaiting_worker')" if active_only else '')+" ORDER BY rowid DESC"+('' if active_only else ' LIMIT 100')
        with self.connect() as c:return [json.loads(r[0]) for r in c.execute(query)]
    def save(self,t,event):
        t=copy.deepcopy(t);old=t.get('revision',0);t['revision']=old+1;t['updated_at']=now()
        t.setdefault('events',[]).append({'at':now(),'actor':'agent' if event.startswith('agent_result') else 'user' if event.startswith('user_') else 'platform','phase':t.get('phase'),'event':event,'status':t['status'],'revision':t['revision'],'report':copy.deepcopy(t.get('last_report')) if event=='validation_failed' else None})
        with self.connect() as c:
            if old:
                r=c.execute('UPDATE workflow_designs SET revision=?,document=? WHERE id=? AND revision=?',(t['revision'],canonical(t),t['id'],old))
                if r.rowcount!=1:raise Conflict('方案已更新，请刷新后重试')
            else:c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(t['id'],1,canonical(t)))
        return t
    def public(self,t):
        result={k:v for k,v in t.items() if k not in ('token','frozen_modules','catalog_snapshot')}
        result.pop('extension_context',None)
        if t.get('extension_request_id'):result['extension_request']=self.extensions.read(t['extension_request_id'])
        return result
    def check_revision(self,t,rev):
        if t['revision']!=rev:raise Conflict('方案版本已改变，请核对最新版本')
    def context(self,t):
        registry=self.modules()
        snapshot=t.get('frozen_modules') or (self.store().context(t['catalog_context_id'])['modules'] if t.get('catalog_context_id') else t.get('catalog_snapshot')) or [m.model_dump() for m in registry.values()]
        catalog=[{k:m.get(k) for k in ('id','version','name','description','capabilities','applies_to')}|{'input_dictionaries':list(m['inputs']),'output_dictionaries':list(m['outputs'])} for m in snapshot]
        formula_request=copy.deepcopy(t.get('formula_request'))
        if formula_request is not None:
            formula_request['verification_inputs']=[ex['inputs'] for ex in formula_request.pop('examples',[])]
        return {'protocol':'workflow_design_v1','phase':t['phase'],'request':t['request'],
            'extension_catalog':t.get('extension_context',self.extensions.catalog(t['request'].get('project_id','local'))),
            'extension_proposal_schema':ExtensionProposal.model_json_schema(),
            'plan':t.get('plan'),'answers':t.get('answers',{}),'formula_request':formula_request,'registration_target':t.get('registration_target'),
            'last_report':t.get('last_report'),'dependency_audit':t.get('dependency_audit'),'previous_candidate':t.get('previous_candidate'),
            'required_result_kind':{'plan':'plan','bind':'workflow','formula':'module'}[t['phase']],
            'stage_instruction':{'plan':'只提出计算方案；不索取运行数值。','bind':'模块已登记，用户已确认方案。仅提交 Workflow；不要再次生成或提交 Module。','formula':'按用户公式生成 Module 候选；不要编排工作流。'}[t['phase']],
            'concrete_protocol_repair':adapter_repair(t.get('previous_candidate')) if t['phase']=='formula' and t.get('last_report') else None,
            'explicitly_mentioned_modules':[m for m in snapshot if re.search(r'(?<![A-Za-z0-9_])'+re.escape(f'{m["id"]}@{m["version"]}')+r'(?![0-9])',t['request']['instruction'])] if t['phase']=='plan' else [],
            'modules':catalog if t['phase']=='plan' else snapshot if t['phase']=='bind' else [],
            'environment':{'status':'not_installed','ref':t['request']['environment_ref'],'interface_version':'environment_package_v1',
                'allow_external_suggestions':t['request']['allow_external_suggestions']},
            'adapter_examples':{'notes':'Structural examples only, not a physical model. Omit unused properties; never use from:null. Apply supplied field names and declarations.', 'dict_return':{'arguments':{'internal_arg':{'from':['inputs','external_bag']}},'returns':{'external_output':{'from':['return']}}},'scalar_return':{'arguments':{'internal_arg':{'from':['inputs','external_bag','field']}},'returns':{'external_output':{'assemble':{'field':{'from':['return']}}}}}},
            'implementation_protocols':{'python_subset':{'signature':'def compute(inputs):','reads':'inputs[bag][field]','returns':'complete output dictionary, e.g. {loss: {P_cond: value}}','adapter':'not used; do not supply multiple function parameters'},'python_function':{'signature':'one named function with declared parameters','requires':'entrypoint and explicit adapter.arguments/returns','example_signature':'def compute(electrical, device):','note':'Multiple parameters require this implementation type, never python_subset'}},
            'controlled_syntax':{'python':'one function, assignments, return, if; no imports, comprehensions, loops or helper definitions; use bare pi, sqrt, sin, cos, abs; nested dict extraction allowed by adapter','numeric_functions':sorted(numeric.FUNCTIONS)},
            'schemas':{('plan' if t['phase']=='plan' else 'workflow' if t['phase']=='bind' else 'module'): (DesignPlan if t['phase']=='plan' else Workflow if t['phase']=='bind' else Module).model_json_schema()},
            'rules':['Plan first; no numerical input values required. Never assume the old DBSRC chain matches the request.',
                'No environment package is installed. Every physical dependency needs cited user or module evidence; uncertain assumptions must be visible.',
                'If external suggestions are disabled, use only user-provided facts and registered module declarations; ask questions when insufficient.',
                'Plans may contain missing tools (module_ref=null). No invented physical model. Module refs are id@version.',
                'After code generation, the platform audits literal field reads against required flags. Dynamic dictionary accesses block automatic registration. All repairs must preserve confirmed required/optional flags and units; interface changes require a user-derived plan. Return python_subset outputs nested under declared output port names.',
                'For NEW or missing tools declare only the fields actually consumed by each step. Fields merely carried in a shared dictionary must be omitted from that step contract or explicitly required=false. Do not require all fields just because upstream supplies a larger dictionary. Never change a registered module contract.',
                'Registered module interfaces are copied deterministically by the platform before user confirmation; do not invent or retype units.',
                'Return each step purpose, dependencies, named flat-dictionary inputs/outputs, assumptions, evidence, formula_latex and selection_reason.',
                'Workflow schema_version=3.0 physical inputs and output_ports MUST be named flat_dict, never bare fields. For loss outputs expose the complete loss dictionary and copy its Port, not scalar p_w/cap1_loss outputs.',
                'Binding examples: whole external dictionary {source:input,port:bag}; whole node dictionary {source:node,node:step_id,port:bag}. Do not use field when connecting a whole dictionary.',
                'Bind only user-confirmed steps and modules, using explicit bindings. Do not replace the calculation plan or create unused nodes.',
                'No implicit constants or default operating points. Expose unresolved numeric values as workflow inputs.',
                'Formula phase only: generate one pure controlled module implementing the supplied formula; no filesystem/network. Never generate expected answers.',
                'Only rename/path extraction/assembly/declared unit conversion in adapters; other transforms require explicit evidence.',
                'Never claim scientific correctness or publication. Platform checks schema, connections and independent examples.']}
    def begin(self,t,phase,limit=4):
        t['extension_context']=self.extensions.catalog(t['request'].get('project_id','local'))
        t.update(phase=phase,status='dispatch_ready',message={'plan':'等待需求分析与计算方案','bind':'方案已确认，等待匹配接线','formula':'等待生成计算工具候选'}[phase],calls=0,call_limit=limit,repeat_count=0,last_report=None,previous_candidate=None,dependency_audit=None,verification_feedback=None,questions={},clarification_used=False)
        t['catalog_context_id']=self.store().put_context({'kind':'design_catalog','modules':[m.model_dump() for m in self.modules().values()]})
        t.pop('catalog_snapshot',None)
        t['configuration']=configuration(limit)
        return self.save(t,'stage_requested:'+phase)
    async def dispatch(self,t):
        if t['calls']>=t.get('call_limit',4):
            t.update(status='paused',message='本批调用额度已用完；保留方案，请用户批准后继续');return self.save(t,'call_limit_reached')
        if os.getenv('PE_DSH_ENABLED','1')=='0':
            t.update(status='paused',message='DSH 未启用；方案已保存，可配置后继续');return self.save(t,'dsh_disabled')
        if t['configuration'].get('provider_hash')!=configuration()['provider_hash']:
            t.update(status='paused',message='任务冻结后上下文规则已变更，请核对新配置再批准继续');return self.save(t,'context_configuration_changed')
        installed=Path.home()/'.dsh/.agent-presets/flux-design-v2'
        for file,key in [('agent.cordis.yml','prompt_hash'),('flux-design-tools.js','tool_hash')]:
            if not (installed/file).exists() or (installed/file).read_bytes()!=(PRESET/file).read_bytes() or digest((PRESET/file).read_text(encoding='utf-8'))!=t['configuration'][key]:
                t.update(status='paused',message='DSH 设计预设未安装或配置已变更；未发起调用');return self.save(t,'preset_mismatch')
        t.update(status='dispatching',call_id=uuid.uuid4().hex,token=secrets.token_urlsafe(32),calls=t['calls']+1,dispatched_at=time.time())
        t=self.save(t,'dispatch_intent')
        try:
            session='flux-design-'+uuid.uuid4().hex
            await asyncio.to_thread(rpc,'session.create',{'sessionId':session,'agentPreset':'flux-design-v2'},t['call_id']+'-create')
            cred={'task_id':t['id'],'call_id':t['call_id'],'token':t['token']}
            await asyncio.to_thread(rpc,'session.prompt',{'sessionId':session,'mode':'queue','content':[{'type':'text','text':'Read design_context then submit design_result. '+canonical(cred)}]},t['call_id'])
            current=self.get(t['id'])
            if current['status']=='dispatching' and current.get('call_id')==t['call_id']:
                current['status']='awaiting_worker';self.save(current,'prompt_accepted')
        except Exception as e:
            current=self.get(t['id']);current.update(status='delivery_unknown',message='调用送达不明，不自动重发：'+str(e));self.save(current,'delivery_unknown')
    def audit_plan(self,plan):
        ids=[s.id for s in plan.steps]
        if len(set(ids))!=len(ids):raise ValueError('步骤 ID 重复')
        seen=set()
        for s in plan.steps:
            if not set(s.dependencies)<=seen:raise ValueError('依赖必须指向前面的步骤；不允许未声明循环')
            if any(p.type!='flat_dict' for p in [*s.inputs.values(),*s.outputs.values()]):raise ValueError('步骤接口必须为命名扁平字典')
            if s.module_ref and s.module_ref not in self.modules():raise ValueError('模块不存在：'+s.module_ref)
            if s.module_ref:
                m=self.modules()[s.module_ref]
                for side in ('inputs','outputs'):
                    supplied=getattr(s,side);expected=getattr(m,side)
                    for name,port in supplied.items():
                        if name not in expected:raise ValueError('模块未声明此字典：'+name)
                        for key,field in port.fields.items():
                            if key not in expected[name].fields:raise ValueError('模块未声明此字段：'+key)
                            target=expected[name].fields[key]
                            for attr in ('type','unit','quantity','semantic_id'):
                                if attr in field.model_fields_set and getattr(field,attr)!=getattr(target,attr):raise ValueError('步骤与模块字段声明冲突：'+name+'.'+key+'/'+attr)
                    setattr(s,side,copy.deepcopy(expected))
            seen.add(s.id)
    def retry(self,t,error,allow_retry=True):
        report={'message':str(error),'code':getattr(getattr(error,'diagnostic',None),'code','DESIGN_VALIDATION'),'path':getattr(getattr(error,'diagnostic',None),'path','/')}
        if t.get('dependency_audit'):report['dependency_audit']=t['dependency_audit']
        signature=digest({'phase':t['phase'],'code':report['code'],'category':str(error).split('：')[0].split(':')[0][:160]})
        t['repeat_count']=t.get('repeat_count',0)+1 if signature==t.get('failure_signature') else 1
        t.update(last_report=report,failure_signature=signature,status='paused' if not allow_retry or report['code'] in ('UNKNOWN_UNIT','EXAMPLE_DATA_INVALID') or report['code'].startswith('DEPENDENCY_') or report['code']=='CONTRACT_CHANGE_REQUIRES_CONFIRM' or t['calls']>=t.get('call_limit',4) or t['repeat_count']>=2 else 'dispatch_ready',message='候选未通过平台检查：'+str(error))
        return self.save(t,'validation_failed')
    async def receive(self,t,req,revalidate=False):
        if not revalidate and t.get('callback_hash')==digest(req.model_dump()):return self.public(t)
        if not revalidate and t['status'] not in ('dispatching','awaiting_worker','delivery_unknown'):raise Conflict('当前调用已结束')
        if not revalidate:t['callback_hash']=digest(req.model_dump());t['explanation']=req.explanation
        else:t['validation_configuration']=configuration()
        try:
            if req.kind=='extension_request' and req.extension_request:
                proposal=self.extensions.propose(t['request'].get('project_id','local'),req.extension_request,t['id'])
                t.update(status='waiting_extension',extension_request_id=proposal['id'],message='自定义声明等待用户补充和批准，尚未启用；不会自动注册工具或运行')
            elif req.kind in ('questions','blocked'):
                if not req.questions and req.kind=='questions':raise ValueError('问题不能为空')
                t.update(status='blocked' if req.kind=='blocked' or t.get('clarification_used') else 'needs_clarification',questions=req.questions,clarification_used=True,message=req.explanation or '关键依据不足，请集中补充后继续')
            elif t['phase']=='plan' and req.kind=='plan' and req.plan:
                for step in req.plan.steps:
                    if not step.module_ref:
                        step.inputs=self.extensions.resolve_ports(step.inputs,t['request'].get('project_id','local'))
                        step.outputs=self.extensions.resolve_ports(step.outputs,t['request'].get('project_id','local'))
                self.audit_plan(req.plan);t.update(plan=req.plan.model_dump(),status='awaiting_confirmation',message='计算方案已提出，请核对步骤、依赖、假设与候选工具')
            elif t['phase']=='bind' and req.kind=='workflow' and req.workflow:
                wf=req.workflow;plan=DesignPlan.model_validate(t['plan']);registry={f'{m["id"]}@{m["version"]}':Module.model_validate(m) for m in t['frozen_modules']}
                used=[]
                def visit(graph):
                    for n in graph.nodes:
                        if n.kind=='compute':used.append((n.id,f'{n.module_id}@{n.module_version}'))
                        if n.body:visit(n.body)
                        for case in n.cases.values():visit(case)
                visit(wf)
                if sorted(used)!=sorted((s.id,s.module_ref) for s in plan.steps):raise ValueError('节点必须与确认方案的步骤及模块逐一一致；控制节点可包裹步骤')
                report=validate_workflow(wf,registry)
                if not report['valid']:raise ValueError(canonical(report['diagnostics']))
                if not wf.outputs:raise ValueError('工作流必须有结果输出')
                check_dependencies(wf,plan,registry)
                # All embedded defaults/constants require explicit user review, never agent invention.
                def defaults(port):return port.default is not None or any(defaults(p) for p in port.fields.values())
                def constants(graph):return any(b.source=='constant' for n in graph.nodes for b in n.inputs.values()) or any(constants(n.body) for n in graph.nodes if n.body) or any(constants(c) for n in graph.nodes for c in n.cases.values()) or any(b.source=='constant' for b in graph.outputs.values())
                if any(defaults(p) for p in wf.inputs.values()) or constants(wf):raise ValueError('首版规划不允许智能体植入默认数值或常量；请将其声明为外部输入')
                wf.id='design_'+t['id'];wf.name=plan.title
                t.update(status='ready_to_save',workflow=wf.model_dump(),validation={**report,'execution':'not_run','scientific_correctness':'not_established'},message='结构检查通过，尚未运行；可保存新工作流')
            elif t['phase']=='formula' and req.kind=='module' and req.module:
                m=req.module;f=t['formula_request'];plan=DesignPlan.model_validate(t['plan']);step=next(s for s in plan.steps if s.id==f['step_id'])
                if m.implementation not in ('expressions','python_subset','python_function') or m.contract_version!='3.0':raise ValueError('仅允许受控纯计算和 flat_dict 契约')
                target=t.get('registration_target',{'id':None,'version':1})
                if target['id'] is not None and (m.id!=target['id'] or m.version!=target['version']):raise ValueError('必须按指定基准派生新版本：'+canonical(target))
                if target['id'] is None and (m.version!=1 or any(x.id==m.id for x in self.modules().values())):raise ValueError('新工具必须使用新 ID 和版本 1；修改已有工具应从其方案派生')
                if f'{m.id}@{m.version}' in self.modules():raise ValueError('不可覆盖已有模块')
                for side in ('inputs','outputs'):
                    generated=getattr(m,side);declared=getattr(step,side)
                    if set(generated)!=set(declared):fail('CONTRACT_CHANGE_REQUIRES_CONFIRM','输入输出字典发生变化；请派生方案并由用户确认，不允许代码修正改变已确认接口','/module/'+side)
                    for bag,port in generated.items():
                        if set(port.fields)!=set(declared[bag].fields):fail('CONTRACT_CHANGE_REQUIRES_CONFIRM','字段增删必须先派生方案确认：'+bag+'；新增 '+', '.join(sorted(set(port.fields)-set(declared[bag].fields)))+'；移除 '+', '.join(sorted(set(declared[bag].fields)-set(port.fields))),'/module/'+side+'/'+bag+'/fields')
                        for attr in ('type','required','default','unit'):
                            if attr in port.model_fields_set and getattr(port,attr)!=getattr(declared[bag],attr):fail('CONTRACT_CHANGE_REQUIRES_CONFIRM','字典声明变化：'+bag+'/'+attr+'；原值 '+canonical(declared[bag].model_dump()[attr])+'；候选 '+canonical(port.model_dump()[attr]),'/module/'+side+'/'+bag+'/'+attr)
                        for key,field in port.fields.items():
                            target=declared[bag].fields[key]
                            if target.extension_refs and field.unit_definition is None:field.unit_definition=copy.deepcopy(target.unit_definition)
                            for attr in ('type','unit','quantity','semantic_id','statistic','event','value_type','axes','unit_definition','required','default'):
                                if attr in field.model_fields_set and getattr(field,attr)!=getattr(target,attr):fail('CONTRACT_CHANGE_REQUIRES_CONFIRM','字段声明变化须用户确认：'+bag+'.'+key+'/'+attr+'；原值 '+canonical(target.model_dump()[attr])+'；候选 '+canonical(field.model_dump()[attr]),'/module/'+side+'/'+bag+'/fields/'+key+'/'+attr)
                    setattr(m,side,copy.deepcopy(declared))
                check_examples(f['examples'],step.inputs,step.outputs)
                dependency=audit_dependencies(m);t['dependency_audit']=dependency
                if dependency['status']=='uncertain':fail('DEPENDENCY_UNCERTAIN','无法可靠确定字段依赖：'+'；'.join(dependency['uncertainties'])+'。请提供显式字段访问或集中补充后再提交','/module/source')
                if dependency['status']!='passed':
                    reasons=[]
                    for key,label in [('unused_required_fields','声明必填但未读取'),('undeclared_fields','读取未声明字段'),('unsafe_optional_fields','无保护读取可选字段')]:
                        if dependency[key]:reasons.append(label+'：'+', '.join(dependency[key]))
                    fail('DEPENDENCY_CONFLICT','；'.join(reasons)+'。保持接口时请修改代码；确需改变接口则派生方案并由用户确认','/module/inputs')
                check_module(m)
                if m.transforms and len(f['examples'])<2:raise ValueError('显式变换必须提供至少两个独立验证样例，请补充独立样例及变换前提')
                from .engine import fill_inputs,validate_value
                for ex in f['examples']:
                    fill_inputs(ex['inputs'],m.inputs,'/examples/inputs')
                    if set(ex['outputs'])!=set(m.outputs):raise ValueError('独立样例必须覆盖全部输出')
                    for k,v in ex['outputs'].items():validate_value(v,m.outputs[k],'/examples/outputs/'+k)
                wf=Workflow(schema_version='3.0',id='formula_check',name=m.name,inputs=m.inputs,nodes=[Node(id='calculate',module_id=m.id,module_version=m.version,inputs={k:Binding(source='input',port=k) for k in m.inputs})],outputs={k:Binding(source='node',node='calculate',port=k) for k in m.outputs},output_ports=m.outputs)
                reports=[]
                for index,ex in enumerate(f['examples']):
                    actual=await asyncio.to_thread(self.worker,{'workflow':wf.model_dump(),'inputs':ex['inputs'],'modules':[m.model_dump()]},30)
                    if actual.get('status')!='succeeded' or not equivalent(actual['outputs'],ex['outputs']):
                        # Human-only feedback: expected answers must never enter the worker context.
                        t['verification_feedback']={'example':index+1,'inputs':ex['inputs'],'expected':ex['outputs'],'actual':actual.get('outputs',{}),'execution_status':actual.get('status')}
                        fail('EXAMPLE_RESULT_MISMATCH','第 '+str(index+1)+' 组独立数值样例不通过；实际输出：'+canonical(actual.get('outputs',{}))+'；预期答案仅在用户核对区显示','/examples/'+str(index)+'/outputs')
                    reports.append({'status':'passed','input_hash':digest(ex['inputs'])})
                probe_reports=[]
                for index,ex in enumerate(f['examples']):
                    def minimum(values,ports):
                        return {k:minimum(values[k],p.fields) if p.type=='flat_dict' else values[k] for k,p in ports.items() if p.required and k in values}
                    minimal=minimum(ex['inputs'],m.inputs)
                    base=await asyncio.to_thread(self.worker,{'workflow':wf.model_dump(),'inputs':minimal,'modules':[m.model_dump()]},30)
                    if base.get('status')!='succeeded':fail('DEPENDENCY_MINIMUM_FAILED','第 '+str(index+1)+' 组最小必需输入测试失败；可选字段不能成为隐式依赖','/examples/'+str(index)+'/inputs')
                    enlarged=copy.deepcopy(minimal);probe=wf.model_copy(deep=True)
                    from .models import Port
                    for name,port in probe.inputs.items():
                        if port.type!='flat_dict' or name not in enlarged:continue
                        key='audit_unused'
                        while key in port.fields:key+='x'
                        port.fields[key]=Port(type='number',quantity='scalar',unit='1')
                        enlarged[name][key]=137
                    extra=await asyncio.to_thread(self.worker,{'workflow':probe.model_dump(),'inputs':enlarged,'modules':[m.model_dump()]},30)
                    if extra.get('status')!='succeeded' or not equivalent(extra['outputs'],base['outputs']):fail('DEPENDENCY_EXTRA_FAILED','额外携带字段改变了输出或阻止计算','/examples/'+str(index)+'/inputs')
                    probe_reports.append({'example':index+1,'minimum_inputs':'passed','extra_fields':'passed'})
                t.update(status='formula_candidate',candidate=m.model_dump(),formula_validation={'interface':'passed','implementation_checks':'passed','dependency_audit':dependency,'input_probes':probe_reports,'probe_status':'passed' if reports else 'not_run_no_independent_examples','independent_examples':reports,'scientific_applicability':'user_review_required'},message='候选已检查；确认注册后才可用于方案')
            else:raise ValueError('当前阶段 '+t['phase']+' 必须提交 kind='+{'plan':'plan','bind':'workflow','formula':'module'}[t['phase']]+'；不要重新提交其他阶段产物')
        except Exception as e:
            t['previous_candidate']=req.model_dump(exclude={'token'})
            failed=self.retry(t,e,allow_retry=not revalidate)
            if getattr(getattr(e,'diagnostic',None),'code','')=='DEPENDENCY_UNCERTAIN':
                failed.update(status='blocked' if failed.get('clarification_used') else 'needs_clarification',clarification_used=True,
                    questions={'dependency_scope':'代码存在无法可靠分析的动态读取或字典传递。请集中说明允许读取的字段，以及是否可改写为显式字段路径；文字说明不会替代后续代码检查。'},message='字段依赖尚不确定，候选保留；补充说明并重新检查前不能注册或应用')
                failed=self.save(failed,'dependency_clarification_required')
            return self.public(failed)
        t['last_report']=None
        return self.public(self.save(t,'platform_candidate_revalidated' if revalidate else 'agent_result:'+req.kind))
    async def loop(self):
        for t in self.all(active_only=True):
            if t['status'] in ('dispatching','awaiting_worker'):
                t.update(status='delivery_unknown',message='服务恢复；不自动重发可能已送达的调用');self.save(t,'restart')
        while True:
            for t in self.all(active_only=True):
                try:
                    if t['status']=='dispatch_ready':await self.dispatch(t)
                    elif t['status']=='awaiting_worker' and time.time()-t['dispatched_at']>600:
                        t.update(status='delivery_unknown',message='等待超时，不自动重发');self.save(t,'timeout')
                except Conflict:pass
                except Exception as e:
                    t=self.get(t['id']);t.update(status='paused',message=str(e));self.save(t,'platform_failure')
            await asyncio.sleep(1)

def install(app,get_store,module_map,worker,problem):
    service=Designs(get_store,module_map,worker);app.state.designs=service
    from .extensions import install as install_extensions
    service.extensions=install_extensions(app,get_store)
    previous=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        async with previous(app):
            task=asyncio.create_task(service.loop())
            try:yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):await task
    app.router.lifespan_context=lifespan
    @app.get('/api/design-module-code/{module_id}/{version}')
    def module_code(module_id:str,version:int):
        m=module_map().get(f'{module_id}@{version}')
        if not m:problem('MODULE_NOT_FOUND','模块不存在',404)
        code=m.source or canonical(m.expressions)
        source_kind='module'
        if m.implementation=='trusted_builtin':
            from . import dbsrc
            code=Path(dbsrc.__file__).read_text(encoding='utf-8');source_kind='frozen_builtin_wrapper_with_dependencies'
        return {'module_ref':f'{module_id}@{version}','implementation':m.implementation,'builtin_id':m.builtin_id,'source_kind':source_kind,'code':code,'adapter':m.adapter.model_dump() if m.adapter else None}
    @app.get('/api/design-configuration')
    def current_configuration():return configuration()
    @app.get('/api/workflow-designs')
    def listing():
        rows=service.all();return {'items':[service.public(t) for t in rows],'count':len(rows)}
    @app.post('/api/workflow-designs')
    def create(req:DesignRequest):
        if req.environment_ref:fail('ENVIRONMENT_UNAVAILABLE','环境包接口已预留，目前尚未安装环境包','/environment_ref')
        return service.public(service.begin({'id':uuid.uuid4().hex,'request':req.model_dump(),'created_at':now()},'plan'))
    @app.get('/api/workflow-designs/{key}')
    def read(key:str):return service.public(service.get(key))
    @app.post('/api/workflow-designs/{key}/extension-decision')
    def extension_decision(key:str,req:ExtensionDecision):
        t=service.get(key)
        if t['status']!='waiting_extension':raise Conflict('当前没有待处理扩展申请')
        decision=service.extensions.decision(t['extension_request_id'],req.revision,req.approve)
        t.update(status='paused',message='扩展已批准，请核对派生方案' if req.approve else '扩展申请已拒绝；原方案保留')
        service.save(t,'extension_approved' if req.approve else 'extension_rejected')
        if not req.approve:return service.public(service.get(key))
        child=copy.deepcopy(t);child.update(id=uuid.uuid4().hex,parent_id=key,revision=0,events=[],created_at=now())
        child.pop('extension_request_id',None);child['approved_extension']=decision['entry']
        project=t['request'].get('project_id','local')
        child['extension_context']=service.extensions.catalog(project)
        for step in child.get('plan',{}).get('steps',[]):
            if not step.get('module_ref'):
                for side in ('inputs','outputs'):
                    step[side]={k:p.model_dump() for k,p in service.extensions.resolve_ports({k:Port.model_validate(v) for k,v in step[side].items()},project).items()}
        candidate=(child.get('previous_candidate') or {}).get('module')
        if candidate and child.get('formula_request'):
            step=next(s for s in child['plan']['steps'] if s['id']==child['formula_request']['step_id'])
            for side in ('inputs','outputs'):candidate[side]=copy.deepcopy(step[side])
            child.update(status='paused',message='已批准定义并派生方案；候选代码保持原样，请重新验证候选')
        elif child.get('plan'):
            child.update(status='awaiting_confirmation',phase='plan',formula_draft=child.get('formula_request') or child.get('formula_draft'),message='已批准定义并派生方案，请核对后继续')
        else:
            child.update(status='paused',phase='plan',message='定义已批准；如需继续需求分析，请批准下一批调用')
        return service.public(service.save(child,'extension_approved_derived_plan'))
    @app.post('/api/workflow-designs/{key}/revise')
    def revise(key:str,req:DesignRevision):
        old=service.get(key);service.check_revision(old,req.revision);service.audit_plan(req.plan)
        return service.public(service.save({'id':uuid.uuid4().hex,'parent_id':key,'request':old['request'],'created_at':now(),'status':'awaiting_confirmation','phase':'plan','plan':req.plan.model_dump(),'formula_draft':copy.deepcopy(old.get('formula_request') or old.get('formula_draft'))},'user_derived_plan'))
    @app.post('/api/workflow-designs/{key}/confirm')
    def confirm(key:str,req:DesignConfirm):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status'] not in ('awaiting_confirmation','missing_tools'):raise Conflict('当前方案不能确认，请派生修改')
        plan=DesignPlan.model_validate(t['plan']);service.audit_plan(plan)
        missing=[s.id for s in plan.steps if not s.module_ref]
        if missing:
            t.update(status='missing_tools',missing_steps=missing,message='方案已保存；补齐缺失工具后派生方案并继续');return service.public(service.save(t,'missing_tools'))
        # Current registry does not certify physical correctness. Explicit user selection is required.
        if not req.allow_unverified:fail('UNVERIFIED_SELECTION','现有工具没有统一的独立验证认证；请明确确认选用并核对适用性','/allow_unverified')
        t['frozen_modules']=[module_map()[r].model_dump() for r in sorted({s.module_ref for s in plan.steps})]
        t['user_selected_unverified']=True
        return service.public(service.begin(t,'bind',req.max_calls))
    @app.post('/api/workflow-designs/{key}/answer')
    def answer(key:str,req:DesignAnswer):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status'] not in ('needs_clarification','blocked') or not t.get('questions'):raise Conflict('当前没有待回答问题')
        if set(req.answers)!=set(t['questions']) or any(not v.strip() for v in req.answers.values()):fail('ANSWER_REQUIRED','请回答全部问题','/answers')
        t.setdefault('answers',{}).update(req.answers);t.update(status='dispatch_ready',questions={})
        return service.public(service.save(t,'user_answers'))
    @app.post('/api/workflow-designs/{key}/continue')
    def resume(key:str,req:DesignContinue):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status'] not in ('paused','blocked','delivery_unknown'):raise Conflict('当前任务无需追加')
        if t.get('phase')=='formula':
            f=t['formula_request'];step=next(s for s in t['plan']['steps'] if s['id']==f['step_id'])
            check_examples(f['examples'],step['inputs'],step['outputs'])
        t.setdefault('configuration_history',[]).append({'at':now(),'configuration':copy.deepcopy(t.get('configuration'))})
        t['configuration']=configuration(req.max_calls)
        t.pop('callback_hash',None);t.update(status='dispatch_ready',calls=0,call_limit=req.max_calls,repeat_count=0)
        return service.public(service.save(t,'user_approved_three_repairs'))
    @app.post('/api/workflow-designs/{key}/stop')
    def stop(key:str,req:DesignConfirm):
        t=service.get(key);service.check_revision(t,req.revision);t.update(status='paused',message='用户停止；晚到结果不应用')
        return service.public(service.save(t,'user_stopped'))
    @app.post('/api/workflow-designs/{key}/formula')
    def formula(key:str,req:FormulaRequest):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status'] not in ('awaiting_confirmation','missing_tools'):raise Conflict('请先返回方案核对阶段')
        step=next((s for s in t['plan']['steps'] if s['id']==req.step_id),None)
        if not step:fail('STEP_NOT_FOUND','步骤不存在','/step_id')
        if not step['inputs'] or not step['outputs']:fail('DECLARATIONS_REQUIRED','步骤尚缺输入输出声明，请先补充方案','/step')
        for side in ('inputs','outputs'):
            for name,port in step[side].items():
                try:check_port(Port.model_validate(port),'/step/'+side+'/'+name)
                except WorkflowError as error:
                    if error.diagnostic.code!='UNKNOWN_UNIT':raise
                    def unknown(p):
                        from .engine import UNITS
                        if p.unit and p.unit not in UNITS and p.unit_definition is None:return p.unit
                        for f in p.fields.values():
                            value=unknown(f)
                            if value:return value
                        return unknown(p.item) if p.item else None
                    unit=unknown(Port.model_validate(port))
                    if not unit:raise
                    check_examples(req.examples,step['inputs'],step['outputs'])
                    proposal=service.extensions.propose(t['request'].get('project_id','local'),ExtensionProposal(kind='unit',name=unit,description='计算步骤 '+step['title']+' 使用的单位；请核对量纲和换算依据'),key)
                    t.update(formula_request=req.model_dump(exclude={'revision'}),status='waiting_extension',extension_request_id=proposal['id'],message='发现未登记单位，等待用户补充并批准；尚未调用 DSH')
                    return service.public(service.save(t,'unit_extension_requested'))
        check_examples(req.examples,step['inputs'],step['outputs'])
        t['formula_request']=req.model_dump(exclude={'revision'})
        base=module_map().get(step.get('module_ref'))
        t['registration_target']={'id':base.id if base else None,'version':max(m.version for m in module_map().values() if m.id==base.id)+1 if base else 1,'parent_version':base.version if base else None}
        return service.public(service.begin(t,'formula'))
    @app.post('/api/workflow-designs/{key}/revalidate-candidate')
    async def revalidate_candidate(key:str,req:DesignConfirm):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['phase']!='formula' or t['status'] not in ('paused','blocked'):raise Conflict('只有已暂停或受阻的公式候选可以重新验证')
        candidate=(t.get('previous_candidate') or {}).get('module')
        if not candidate:fail('CANDIDATE_MISSING','没有保留的模块候选，不能重新验证','/candidate')
        # No code generation, registration, publication or retry budget is consumed.
        t['dependency_audit']=None;t['verification_feedback']=None
        return await service.receive(t,DesignWorkerResult(call_id=t.get('call_id','platform_revalidation'),token=t.get('token',''),kind='module',module=Module.model_validate(candidate),explanation=t.get('explanation','')),revalidate=True)
    @app.post('/api/workflow-designs/{key}/register-formula')
    def register_formula(key:str,req:DesignConfirm):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status']!='formula_candidate':raise Conflict('没有可注册候选')
        if not req.allow_unverified:fail('EXPLICIT_SELECTION','请确认公式及适用条件','/allow_unverified')
        m=Module.model_validate(t['candidate'])
        if audit_dependencies(m)['status']!='passed':fail('DEPENDENCY_RECHECK_REQUIRED','当前候选依赖未通过检查，请返回方案修正后重新生成','/module/inputs')
        from .registration import verify_references
        store=get_store();verify_references(m.model_dump(),store.definitions(),store.catalogs(),store.rules())
        existing=module_map().get(f'{m.id}@{m.version}')
        if existing is None:store.add_module(m,parent_version=t.get('registration_target',{}).get('parent_version'),summary='按用户确认公式派生；独立样例范围见方案 '+key,acceptance_label='独立样例通过，适用性待核对' if t['formula_request']['examples'] else '未提供独立样例，待核对')
        elif existing!=m:raise Conflict('候选版本已被不同内容占用')
        new_plan=copy.deepcopy(t['plan'])
        for step in new_plan['steps']:
            if step['id']==t['formula_request']['step_id']:step.update(module_ref=f'{m.id}@{m.version}',selection_reason='用户确认公式候选；独立验证范围见父方案公式报告')
        t.update(status='formula_registered',message='工具已独立注册；派生新方案继续，旧方案记录保留')
        service.save(t,'formula_registered')
        child={'id':uuid.uuid4().hex,'parent_id':key,'request':t['request'],'created_at':now(),'status':'awaiting_confirmation','phase':'plan','plan':new_plan,'tool_evidence':{'module_ref':f'{m.id}@{m.version}','report':t['formula_validation']}}
        return service.public(service.save(child,'tool_completed_derived_plan'))
    @app.post('/api/workflow-designs/{key}/publish')
    def publish(key:str,req:DesignConfirm):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status']=='saved':return service.public(t)
        if t['status']!='ready_to_save':raise Conflict('工作流尚未通过结构检查')
        wf=Workflow.model_validate(t['workflow']);report=validate_workflow(wf,module_map())
        if not report['valid']:fail('STRUCTURE_INVALID','模块或契约不兼容','/workflow')
        # Deterministic id makes retry idempotent across a crash between saves.
        existing=get_store().workflow(wf.id)
        if not existing:get_store().save(wf,{},0)
        elif existing['workflow']!=wf.model_dump():raise Conflict('同名工作流已有不同内容')
        t.update(status='saved',workflow_revision=1,message='已保存新工作流；未运行、未证明数值或工程正确性')
        return service.public(service.save(t,'workflow_saved_without_running'))
    def authorized(key,call,token):
        t=service.get(key)
        if t.get('call_id')!=call or not secrets.compare_digest(t.get('token',''),token):problem('WORKER_CREDENTIAL','调用凭证无效',403)
        return t
    @app.get('/api/workflow-designs/{key}/context')
    def context(key:str,call_id:str,module_ref:str='',authorization:str=Header(default='')):
        t=authorized(key,call_id,authorization.removeprefix('Bearer '))
        if module_ref:
            modules=t.get('frozen_modules') or get_store().context(t['catalog_context_id'])['modules']
            selected=next((m for m in modules if f'{m["id"]}@{m["version"]}'==module_ref),None)
            if not selected:problem('MODULE_NOT_FOUND','冻结工具快照中没有此版本',404)
            return {'module':selected,'scientific_review':'not_certified'}
        return service.context(t)
    @app.post('/api/workflow-designs/{key}/result')
    async def result(key:str,req:dict):
        t=authorized(key,req.get('call_id',''),req.get('token',''))
        try:parsed=DesignWorkerResult.model_validate(req)
        except Exception as exc:
            if t['status'] not in ('dispatching','awaiting_worker','delivery_unknown'):raise Conflict('本次调用已结束')
            return service.public(service.retry(t,exc))
        return await service.receive(t,parsed)
