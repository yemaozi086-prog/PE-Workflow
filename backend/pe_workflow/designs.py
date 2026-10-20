"""Requirement-first design. Agent proposes; platform validates and publishes drafts.
No operating-point values are requested until the saved workflow is run.
"""
import asyncio,copy,json,os,secrets,time,uuid,re
from contextlib import asynccontextmanager,suppress
from pathlib import Path
from fastapi import Header
from .models import (DesignRequest,DesignRevision,DesignConfirm,DesignAnswer,DesignWorkerResult,
                     DesignPlan,PlanPatch,DesignReplan,FormulaRequest,Module,Workflow,Node,Binding,DesignContinue,Port,ExtensionProposal,ExtensionDecision,DesignEvidenceReview)
from .storage import ROOT,canonical,digest,now,Conflict
from .engine import fail,validate_workflow,check_module,check_port,fill_inputs,validate_value,WorkflowError
from .automation import rpc
from .evaluation import equivalent
from . import numeric
from .design_guidance import adapter_repair,array_logic_repair
from .expressions import syntax_manifest
from .dependency_audit import audit_dependencies
from .plan_audit import preflight,same_checks
from .plan_patch import apply_patch as apply_plan_patch
from .repair_routing import route_failure,budget_snapshot
from .planning_protocol import blueprint_schema
from .preset_runtime import render_prompt,tool_filename

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
    prompt=render_prompt((PRESET/'agent.cordis.yml').read_text(encoding='utf-8'),(PRESET/'flux-design-tools.js').read_bytes())
    return {'prompt':prompt,'preset':'flux-design-v3',
            'prompt_hash':digest(prompt),
            'tool_hash':digest((PRESET/'flux-design-tools.js').read_text(encoding='utf-8')),
            'provider_hash':digest({'syntax_manifest':syntax_manifest(),'interpreter':(Path(__file__).parent/'expressions.py').read_text(encoding='utf-8'),'numeric':(Path(__file__).parent/'numeric.py').read_text(encoding='utf-8'),'control_contracts':(Path(__file__).parent/'control_contracts.py').read_text(encoding='utf-8'),'control_repair':(Path(__file__).parent/'control_repair.py').read_text(encoding='utf-8'),'submission_protocol':(Path(__file__).parent/'submission_protocol.py').read_text(encoding='utf-8'),'candidate_receipts':(Path(__file__).parent/'candidate_receipts.py').read_text(encoding='utf-8'),'planning_protocol':(Path(__file__).parent/'planning_protocol.py').read_text(encoding='utf-8'),'plan_controls':(Path(__file__).parent/'plan_controls.py').read_text(encoding='utf-8'),'finite_control':(Path(__file__).parent/'finite_control.py').read_text(encoding='utf-8'),'binding_assembly':(Path(__file__).parent/'binding_assembly.py').read_text(encoding='utf-8'),'context_pages':(Path(__file__).parent/'design_context_pages.py').read_text(encoding='utf-8'),'progress':(Path(__file__).parent/'design_progress.py').read_text(encoding='utf-8'),'recovery':(Path(__file__).parent/'completion_recovery.py').read_text(encoding='utf-8'),'completion':(Path(__file__).parent/'tool_completion.py').read_text(encoding='utf-8'),'routing':(Path(__file__).parent/'repair_routing.py').read_text(encoding='utf-8'),'provider':Path(__file__).read_text(encoding='utf-8'),'guidance':(Path(__file__).parent/'design_guidance.py').read_text(encoding='utf-8'),'missing_values':(Path(__file__).parent/'missing_values.py').read_text(encoding='utf-8'),'missing_revision':(Path(__file__).parent/'missing_revision.py').read_text(encoding='utf-8'),'input_usage':(Path(__file__).parent/'input_usage.py').read_text(encoding='utf-8'),'dependency_audit':(Path(__file__).parent/'dependency_audit.py').read_text(encoding='utf-8'),'plan_audit':(Path(__file__).parent/'plan_audit.py').read_text(encoding='utf-8'),'plan_patch':(Path(__file__).parent/'plan_patch.py').read_text(encoding='utf-8'),'shape_repair':(Path(__file__).parent/'shape_repair.py').read_text(encoding='utf-8'),'models':(Path(__file__).parent/'models.py').read_text(encoding='utf-8')}),
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
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS workflow_designs(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS design_late_results(task_id TEXT,call_id TEXT,payload_hash TEXT,document TEXT,created_at TEXT,PRIMARY KEY(task_id,call_id,payload_hash))')
        return store.connect()
    def get(self,key):
        with self.connect() as c:r=c.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()
        if not r:fail('DESIGN_NOT_FOUND','计算方案不存在','/design')
        return json.loads(r[0])
    def all(self,active_only=False,archived=False):
        query="SELECT document FROM workflow_designs WHERE COALESCE(json_extract(document,'$.archived'),0)="+('1' if archived and not active_only else '0')+(" AND json_extract(document,'$.status') IN ('dispatch_ready','dispatching','awaiting_worker','delivery_unknown')" if active_only else '')+" ORDER BY rowid DESC"+('' if active_only else ' LIMIT 100')
        with self.connect() as c:return [json.loads(r[0]) for r in c.execute(query)]
    def save(self,t,event):
        t=copy.deepcopy(t);old=t.get('revision',0);t['revision']=old+1;t['updated_at']=now()
        if 'call_limit' in t and event!='platform_patch_applied':
            scope=t.get('phase','plan')+(':'+t.get('formula_request',{}).get('step_id','') if t.get('phase')=='formula' else '')
            t.setdefault('stage_budgets',{})[scope]={'used':t.get('calls',0),'limit':t['call_limit']}
        t.setdefault('events',[]).append({'at':now(),'actor':'agent' if event.startswith('agent_result') else 'user' if event.startswith('user_') else 'platform','phase':t.get('phase'),'event':event,'status':t['status'],'revision':t['revision'],'report':copy.deepcopy(t.get('last_report')) if event=='validation_failed' else None,'routing':copy.deepcopy(t.get('failure_routing')) if event=='validation_failed' else None})
        with self.connect() as c:
            if old:
                r=c.execute('UPDATE workflow_designs SET revision=?,document=? WHERE id=? AND revision=?',(t['revision'],canonical(t),t['id'],old))
                if r.rowcount!=1:raise Conflict('方案已更新，请刷新后重试')
            else:c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(t['id'],1,canonical(t)))
        return t
    def plan_report(self,t,plan=None):
        plan=plan or DesignPlan.model_validate(t['plan'])
        previous=DesignPlan.model_validate(t['previous_plan']) if t.get('previous_plan') and t.get('preserve_plan_checks',True) and t.get('repair_mode')!='plan_patch' else None
        return preflight(plan,previous)
    def public(self,t):
        result={k:v for k,v in t.items() if k not in ('token','frozen_modules','catalog_snapshot')}
        from .submission_protocol import execution_state,continuation_action
        result['execution_state']=execution_state(t)
        result['continuation_action']=continuation_action(t)
        from .design_lifecycle import actions
        result['recovery_actions']=actions(t)
        if hasattr(self,'receipts'):
            result['candidate_processing']=self.receipts.state(t)
            if (t.get('last_report') or {}).get('code')=='CANDIDATE_PROCESSING_INTERRUPTED':
                for action in result['recovery_actions']:action['receipt_hashes']=[p['payload_hash'] for p in self.receipts.pending(t)]
        result['configuration_review_required']=t.get('status')=='paused' and bool(t.get('events')) and t['events'][-1].get('event')=='context_configuration_changed'
        result['remaining_calls']=max(0,t.get('call_limit',4)-t.get('calls',0))
        result['worker_feedback']={'accepted':t.get('status') in ('awaiting_confirmation','ready_to_save','formula_ready'),'required_result_kind':'plan_delta' if t.get('format_repair') else 'plan_blueprint' if t.get('planning_protocol')=='blueprint_v2' and t.get('phase')=='plan' else {'plan':'plan','bind':'workflow','formula':'module'}.get(t.get('phase')),'repair_ticket':result['execution_state'],'instruction':'Read diagnostics and rules before the next submission. No full rewrite when allowed_paths is nonempty. Platform owns retries; do not self-submit after call closes.'}
        result.pop('latest_blueprint',None)
        result.pop('extension_context',None)
        if t.get('extension_request_id'):result['extension_request']=self.extensions.read(t['extension_request_id'])
        if t.get('plan'):
            try:
                plan=DesignPlan.model_validate(t['plan']);self.audit_plan(plan)
                result['plan_preflight']=self.plan_report(t,plan)
            except (ValueError,WorkflowError) as e:
                result['plan_preflight']={'valid':False,'issues':[{'code':'PLAN_INVALID','message':str(e),'path':'/plan'}]}
        return result
    def check_revision(self,t,rev):
        if t['revision']!=rev:raise Conflict('方案版本已改变，请核对最新版本')
        if t.get('archived'):raise Conflict('任务已归档，请先恢复；恢复不会自动启动调用')
        if t.get('superseded_by'):raise Conflict('此旧任务已切换，请打开新修订 '+t['superseded_by'])
    def context(self,t):
        from .submission_protocol import execution_state
        registry=self.modules()
        snapshot=t.get('frozen_modules') or (self.store().context(t['catalog_context_id'])['modules'] if t.get('catalog_context_id') else t.get('catalog_snapshot')) or [m.model_dump() for m in registry.values()]
        catalog=[{k:m.get(k) for k in ('id','version','name','description','capabilities','applies_to')}|{'input_dictionaries':list(m['inputs']),'output_dictionaries':list(m['outputs'])} for m in snapshot]
        formula_request=copy.deepcopy(t.get('formula_request'))
        if formula_request is not None:
            formula_request['verification_inputs']=[ex['inputs'] for ex in formula_request.pop('examples',[])]
        patch_mode=t['phase']=='plan' and t.get('repair_mode')=='plan_patch'
        schema=(PlanPatch if patch_mode else DesignPlan if t['phase']=='plan' else Workflow if t['phase']=='bind' else Module).model_json_schema()
        if patch_mode:schema['$defs']['PlanStepEdit']['properties']['step_id']['enum']=[s['id'] for s in t['patch_base_plan']['steps']]
        result={'protocol':'workflow_design_v1','phase':t['phase'],'request':t['request'],
            'repair_ticket':execution_state(t),
            'repair_budget':{'used_calls':t.get('calls',0),'remaining_calls':max(0,t.get('call_limit',4)-t.get('calls',0)),'repeat_count':t.get('repeat_count',0),'owner':'platform'},
            'repair_mode':t.get('repair_mode'),'patch_base':{'hash':t.get('patch_base_hash'),'plan':t.get('patch_base_plan')},
            'previous_patch':t.get('previous_patch'),
            'previous_plan':t.get('previous_plan'),'replan_diagnostics':t.get('replan_diagnostics'),
            'repair_constraints':{'preserve_step_ids_and_checks':bool(t.get('previous_plan')) and t.get('preserve_plan_checks',True),'instruction':'同一需求的修正保留原步骤 ID 和全部原保护条件；按 input_sources/field_map 翻译局部路径，不能删除条件或改变阈值。'},
            'plan_repair':(t.get('last_report') or {}).get('plan_preflight') or (self.plan_report(t) if t.get('plan') and t['phase']=='plan' else t.get('replan_diagnostics')),
            'extension_catalog':t.get('extension_context',self.extensions.catalog(t['request'].get('project_id','local'))),
            'extension_proposal_schema':ExtensionProposal.model_json_schema(),
            'plan':t.get('plan'),'answers':t.get('answers',{}),'formula_request':formula_request,'registration_target':t.get('registration_target'),
            'last_report':t.get('last_report'),'dependency_audit':t.get('dependency_audit'),'previous_candidate':t.get('previous_candidate'),
            'required_result_kind':'plan_patch' if patch_mode else {'plan':'plan','bind':'workflow','formula':'module'}[t['phase']],
            'stage_instruction':'只返回 kind=plan_patch 与 patch，不返回完整 plan。patch 必须为 JSON 对象，不能为字符串。edits 仅修改指定步骤输入接口与来源；若仅修复类型/维度/轴，设置 repair_shapes=true，保持来源和所有其他字段元数据不变，精确复制正式来源的 type/value_type/axes；待补齐工具不设置 derive_interface，已注册工具接口变化则设为 true 以派生；检查通过仍需用户确认。步骤、公式、输出、依赖、保护条件由平台冻结。仅保护条件路径错误时允许 edits=[]，平台按明确来源翻译；映射不唯一时集中追问。' if patch_mode else {'plan':'只提出计算方案；不索取运行数值。','bind':'模块已登记，用户已确认方案。仅提交 Workflow；不要再次生成或提交 Module。','formula':'按用户公式生成 Module 候选；不要编排工作流。'}[t['phase']],
            'concrete_protocol_repair':adapter_repair(t.get('previous_candidate')) if t['phase']=='formula' and t.get('last_report') else None,
            'explicitly_mentioned_modules':[m for m in snapshot if re.search(r'(?<![A-Za-z0-9_])'+re.escape(f'{m["id"]}@{m["version"]}')+r'(?![0-9])',t['request']['instruction'])] if t['phase']=='plan' else [],
            'modules':catalog if t['phase']=='plan' else snapshot if t['phase']=='bind' else [],
            'environment':{'status':'not_installed','ref':t['request']['environment_ref'],'interface_version':'environment_package_v1',
                'allow_external_suggestions':t['request']['allow_external_suggestions']},
            'adapter_examples':{'notes':'Structural examples only, not a physical model. Omit unused properties; never use from:null. Apply supplied field names and declarations.', 'dict_return':{'arguments':{'internal_arg':{'from':['inputs','external_bag']}},'returns':{'external_output':{'from':['return']}}},'scalar_return':{'arguments':{'internal_arg':{'from':['inputs','external_bag','field']}},'returns':{'external_output':{'assemble':{'field':{'from':['return']}}}}}},
            'implementation_protocols':{'python_subset':{'signature':'def compute(inputs):','reads':'inputs[bag][field]','returns':'complete output dictionary, e.g. {loss: {P_cond: value}}','adapter':'not used; do not supply multiple function parameters'},'python_function':{'signature':'one named function with declared parameters','requires':'entrypoint and explicit adapter.arguments/returns','example_signature':'def compute(electrical, device):','note':'Multiple parameters require this implementation type, never python_subset'}},
            'controlled_syntax':{'manifest':syntax_manifest(),'array_logic':array_logic_repair(),'python':'one pure function; interpreter manifest and implementation_protocols are authoritative; controlled imports only; no comprehensions, loops or helper definitions','numeric_functions':sorted(numeric.FUNCTIONS),'numeric_catalog':numeric.catalog(),'array_rules':'Use only catalog functions and listed keyword arguments. Declare every explicit_transform in module.transforms. Nontrivial transforms require at least two independent examples; never generate reference answers.'},
            'schemas':{('patch' if patch_mode else 'plan' if t['phase']=='plan' else 'workflow' if t['phase']=='bind' else 'module'):schema},
            'rules':['Plan first; no numerical input values required. Never assume the old DBSRC chain matches the request.',
                'Declare plan.external_inputs and each step.input_sources using Binding. Each dictionary has exactly one source. Split multi-source inputs into independent named bags; a registered one-bag module cannot silently gain a second source. Propose a missing derived tool with evidence if needed.',
                'For interface derivation use module_ref=null and derives_from=existing_id@version. Declare the proposed independent input bags, preserve the physical formula, and request user confirmation before generating its next immutable version.',
                'Copy exact physical semantics, including statistic=rms and power_loss versus power. Never delete semantics to bypass checks. field_map maps target field to source field within ONE dictionary.',
                'Declare step.input_checks before unsafe arithmetic using bag["field"] comparisons and and/or only, e.g. design["L"] > 0. Cite supplied model prerequisites. Final constraint evaluation cannot protect earlier division or invalid CCM calculations. BIND copies sources and checks exactly; no hidden rewiring.',
                'No environment package is installed. Every physical dependency needs cited user or module evidence; uncertain assumptions must be visible.',
                'If external suggestions are disabled, use only user-provided facts and registered module declarations; ask questions when insufficient.',
                'Plans may contain missing tools (module_ref=null). No invented physical model. Module refs are id@version.',
                'After code generation, the platform audits literal field reads against required flags. Dynamic dictionary accesses block automatic registration. All repairs must preserve confirmed required/optional flags and units; interface changes require a user-derived plan. Return python_subset outputs nested under declared output port names.',
                'PLAN: new tools declare only calculation and input-check dependencies; upstream extra fields do not make them required. FORMULA: preserve confirmed inputs/outputs and required flags exactly. Implement the actual formula without dummy reads. If confirmed inputs overdeclare requirements, platform offers input-usage revision review; never silently omit fields or change required flags. Node checks are copied into module.input_checks by the platform and are executed independently of formula reads. Never change registered modules.',
                'Registered module interfaces are copied deterministically by the platform before user confirmation; do not invent or retype units.',
                'Return each step purpose, dependencies, named flat-dictionary inputs/outputs, assumptions, evidence, formula_latex and selection_reason.',
                'Workflow schema_version=3.0 physical inputs and output_ports MUST be named flat_dict, never bare fields. For loss outputs expose the complete loss dictionary and copy its Port, not scalar p_w/cap1_loss outputs.',
                'Binding examples: whole external dictionary {source:input,port:bag}; whole node dictionary {source:node,node:step_id,port:bag}. Do not use field when connecting a whole dictionary.',
                'Bind only user-confirmed steps and modules, using explicit bindings. Do not replace the calculation plan or create unused nodes.',
                'No implicit constants or default operating points. Expose unresolved numeric values as workflow inputs.',
                'Formula phase only: generate one pure controlled module implementing the supplied formula; no filesystem/network. Never generate expected answers.',
                'Formula registration_target is authoritative: use its exact id and version for derived tools; only a null target id authorizes a new unique id and version 1. Do not add parent_version to Module.',
                'Only rename/path extraction/assembly/declared unit conversion in adapters; other transforms require explicit evidence.',
                'Never claim scientific correctness or publication. Platform checks schema, connections and independent examples.']}
        if t.get('planning_protocol')=='blueprint_v2' and t['phase']=='plan' and not patch_mode:
            from .planning_protocol import guidance
            result['planning_protocol']=guidance()
            if t.get('format_repair'):
                from .planning_protocol import repair_context
                result['format_repair']=repair_context(t['format_repair'])
                result['previous_candidate']={'kind':(t.get('previous_candidate') or {}).get('kind'),'receipt_hash':t.get('candidate_receipt_hash'),'notice':'原始候选由平台留存；本轮只读取format_repair中的诊断片段。'}
            else:result['format_repair']=None
            result['required_result_kind']='plan_delta' if t.get('format_repair') else 'plan_blueprint'
            result['stage_instruction']='Submit '+result['required_result_kind']+' using planning_protocol. Do not rewrite full plans during local repair.'
            from .planning_protocol import delta_schema
            result['schemas']={'delta':delta_schema(t['format_repair'].get('allowed_paths',[]))} if t.get('format_repair') else {'blueprint':blueprint_schema()}
            # These legacy flat-only instructions do not apply to explicit numeric control data.
            result['rules']=[r for r in result['rules'] if 'MUST be named flat_dict' not in r]
        return result
    def begin(self,t,phase,limit=4):
        retained_failure={k:t[k] for k in ('repeat_count','failure_signature') if k in t} if t.get('declaration_recovery_key') else {}
        scope=phase+(':'+t.get('formula_request',{}).get('step_id','') if phase=='formula' else '')
        budget=t.get('stage_budgets',{}).get(scope)
        t['extension_context']=self.extensions.catalog(t['request'].get('project_id','local'))
        t.update(phase=phase,status='dispatch_ready',message={'plan':'等待需求分析与计算方案','bind':'方案已确认，等待匹配接线','formula':'等待生成计算工具候选'}[phase],calls=0,call_limit=limit,repeat_count=0,last_report=None,previous_candidate=None,dependency_audit=None,verification_feedback=None,questions={},clarification_used=False,failure_routing=None)
        t.update(retained_failure)
        if budget:
            t.update(calls=budget['used'],call_limit=budget['limit'])
            if t['calls']>=t['call_limit']:t.update(status='paused',message='已恢复原阶段额度；额度耗尽，请明确批准追加后继续')
        if retained_failure.get('repeat_count',0)>=2:t.update(status='paused',message='同类错误连续两次，补充声明不会重置停止规则；需要明确批准后继续')
        t['catalog_context_id']=self.store().put_context({'kind':'design_catalog','modules':[m.model_dump() for m in self.modules().values()]})
        t.pop('catalog_snapshot',None)
        t['configuration']=configuration(limit)
        return self.save(t,'stage_requested:'+phase)
    async def dispatch(self,t):
        if hasattr(self,'completion') and not self.completion.can_dispatch(t):return
        if t['calls']>=t.get('call_limit',4):
            t.update(status='paused',message='本批调用额度已用完；保留方案，请用户批准后继续');return self.save(t,'call_limit_reached')
        if os.getenv('PE_DSH_ENABLED','1')=='0':
            t.update(status='paused',message='DSH 未启用；方案已保存，可配置后继续');return self.save(t,'dsh_disabled')
        if t['configuration'].get('provider_hash')!=configuration()['provider_hash']:
            t.update(status='paused',message='任务冻结后上下文规则已变更，请核对新配置再批准继续');return self.save(t,'context_configuration_changed')
        installed=Path.home()/'.dsh/.agent-presets/flux-design-v3'
        current=configuration()
        for file,key,expected in [('agent.cordis.yml','prompt_hash',current['prompt'].encode('utf-8')),(tool_filename((PRESET/'flux-design-tools.js').read_bytes()),'tool_hash',(PRESET/'flux-design-tools.js').read_bytes())]:
            if not (installed/file).exists() or (installed/file).read_bytes()!=expected or current[key]!=t['configuration'][key]:
                t.update(status='paused',message='DSH 设计预设未安装或配置已变更；未发起调用');return self.save(t,'preset_mismatch')
        from .design_lifecycle import close_call
        close_call(self,t,'new_call_started')
        t.update(status='dispatching',session_id='flux-design-'+uuid.uuid4().hex,worker_result_closed=False,call_id=uuid.uuid4().hex,token=secrets.token_urlsafe(32),calls=t['calls']+1,total_calls=t.get('total_calls',t['calls'])+1,dispatched_at=time.time())
        t=self.save(t,'dispatch_intent')
        try:
            if os.getenv('PE_WORKFLOW_PORT'):
                from .worker_routes import write_route
                write_route(ROOT,t['id'],t['call_id'],t['token'],int(os.environ['PE_WORKFLOW_PORT']))
            session=t['session_id']
            await asyncio.to_thread(rpc,'session.create',{'sessionId':session,'agentPreset':'flux-design-v3'},t['call_id']+'-create')
            cred={'task_id':t['id'],'call_id':t['call_id'],'token':t['token']}
            await asyncio.to_thread(rpc,'session.prompt',{'sessionId':session,'mode':'queue','content':[{'type':'text','text':'Read design_context then submit design_result. '+canonical(cred)}]},t['call_id'])
            current=self.get(t['id'])
            if current['status']=='dispatching' and current.get('call_id')==t['call_id']:
                current['status']='awaiting_worker';self.save(current,'prompt_accepted')
        except Exception as e:
            current=self.get(t['id']);current.update(status='delivery_unknown',message='调用送达不明，不自动重发：'+str(e));self.save(current,'delivery_unknown')
    def prepare_formula(self,t,req,limit=4):
        if t['status'] not in ('awaiting_confirmation','missing_tools'):raise Conflict('请先返回方案核对阶段')
        checked=DesignPlan.model_validate(t['plan']);self.audit_plan(checked)
        report=self.plan_report(t,checked)
        if not report['valid']:fail('PLAN_PREFLIGHT','请先修正方案来源与语义，再生成工具：'+canonical(report['issues']),'/plan')
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
                    proposal=self.extensions.propose(t['request'].get('project_id','local'),ExtensionProposal(kind='unit',name=unit,description='计算步骤 '+step['title']+' 使用的单位；请核对量纲和换算依据'),t['id'])
                    t.update(formula_request=req.model_dump(exclude={'revision'}),status='waiting_extension',extension_request_id=proposal['id'],message='发现未登记单位，等待用户补充并批准；尚未调用 DSH')
                    return self.save(t,'unit_extension_requested')
        check_examples(req.examples,step['inputs'],step['outputs'])
        t['formula_request']=req.model_dump(exclude={'revision'})
        base=self.modules().get(step.get('module_ref') or step.get('derives_from'))
        t['registration_target']={'id':base.id if base else None,'version':max(m.version for m in self.modules().values() if m.id==base.id)+1 if base else 1,'parent_version':base.version if base else None}
        if t.get('completion_registration_target'):t['registration_target']=copy.deepcopy(t['completion_registration_target'])
        return self.begin(t,'formula',limit)
    def audit_plan(self,plan):
        ids=[s.id for s in plan.steps]
        if len(set(ids))!=len(ids):raise ValueError('步骤 ID 重复')
        seen=set()
        for s in plan.steps:
            if not(plan.grids or plan.loops) and not set(s.dependencies)<=seen:raise ValueError('依赖必须指向前面的步骤；不允许未声明循环')
            if any(p.type!='flat_dict' for p in [*s.inputs.values(),*s.outputs.values()]):raise ValueError('步骤接口必须为命名扁平字典')
            if s.module_ref and s.module_ref not in self.modules():raise ValueError('模块不存在：'+s.module_ref)
            if s.derives_from and s.derives_from not in self.modules():raise ValueError('派生来源模块不存在：'+s.derives_from)
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
        if t.get('blueprint_origin') and t.get('latest_blueprint') and not t.get('format_repair'):
            preflight=getattr(error,'plan_preflight',None) or (self.plan_report(t) if t.get('plan') else {})
            issues=preflight.get('issues',[])
            if issues and all(i.get('code')=='PLAN_DEPENDENCY_MISMATCH' for i in issues):
                from .planning_protocol import repair_manifest,ProtocolError
                manifest=repair_manifest({'kind':'plan_blueprint','blueprint':t['latest_blueprint']},ProtocolError(issues),self.modules())
                if manifest:t['format_repair']=manifest
        if t.get('format_repair'):
            report['allowed_repair_paths']=t['format_repair']['allowed_paths']
            report['issues']=getattr(error,'issues',None)
            if hasattr(error,'errors'):report['issues']=[{'path':'/'+('/'.join(map(str,e['loc']))),'message':e['msg']} for e in error.errors()]
        if getattr(error,'issues',None):report['issues']=error.issues
        if hasattr(error,'errors') and not report.get('issues'):report['issues']=[{'path':'/'+('/'.join(map(str,e['loc']))),'message':e['msg']} for e in error.errors()]
        if t.get('dependency_audit'):report['dependency_audit']=t['dependency_audit']
        if getattr(error,'plan_preflight',None):report['plan_preflight']=error.plan_preflight
        elif t['phase']=='plan' and t.get('plan'):report['plan_preflight']=self.plan_report(t)
        category=str(error).split('：')[0].split(':')[0][:160]
        if t.get('format_repair') and report.get('issues'):
            category=sorted({x['message'] for x in report['issues']})
        if report.get('plan_preflight'):
            category=sorted({(d['code'],d['path']) for d in report['plan_preflight']['issues']})
        signature=digest({'phase':t['phase'],'code':report['code'],'category':category})
        t['repeat_count']=t.get('repeat_count',0)+1 if signature==t.get('failure_signature') else 1
        routing=route_failure(error,report)
        if t.pop('unscoped_plan_failure',False) and routing['automatic']:
            routing.update(route='needs_confirmation',automatic=False,reason='此声明错误没有安全的局部修正范围',next_action='已保留原始候选，请核对结构修订；不自动重新生成整份方案')
        connection_questions=(t.get('format_repair') or {}).get('questions') or []
        if connection_questions:
            routing.update(route='needs_information',automatic=False,reason='连接声明仍有未确定的来源或输出端口',next_action='集中确认所列连接；不猜测输出、不消耗额外修正调用')
        if t.get('blueprint_origin') and t['phase']=='plan' and routing['automatic'] and not t.get('format_repair') and t.get('repair_mode')!='plan_patch':
            repairs=(report.get('plan_preflight') or {}).get('repairs') or []
            if repairs and t.get('plan'):
                t.update(repair_mode='plan_patch',patch_base_plan=copy.deepcopy(t['plan']),patch_base_hash=digest(t['plan']))
                routing['next_action']='仅提交现有步骤输入与来源补丁，冻结其余方案内容'
            else:
                routing.update(route='needs_confirmation',automatic=False,reason='当前诊断无法限定安全的局部修改范围',next_action='核对结构修订范围；不自动重写整份方案或追加调用')
        stopped=not allow_retry or t['calls']>=t.get('call_limit',4) or t['repeat_count']>=2
        routing['stop_reason']='revalidation_only' if not allow_retry else 'budget_exhausted' if t['calls']>=t.get('call_limit',4) else 'repeated_error' if t['repeat_count']>=2 else None
        t.update(last_report=report,failure_routing=routing,failure_signature=signature,status='dispatch_ready' if routing['automatic'] and not stopped else 'paused',message=routing['reason']+'；'+routing['next_action'])
        if allow_retry and routing['route']=='uncertain':
            t.update(status='needs_clarification',questions={'failure_evidence':'请补充错误归属的依据或说明尚不能确认的部分。若要改变公式、样例、接口或约束，请派生修订；本回答不会修改已确认内容。'},clarification_used=True)
        elif allow_retry and routing['route']=='needs_information':
            t.update(status='needs_clarification',questions={'declaration_evidence':routing['next_action']+'。问题位置：'+report['path']+'；请提供定义或资料来源，也可说明暂时无法提供。补充说明不会自行改写已确认契约。'},clarification_used=True)
            if connection_questions:
                t['questions']={'connection_'+str(i+1):q['question']+' 位置：'+q['path']+'；当前声明：'+canonical(q['selected_binding'])+'；该节点已声明输出：'+', '.join(q['available_ports']) for i,q in enumerate(connection_questions)}
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
                t['failure_routing']={'route':'needs_information','automatic':False,'reason':'智能体提出待核对的信息，平台尚未将其判为候选错误','next_action':'补充依据或保存待补充，不通过追加代码调用绕过问题','evidence':{'code':'AGENT_INFORMATION_REQUEST'}}
            elif t['phase']=='plan' and t.get('repair_mode')=='plan_patch' and req.kind not in ('plan_patch',):
                fail('PATCH_REQUIRED','修复任务只接受局部补丁，不接受重写完整方案','/patch')
            elif t['phase']=='plan' and req.kind=='plan_patch' and req.patch:
                if t.get('repair_mode')!='plan_patch':fail('PATCH_UNEXPECTED','当前不是补丁修复任务','/patch')
                t['previous_patch']=req.patch.model_dump()
                candidate,audit=apply_plan_patch(DesignPlan.model_validate(t['patch_base_plan']),req.patch)
                self.audit_plan(candidate)
                report=preflight(candidate)
                if not report['valid']:fail('PLAN_PREFLIGHT',canonical(report['issues']),'/patch')
                t.update(plan=candidate.model_dump(),patch_audit=audit,status='awaiting_confirmation',message='局部补丁检查通过；原步骤与保护条件已保留，请核对后继续')
            elif t['phase']=='plan' and req.kind=='plan' and req.plan:
                for step in req.plan.steps:
                    if not step.module_ref:
                        step.inputs=self.extensions.resolve_ports(step.inputs,t['request'].get('project_id','local'))
                        step.outputs=self.extensions.resolve_ports(step.outputs,t['request'].get('project_id','local'))
                self.audit_plan(req.plan);t['plan']=req.plan.model_dump()
                precheck=self.plan_report(t,req.plan)
                if not precheck['valid']:fail('PLAN_PREFLIGHT','方案接线预检查未通过：'+canonical(precheck['issues']),'/plan')
                t.update(status='awaiting_confirmation',message='计算方案已提出，请核对步骤、依赖、假设与候选工具')
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
                def check_bindings(graph):
                    for node in graph.nodes:
                        step=next((s for s in plan.steps if s.id==node.id),None)
                        if node.kind=='compute' and step:
                            if step.input_sources and node.inputs!=step.input_sources:raise ValueError('实际来源必须与已确认方案一致：'+node.id)
                            if node.input_checks!=step.input_checks:raise ValueError('不能删除或改写已确认输入保护：'+node.id)
                        if node.body:check_bindings(node.body)
                        for case in node.cases.values():check_bindings(case)
                check_bindings(wf)
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
                            for attr in ('type','unit','quantity','semantic_id','statistic','event','value_type','axes','unit_definition','required','default','missing_when'):
                                if attr in field.model_fields_set and getattr(field,attr)!=getattr(target,attr):fail('CONTRACT_CHANGE_REQUIRES_CONFIRM','字段声明变化须用户确认：'+bag+'.'+key+'/'+attr+'；原值 '+canonical(target.model_dump()[attr])+'；候选 '+canonical(field.model_dump()[attr]),'/module/'+side+'/'+bag+'/fields/'+key+'/'+attr)
                    setattr(m,side,copy.deepcopy(declared))
                check_examples(f['examples'],step.inputs,step.outputs)
                if m.input_checks and not same_checks(m.input_checks,step.input_checks):
                    fail('CONTRACT_CHANGE_REQUIRES_CONFIRM','工具保护条件必须与已确认步骤一致','/module/input_checks')
                m.input_checks=copy.deepcopy(step.input_checks)
                module_check_error=None
                try:check_module(m)
                except WorkflowError as error:
                    if error.diagnostic.code=='MODULE_SYNTAX':raise
                    module_check_error=error
                dependency=audit_dependencies(m);t['dependency_audit']=dependency
                if dependency['status']=='uncertain':fail('DEPENDENCY_UNCERTAIN','无法可靠确定字段依赖：'+'；'.join(dependency['uncertainties'])+'。请提供显式字段访问或集中补充后再提交','/module/source')
                if dependency['status']!='passed':
                    if dependency['unused_required_fields'] and not dependency['undeclared_fields'] and not dependency['unsafe_optional_fields']:
                        fail('INPUT_USAGE_REVIEW_REQUIRED','声明含本工具未使用的必填字段；请查看输入依赖修订预览并确认。不能为通过检查而添加无效读取，也不能在候选中擅自改必填性。','/module/inputs')
                    reasons=[]
                    for key,label in [('unused_required_fields','声明必填但未读取'),('undeclared_fields','读取未声明字段'),('unsafe_optional_fields','无保护读取可选字段')]:
                        if dependency[key]:reasons.append(label+'：'+', '.join(dependency[key]))
                    fail('DEPENDENCY_CONFLICT','；'.join(reasons)+'。保持接口时请修改代码；确需改变接口则派生方案并由用户确认','/module/inputs')
                if module_check_error:raise module_check_error
                if m.transforms and len(f['examples'])<2:fail('TRANSFORM_EXAMPLES_REQUIRED','显式变换必须提供至少两个独立验证样例，请补充独立样例及变换前提','/examples')
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
            return self.public(failed)
        t['last_report']=None
        if req.kind not in ('questions','blocked','extension_request'):t['failure_routing']=None
        return self.public(self.save(t,'platform_candidate_revalidated' if revalidate else 'agent_result:'+req.kind))
    async def loop(self):
        for t in self.all(active_only=True):
            if t['status'] in ('dispatching','awaiting_worker'):
                t.update(status='delivery_unknown',message='服务恢复；不自动重发可能已送达的调用');self.save(t,'restart')
        observed_at=0
        while True:
            if time.time()-observed_at>=15:
                observed_at=time.time()
                for live in self.all(active_only=True):
                    if live['status'] in ('dispatching','awaiting_worker','delivery_unknown'):
                        try:await asyncio.to_thread(self.progress.refresh,live['id'])
                        except Exception:
                            import logging
                            logging.getLogger(__name__).exception('Design observation failed; no resend')
            for t in self.all(active_only=True):
                try:
                    if t['status']=='dispatch_ready':await self.dispatch(t)
                    elif t['status']=='awaiting_worker' and time.time()-t['dispatched_at']>600:
                        t.update(status='delivery_unknown',message='等待超时，不自动重发');self.save(t,'timeout')
                except Conflict:pass
                except Exception as e:
                    t=self.get(t['id']);t.update(status='paused',failure_routing={'route':'platform_fault','automatic':False,'reason':'平台任务调度异常','next_action':'检查平台诊断后恢复，不自动重发','evidence':{'exception':type(e).__name__}},message=str(e));self.save(t,'platform_failure')
            if hasattr(self,'completion'):
                try:self.completion.tick()
                except Exception as error:
                    # Queue failures are persisted by tick; do not kill the design runner.
                    import logging
                    logging.getLogger(__name__).exception('Tool completion queue failed')
            await asyncio.sleep(1)

def install(app,get_store,module_map,worker,problem):
    service=Designs(get_store,module_map,worker);app.state.designs=service
    from .extensions import install as install_extensions
    service.extensions=install_extensions(app,get_store)
    from .tool_completion import install as install_completion
    service.completion=install_completion(app,service)
    from .missing_revision import install as install_missing_revision
    install_missing_revision(app,service)
    from .module_formulas import install as install_formulas
    install_formulas(app,service)
    from .binding_assembly import install as install_binding_assembly
    service.binding_assembly=install_binding_assembly(app,service)
    from .design_progress import install as install_progress
    service.progress=install_progress(app,service)
    from .design_lifecycle import install as install_lifecycle
    install_lifecycle(app,service)
    from .planning_migration import install as install_planning_migration
    service.planning_migration=install_planning_migration(app,service)
    from .control_repair import install as install_control_repair
    service.control_repair=install_control_repair(app,service)
    from .candidate_receipts import install as install_receipts
    service.receipts=install_receipts(app,service)
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
    def listing(archived:bool=False):
        # Per-step generation belongs inside its queue, not the main task selector.
        with service.connect() as con:
            rows=[json.loads(r[0]) for r in con.execute("SELECT document FROM workflow_designs WHERE COALESCE(json_extract(document,'$.archived'),0)=?"+('' if archived else " AND json_extract(document,'$.queue_id') IS NULL")+" ORDER BY rowid DESC LIMIT 100",(int(archived),))]
        return {'items':[service.public(t) for t in rows],'count':len(rows)}
    from .models import DesignArchive
    @app.post('/api/workflow-designs/{key}/archive')
    def archive(key:str,req:DesignArchive):
        t=service.get(key)
        if t['revision']!=req.revision:raise Conflict('方案已更新，请刷新')
        if bool(t.get('archived'))==req.archived:return service.public(t)
        with service.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            live=json.loads(con.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()[0])
            if live['revision']!=req.revision:raise Conflict('方案已更新，请刷新')
            queues=[json.loads(r[0]) for r in con.execute("SELECT document FROM tool_completion_queues WHERE json_extract(document,'$.source_id')=? OR json_extract(document,'$.result_design_id')=? OR id=?",(key,key,t.get('queue_id','')))]
            ids={key}
            for q in queues:ids.update([q['source_id'],*[i['task_id'] for i in q['items']]])
            tasks=[json.loads(con.execute('SELECT document FROM workflow_designs WHERE id=?',(task_id,)).fetchone()[0]) for task_id in ids]
            if any(task['status'] in ACTIVE or task['status']=='delivery_unknown' for task in tasks):raise Conflict('请先核对执行中或送达不明的调用，再归档或恢复相关记录')
            from .completion_recovery import Recovery
            for task in tasks:
                task.update(archived=req.archived,archived_at=now() if req.archived else None)
                Recovery.persist(con,task,'user_archived_task' if req.archived else 'user_restored_task')
            for q in queues:
                q.update(archived=req.archived,status='paused')
                service.completion.write(con,q,q['revision'])
        return service.public(service.get(key))
    @app.post('/api/workflow-designs')
    def create(req:DesignRequest):
        if req.environment_ref:fail('ENVIRONMENT_UNAVAILABLE','环境包接口已预留，目前尚未安装环境包','/environment_ref')
        return service.public(service.begin({'id':uuid.uuid4().hex,'planning_protocol':'blueprint_v2','request':req.model_dump(),'created_at':now()},'plan'))
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
        child=service.save(child,'extension_approved_derived_plan')
        service.completion.adopted_extension(t,child)
        return service.public(child)
    @app.post('/api/workflow-designs/{key}/revise')
    def revise(key:str,req:DesignRevision):
        old=service.get(key);service.check_revision(old,req.revision);service.audit_plan(req.plan)
        from .repair_routing import changed_paths
        return service.public(service.save({'id':uuid.uuid4().hex,'parent_id':key,'request':old['request'],'created_at':now(),'status':'awaiting_confirmation','phase':'plan','plan':req.plan.model_dump(),'revision_changes':changed_paths(old.get('plan',{}),req.plan.model_dump()),'stage_budgets':budget_snapshot(old),'total_calls':old.get('total_calls',old.get('calls',0)),'formula_draft':copy.deepcopy(old.get('formula_request') or old.get('formula_draft'))},'user_derived_plan'))
    @app.post('/api/workflow-designs/{key}/confirm')
    def confirm(key:str,req:DesignConfirm):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status'] not in ('awaiting_confirmation','missing_tools'):raise Conflict('当前方案不能确认，请派生修改')
        plan=DesignPlan.model_validate(t['plan']);service.audit_plan(plan)
        report=service.plan_report(t,plan)
        if not report['valid']:fail('PLAN_PREFLIGHT','方案接线预检查未通过：'+canonical(report['issues']),'/plan')
        missing=[s.id for s in plan.steps if not s.module_ref]
        if missing:
            t.update(status='missing_tools',missing_steps=missing,message='方案已保存；补齐缺失工具后派生方案并继续');return service.public(service.save(t,'missing_tools'))
        # Current registry does not certify physical correctness. Explicit user selection is required.
        if not req.allow_unverified:fail('UNVERIFIED_SELECTION','现有工具没有统一的独立验证认证；请明确确认选用并核对适用性','/allow_unverified')
        t['frozen_modules']=[module_map()[r].model_dump() for r in sorted({s.module_ref for s in plan.steps})]
        t['user_selected_unverified']=True
        if plan.external_inputs or any(s.input_sources for s in plan.steps):
            t.update(phase='bind',status='awaiting_confirmation',assembly_pending=True,message='工具已确认。请核对程序接线预览并确认组装；不会调用模型。')
            return service.public(service.save(t,'explicit_binding_preview_requested'))
        return service.public(service.begin(t,'bind',req.max_calls))
    @app.post('/api/workflow-designs/{key}/replan')
    def replan(key:str,req:DesignReplan):
        old=service.get(key);service.check_revision(old,req.revision)
        if old['status'] in ACTIVE:raise Conflict('请先停止正在处理的任务')
        request=copy.deepcopy(old['request']);request['instruction']=req.instruction
        child={'id':uuid.uuid4().hex,'parent_id':key,'request':request,'created_at':now(),
               'replan_diagnostics':service.public(old).get('plan_preflight'),
               'previous_plan':copy.deepcopy(old.get('plan')),'preserve_plan_checks':req.instruction==old['request']['instruction']}
        return service.public(service.begin(child,'plan',req.max_calls))
    def shape_preview(t):
        from .shape_repair import preview
        base=DesignPlan.model_validate(t.get('patch_base_plan') or t.get('plan'))
        patch,changes=preview(base)
        result={'revision':t['revision'],'patch_hash':digest(patch.model_dump()),'changes':changes,'valid':False,'issues':[],'model_calls':0}
        if not changes:return result,None,None
        try:
            candidate,audit=apply_plan_patch(base,patch)
            service.audit_plan(candidate)
            result['valid']=True
            return result,candidate,audit
        except WorkflowError as e:
            result['issues']=(getattr(e,'plan_preflight',{}) or {}).get('issues') or [e.diagnostic.model_dump()]
        except ValueError as e:
            result['issues']=[{'code':'SHAPE_REPAIR_VALIDATION','message':str(e),'path':'/plan'}]
        return result,None,None

    @app.get('/api/workflow-designs/{key}/shape-repair-preview')
    def shape_repair_preview(key:str):
        t=service.get(key)
        if t['status'] in ACTIVE:raise Conflict('请先停止运行中的任务')
        return shape_preview(t)[0]

    from .models import ShapeRepairConfirm
    @app.post('/api/workflow-designs/{key}/shape-repair-confirm')
    def shape_repair_confirm(key:str,req:ShapeRepairConfirm):
        receipt=key+':'+digest(req.model_dump())
        with service.connect() as c:
            existing=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.shape_repair_receipt')=?",(receipt,)).fetchone()
        if existing:return service.public(json.loads(existing[0]))
        old=service.get(key);service.check_revision(old,req.revision)
        if old['phase']!='plan' or old['status'] in ACTIVE or old.get('archived') or old.get('superseded_by'):raise Conflict('请使用未运行且未归档的当前方案任务')
        review,candidate,audit=shape_preview(old)
        if review['patch_hash']!=req.patch_hash:raise Conflict('修正预览已变化，请重新核对')
        if not review['valid']:fail('SHAPE_REPAIR_BLOCKED','修正预览仍有阻塞，请核对具体诊断','/plan')
        child={'id':uuid.uuid4().hex,'parent_id':key,'request':copy.deepcopy(old['request']),'created_at':now(),
               'phase':'plan','status':'awaiting_confirmation','plan':candidate.model_dump(),'patch_audit':audit,
               'patch_origin':'platform','calls':old.get('calls',0),'call_limit':old.get('call_limit',4),
               'shape_repair_receipt':receipt,'configuration':configuration(old.get('call_limit',4)),
               'repeat_count':old.get('repeat_count',0),'failure_signature':old.get('failure_signature'),
               'stage_budgets':budget_snapshot(old),'total_calls':old.get('total_calls',old.get('calls',0)),
               'message':'已按预览派生接口修订；未调用模型、未重置额度。请核对方案后继续补工具。'}
        child.update(revision=1,updated_at=now(),events=[{'at':now(),'event':'user_confirmed_source_shape_repair','actor':'user','revision':1,'status':'awaiting_confirmation'}])
        with service.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            existing=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.shape_repair_receipt')=?",(receipt,)).fetchone()
            if existing:return service.public(json.loads(existing[0]))
            live=json.loads(c.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()[0]);service.check_revision(live,req.revision)
            c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child['id'],1,canonical(child)))
        return service.public(child)

    @app.post('/api/workflow-designs/{key}/repair')
    def repair(key:str,req:DesignConfirm):
        old=service.get(key);service.check_revision(old,req.revision)
        if old['status'] in ACTIVE:raise Conflict('请先停止运行中的任务')
        report=service.public(old).get('plan_preflight')
        if not report or report['valid']:fail('PATCH_NOT_NEEDED','当前没有待修复的方案问题','/plan')
        raw=old.get('patch_base_plan') or (old.get('previous_plan') if any(d['code']=='PLAN_GUARD_REGRESSION' for d in report['issues']) else old.get('plan'))
        base=DesignPlan.model_validate(raw);service.audit_plan(base)
        if not base.external_inputs:fail('PATCH_BASE_REQUIRED','旧方案尚无外部字典声明，请先重新规划；不能猜测运行数据来源','/plan/external_inputs')
        child={'id':uuid.uuid4().hex,'parent_id':key,'request':copy.deepcopy(old['request']),'created_at':now(),
               'stage_budgets':budget_snapshot(old),'total_calls':old.get('total_calls',old.get('calls',0)),
               'repair_mode':'plan_patch','patch_base_plan':base.model_dump(),'patch_base_hash':digest(base.model_dump()),
               'plan':base.model_dump(),'replan_diagnostics':preflight(base)}
        # Pure path repair is deterministic. Do not delegate a known mapping to
        # a model or consume generation budget just to request an empty patch.
        if all(d['code']=='INPUT_CHECK_INVALID' for d in child['replan_diagnostics']['issues']):
            try:
                patch=PlanPatch(base_hash=child['patch_base_hash'])
                candidate,audit=apply_plan_patch(base,patch)
            except WorkflowError:pass
            else:
                child.update(phase='plan',status='awaiting_confirmation',calls=0,call_limit=req.max_calls,
                    configuration=configuration(req.max_calls),plan=candidate.model_dump(),patch_audit=audit,
                    patch_origin='platform',previous_patch=patch.model_dump(),
                    message='平台已按明确接线转换保护字段路径；未调用智能体，请核对派生方案')
                return service.public(service.save(child,'platform_patch_applied'))
        return service.public(service.begin(child,'plan',req.max_calls))
    @app.post('/api/workflow-designs/{key}/answer')
    def answer(key:str,req:DesignAnswer):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status'] not in ('needs_clarification','blocked') or not t.get('questions'):raise Conflict('当前没有待回答问题')
        if set(req.answers)!=set(t['questions']) or any(not v.strip() for v in req.answers.values()):fail('ANSWER_REQUIRED','请回答全部问题','/answers')
        if t.get('completion_missing_info'):
            t.setdefault('answers',{}).update(req.answers);t.update(status='blocked',message='补充说明已保存；请派生修订补齐缺失声明并确认，不会直接启动代码生成')
            return service.public(service.save(t,'completion_information_saved'))
        if (t.get('failure_routing') or {}).get('route')=='uncertain':
            t.setdefault('answers',{}).update(req.answers)
            t.update(status='paused',questions={},message='补充证据已保存。错误归属仍须核对；如改变公式、样例或接口，请派生修订后确认；不会自动追加调用。')
            return service.public(service.save(t,'user_evidence_saved'))
        can_resume=t.get('calls',0)<t.get('call_limit',4) and t.get('repeat_count',0)<2
        t.setdefault('answers',{}).update(req.answers);t.update(status='dispatch_ready' if can_resume else 'paused',questions={},failure_routing=None,message='补充信息已保存；使用剩余额度继续' if can_resume else '补充信息已保存；已触及额度或重复错误上限，需要明确批准后继续')
        return service.public(service.save(t,'user_answers'))
    @app.post('/api/workflow-designs/{key}/defer')
    def defer(key:str,req:DesignConfirm):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status'] not in ('needs_clarification','blocked','waiting_extension'):raise Conflict('当前没有待补充的信息')
        if t['status']!='waiting_extension':t['status']='blocked'
        t['message']='已保存待补充事项；取得资料后恢复，不增加或消耗调用额度'
        return service.public(service.save(t,'user_deferred_information'))
    @app.post('/api/workflow-designs/{key}/continue')
    def resume(key:str,req:DesignContinue):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status']=='delivery_unknown':raise Conflict('原调用结果尚未确认，不重复发送；请先核对进度或明确停止接收旧结果')
        if t['status'] not in ('paused','blocked'):raise Conflict('当前调用尚未关闭，须先核对或停止；不能追加并重复发送')
        if t.get('questions'):raise Conflict('尚有待补充问题，请回答或保存待补充；不能通过追加调用绕过')
        from .design_lifecycle import SAFE_CODES
        if (t.get('failure_routing') or {}).get('route') in ('platform_fault','uncertain','needs_information','needs_confirmation') and (t.get('last_report') or {}).get('code') not in SAFE_CODES:
            raise Conflict('当前问题不能通过追加代码调用解决；请先核对诊断、补充资料或派生修订')
        if t.get('phase')=='formula':
            f=t['formula_request'];step=next(s for s in t['plan']['steps'] if s['id']==f['step_id'])
            check_examples(f['examples'],step['inputs'],step['outputs'])
        t.setdefault('configuration_history',[]).append({'at':now(),'configuration':copy.deepcopy(t.get('configuration'))})
        t['configuration']=configuration(req.max_calls)
        t.setdefault('budget_history',[]).append({'at':now(),'phase':t['phase'],'used':t.get('calls',0),'limit':t.get('call_limit',4),'approved_additional_calls':req.max_calls})
        t.pop('callback_hash',None);t.update(status='dispatch_ready',call_limit=max(t.get('call_limit',4),t.get('calls',0))+req.max_calls,repeat_count=0)
        return service.public(service.save(t,'user_approved_three_repairs'))
    @app.post('/api/workflow-designs/{key}/review-evidence')
    def review_evidence(key:str,req:DesignEvidenceReview):
        t=service.get(key);service.check_revision(t,req.revision)
        if t['status'] not in ('paused','needs_clarification') or (t.get('last_report') or {}).get('code')!='EXAMPLE_RESULT_MISMATCH':
            raise Conflict('当前不是等待核对独立样例的任务')
        feedback=t.get('verification_feedback') or {}
        if feedback.get('execution_status')!='succeeded':raise Conflict('计算未成功，须先查明执行失败原因，不能将其认定为公式差异')
        t.setdefault('evidence_reviews',[]).append({'at':now(),'evidence':req.evidence,'keep_confirmed_basis':True,'example':feedback['example']})
        t.setdefault('answers',{})['confirmed_basis_review']=req.evidence
        t.update(questions={},failure_routing={'route':'candidate_repair','automatic':True,'reason':'用户提供依据并确认保留原公式、样例与接口','next_action':'仅检查和修正候选；不改变验收答案','evidence':{'code':'USER_CONFIRMED_FIXED_BASIS'}},status='dispatch_ready' if t.get('calls',0)<t.get('call_limit',4) and t.get('repeat_count',0)<2 else 'paused',message='依据核对已保存；继续使用原额度与冻结契约，重复错误限制不重置')
        return service.public(service.save(t,'user_confirmed_fixed_basis'))
    @app.post('/api/workflow-designs/{key}/stop')
    def stop(key:str,req:DesignConfirm):
        from .design_lifecycle import close_call
        t=service.get(key);service.check_revision(t,req.revision);close_call(service,t,'user_stopped');t.update(status='paused',worker_result_closed=True,message='用户停止平台任务；尚未确认 DSH 已停止。晚到结果仅记录，不应用')
        return service.public(service.save(t,'user_stopped'))
    @app.post('/api/workflow-designs/{key}/formula')
    def formula(key:str,req:FormulaRequest):
        t=service.get(key);service.check_revision(t,req.revision)
        return service.public(service.prepare_formula(t,req))
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
        if t.get('queue_id'):
            from .models import ToolCompletionRegister
            q=service.completion.get(t['queue_id'])
            return service.completion.register(q['id'],ToolCompletionRegister(revision=q['revision'],task_ids=[key],allow_unverified=req.allow_unverified))['design']
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
        child={'id':uuid.uuid4().hex,'parent_id':key,'request':t['request'],'created_at':now(),'status':'awaiting_confirmation','phase':'plan','plan':new_plan,'stage_budgets':budget_snapshot(t),'total_calls':t.get('total_calls',t.get('calls',0)),'tool_evidence':{'module_ref':f'{m.id}@{m.version}','report':t['formula_validation']}}
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
    def context(key:str,call_id:str,module_ref:str='',section:str='',offset:int=0,step_id:str='',authorization:str=Header(default='')):
        t=authorized(key,call_id,authorization.removeprefix('Bearer '))
        if t.get('worker_result_closed') or t.get('superseded_by'):
            return {'status':'stopped','code':'CALL_CLOSED','stop_required':True,'message':'此调用已停止接收结果或已切换修订，不再重复读取资料'}
        if offset<0:fail('CONTEXT_OFFSET','分页位置不能为负数','/offset')
        from .design_context_pages import registry as context_registry, page, read as context_page
        if module_ref:
            service.progress.tool(t,'design_module',module_ref)
            modules=context_registry(service,t)
            selected=next((m for m in modules if f'{m["id"]}@{m["version"]}'==module_ref),None)
            if not selected:
                diagnostic={'code':'MODULE_NOT_FOUND','path':'/context/module_ref','message':'引用不在冻结索引中：'+module_ref}
                t.update(status='paused',worker_result_closed=True,last_report=diagnostic,
                         failure_routing={'route':'platform_fault','automatic':False,'reason':'工具资料读取失败，已停止本轮平台处理','next_action':'核对冻结索引与资料通路后恢复，不猜名重试','evidence':diagnostic},
                         message='模块读取失败，已停止接收本轮结果；DSH 是否停止需核对实际进度。没有自动重发。')
                service.save(t,'context_read_failed')
                return {'status':'failed','code':'MODULE_NOT_FOUND','module_ref':module_ref,'stop_required':True,'message':'已停止本轮处理。核对准确冻结索引，不再猜测模块名称。'}
            if section:return page({'module':selected,'scientific_review':'not_certified'},'module',offset)
            return {'module':selected,'scientific_review':'not_certified'}
        if section:
            if section not in ('index','request','plan','step','schema','rules','diagnostics','formula','extensions','controls','candidate'):fail('CONTEXT_SECTION','未知上下文章节','/section')
            service.progress.tool(t,'design_context:'+section)
            return context_page(service,t,section,offset,step_id)
        return service.context(t)
    async def process_result(key:str,req:dict,recovery=False):
        from .design_lifecycle import old_result
        current=service.get(key)
        if req.get('call_id')!=current.get('call_id'):
            late=old_result(service,current,req)
            if late:return late
        t=authorized(key,req.get('call_id',''),req.get('token',''))
        if t.get('superseded_by') or (t.get('worker_result_closed') and not recovery):
            payload={k:v for k,v in req.items() if k!='token'}
            with service.connect() as con:
                con.execute('INSERT OR IGNORE INTO design_late_results VALUES(?,?,?,?,?)',(key,req.get('call_id',''),digest(payload),canonical(payload),now()))
            return {'status':'recorded_late_result','applied':False,'active_design_id':t.get('superseded_by')}
        receipt=service.receipts.capture(t,req)
        if receipt['processed']:return service.public(t)
        t['candidate_receipt_hash']=receipt['payload_hash']
        repair_input=req
        try:
            from .planning_protocol import compile_blueprint,local_patch,normalize_binding_ports
            from .submission_protocol import normalize_submission
            normalized,envelope_audit=normalize_submission(req,t.get('planning_protocol')=='blueprint_v2')
            if envelope_audit:
                for item in envelope_audit:item.update(candidate_receipt_hash=receipt['payload_hash'],call_id=t['call_id'])
                t.setdefault('envelope_repair_audit',[]).extend(envelope_audit)
            req=normalized
            repair_input=req
            if t.get('format_repair') and req.get('kind') not in ('plan_delta','questions','blocked','extension_request'):
                fail('PLAN_DELTA_SCOPE','本轮只接受诊断限定的局部修改，不接受整份方案重写','/delta')
            if req.get('kind')=='plan_delta':
                if not t.get('format_repair'):fail('PLAN_DELTA_SCOPE','当前没有冻结的格式修正范围','/delta')
                try:raw=local_patch(t['format_repair'],req.get('delta'))
                except ValueError as e:fail('PLAN_DELTA_SCOPE',str(e),'/delta')
                field=t['format_repair']['field']
                t.setdefault('format_patch_history',[]).append({'base_hash':t['format_repair']['base_hash'],'edits':copy.deepcopy(req['delta']['edits']),'result_hash':digest(raw),'at':now()})
                if field=='blueprint':t['blueprint_origin']=True
                t['format_repair']['base']=raw;t['format_repair']['base_hash']=digest(raw)
                repair_input={'kind':'plan_blueprint' if field=='blueprint' else 'plan',field:raw}
                normalized.pop('delta',None);normalized['kind']='plan'
                normalized['plan']=(compile_blueprint(raw,service.modules()).model_dump() if field=='blueprint' else raw)
                if field=='blueprint':t['latest_blueprint']=copy.deepcopy(raw)
            elif req.get('kind')=='plan_blueprint':
                raw,repairs=normalize_binding_ports(req.get('blueprint') or {},service.modules())
                for repair in repairs:repair.update(candidate_receipt_hash=receipt['payload_hash'],call_id=t['call_id'])
                if repairs:t.setdefault('binding_repair_audit',[]).extend(repairs)
                repair_input={'kind':'plan_blueprint','blueprint':raw}
                normalized.pop('blueprint',None);normalized['kind']='plan';normalized['plan']=compile_blueprint(raw,service.modules()).model_dump();t['blueprint_origin']=True;t['latest_blueprint']=copy.deepcopy(raw)
            elif req.get('kind')=='plan' and isinstance(req.get('plan'),dict):
                raw,repairs=normalize_binding_ports(req['plan'],service.modules())
                for repair in repairs:repair.update(candidate_receipt_hash=receipt['payload_hash'],call_id=t['call_id'])
                if repairs:t.setdefault('binding_repair_audit',[]).extend(repairs)
                normalized['plan']=raw;repair_input={'kind':'plan','plan':raw}
            parsed=DesignWorkerResult.model_validate(normalized)
        except Exception as exc:
            if not recovery and t['status'] not in ('dispatching','awaiting_worker','delivery_unknown'):raise Conflict('本次调用已结束')
            from .planning_protocol import repair_manifest
            t['previous_candidate']=receipt['payload']
            manifest=repair_manifest(repair_input,exc,service.modules())
            if manifest:t['format_repair']=manifest
            elif req.get('kind') in ('plan','plan_blueprint') and not t.get('format_repair'):t['unscoped_plan_failure']=True
            response=service.public(service.retry(t,exc,allow_retry=not recovery));service.receipts.complete(t,receipt)
            return response
        t.pop('format_repair',None)
        response=await service.receive(t,parsed,revalidate=recovery);service.receipts.complete(t,receipt)
        return response

    @app.post('/api/workflow-designs/{key}/result')
    async def result(key:str,req:dict):
        # Auth first: an untrusted request must never reserve another call's slot.
        t=service.get(key)
        if req.get('call_id')==t.get('call_id'):authorized(key,req.get('call_id',''),req.get('token',''))
        with service.receipts.processing(key,req.get('call_id','')):
            return await process_result(key,req)

    @app.post('/api/workflow-designs/{key}/recheck-received-candidate')
    async def recheck_received(key:str,req:dict):
        t=service.get(key);service.check_revision(t,req.get('revision'))
        if t.get('superseded_by') or t.get('archived') or (t.get('last_report') or {}).get('code')!='CANDIDATE_PROCESSING_INTERRUPTED':raise Conflict('当前没有待恢复的中断候选')
        with service.receipts.processing(key,t.get('call_id','')):
            pending=service.receipts.pending(t)
            if req.get('receipt_hash'):pending=[p for p in pending if p['payload_hash']==req['receipt_hash']]
            if len(pending)!=1:raise Conflict('收到多份未处理候选，需明确选择原文；不会猜测或调用模型')
            payload={**pending[0]['payload'],'call_id':t['call_id'],'token':t['token']}
            return await process_result(key,payload,recovery=True)
