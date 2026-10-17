"""Deterministic registration and DSH task packages. No LLM dependency."""
import asyncio
from . import numeric
from .engine import check_module, check_port, compatible, WorkflowError, validate_workflow, fill_inputs, validate_value
from .expressions import analyze_source
from .models import CandidateRequest, ContextRequest, ReplacementRequest, SourceRequest, RegistrationContextRequest, ParameterDefinition, Module, Workflow
from .storage import digest


RULES = {
    "role":"Implement the supplied task; the platform owns orchestration and validation.",
    "scope":"One pure Python function; controlled AST interpreter, no filesystem/network/process access.",
    "semantics":"Never infer physical meaning from names alone. Use declared facts and provenance. Batch unresolved questions once; return needs_clarification before generating a candidate if critical facts are missing.",
    "adapter":"Only declared rename, extraction, assembly and registered affine unit conversion. Array indexing, transpose, reshape, resampling and slicing are physical transforms, not hidden input adaptation.",
    "transforms":"Declare operation, preconditions and rationale; submit independent expected verification samples covering the transform.",
    "tests":"Expected values must come from supplied evidence or hand-verifiable examples, not from running your own candidate and copying its output.",
    "response":"Return CandidateRequest JSON or {status: needs_clarification, questions: [...], evidence: [...]}. Do not register or apply by guessing.",
    "replacement":"Capabilities are search hints only. The platform checks the concrete workflow and returns a proposal; never claim equivalence or real-device accuracy from interface compatibility."
}


def verify_references(document,definitions,catalogs=None,rules=None):
    from .catalog import verify_catalog_references,verify_shared_rules
    if catalogs is not None:verify_catalog_references(document,catalogs)
    if rules is not None:verify_shared_rules(document,rules)
    registry={(d['id'],d['version']):d['port'] for d in definitions}
    def visit(value):
        if isinstance(value,dict):
            ref=value.get('definition_ref')
            if ref:
                expected=registry.get((ref['id'],ref['version']))
                if expected is None:raise ValueError(f"参数定义版本不存在：{ref}")
                from .models import Port
                actual=Port.model_validate(value).model_dump(exclude={'definition_ref','description','required','default'})
                target=Port.model_validate(expected).model_dump(exclude={'definition_ref','description','required','default'})
                if actual!=target:raise ValueError(f"参数定义引用与冻结声明不符：{ref}")
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(document)


def replacement(req,registry):
    workflow=req.workflow.model_copy(deep=True)
    node=next((n for n in workflow.nodes if n.id==req.node_id),None)
    candidate=registry.get(f'{req.module_id}@{req.module_version}')
    if node is None or node.kind!='compute' or candidate is None:
        return {'status':'incompatible','diagnostics':[{'code':'REPLACEMENT_TARGET','message':'必须指定当前图中的计算节点及已登记模块','path':'/node_id'}]}
    old=registry[f'{node.module_id}@{node.module_version}']
    try:
        # Preserve the replaced contract even if downstream declarations are weaker.
        for name,port in old.outputs.items():
            if name not in candidate.outputs:
                return {'status':'incompatible','diagnostics':[{'code':'REPLACEMENT_OUTPUT','message':f'候选缺少原输出 {name}','path':'/module/outputs'}]}
            compatible(candidate.outputs[name],port,f'/module/outputs/{name}')
        for name,port in old.inputs.items():
            if name in candidate.inputs and name in node.inputs:
                compatible(port,candidate.inputs[name],f'/module/inputs/{name}')
                compatible(candidate.inputs[name],port,f'/module/inputs/{name}')
    except WorkflowError as exc:
        return {'status':'incompatible','diagnostics':[exc.diagnostic.model_dump()]}
    node.module_id=candidate.id;node.module_version=candidate.version
    # Optional obsolete bindings may be removed. New required inputs are never guessed.
    node.inputs={k:v for k,v in node.inputs.items() if k in candidate.inputs}
    report=validate_workflow(workflow,registry,req.inputs)
    if not report['valid']:
        codes={d['code'] for d in report['diagnostics']}
        status='needs_inputs' if 'MISSING_BINDING' in codes else 'incompatible'
    elif candidate.domains or candidate.applies_to!=old.applies_to:
        status='conditional'
    else:status='compatible'
    return {'status':status,'validation':report,'workflow':workflow.model_dump(),'diagnostics':report['diagnostics'],
            'claims':{'capabilities':candidate.capabilities,'applies_to':candidate.applies_to,'domains':{k:v.model_dump() for k,v in candidate.domains.items()}},
            'limitation':'检查接口与接线；适用范围是作者声明，需使用场景证据和运行样例验证，不能据此证明模型物理等价。'}


def install(app,store,module_map,worker,problem):
    get_store=store
    class StoreProxy:
        def __getattr__(self,name):return getattr(get_store(),name)
    store=StoreProxy()

    @app.post('/api/harness/prepare-registration')
    def prepare_registration(req:RegistrationContextRequest):
        packet={'protocol_version':'4.0','task_kind':'register_flat_dictionary_module',
                'instruction':req.instruction,'module_id':req.module_id,'project_id':req.project_id,
                'suggested_revision':max((m['version'] for m in store.modules() if m['id']==req.module_id),default=0)+1,
                'uploaded_source':req.source,'entrypoint':req.entrypoint,'source_analysis':analyze_source(req.source),
                'declared_facts':req.declared_facts,'supplied_anchors':[a.model_dump() for a in req.anchors],
                'registry':store.modules(),'parameter_definitions':store.definitions(),
                'parameter_catalogs':store.catalogs(),'naming_rules':store.rules(),'numerical':numeric.catalog(),
                'candidate_schema':CandidateRequest.model_json_schema(),'rules':RULES|{
                    'public_contract':'Use contract_version 3.0: every input/output is a named flat dictionary. Keep independently named inputs separate.',
                    'extension':'Include validated module/project naming_rules with positive and negative examples. Never set confirm_shared or promote shared rules.',
                    'original_function':'Preserve uploaded function and formula. Prefer explicit adapter paths, assembly and unit conversion; generated adaptation code must remain pure.',
                    'missing_facts':'Return one batch of questions for uncertain meaning, units, array shape or independent expected results. Do not invent anchors.'}}
        return {'status':'prepared','connected':False,'context_id':store.put_context(packet),'context':packet}

    @app.get('/api/numeric/capabilities')
    def numerical():return numeric.catalog()

    @app.get('/api/parameter-definitions')
    def definitions():return store.definitions()

    @app.post('/api/parameter-definitions')
    def define(req:ParameterDefinition):
        check_port(req.port,'/port')
        try:verify_references(req.model_dump(),store.definitions(),store.catalogs(),store.rules())
        except ValueError as exc:problem('DEFINITION_CONFLICT',str(exc))
        store.add_definition(req)
        return {'status':'registered','definition':req.model_dump()}

    @app.post('/api/modules/analyze-source')
    def analyze(req:SourceRequest):return analyze_source(req.source,req.entrypoint)

    @app.get('/api/modules/search')
    def search(q:str=''):
        terms=q.lower().split()
        return [m for m in store.modules() if all(t in str(m).lower() for t in terms)]

    @app.get('/api/modules/{module_id}/versions/{version}')
    def inspect(module_id:str,version:int):
        module=module_map().get(f'{module_id}@{version}')
        if not module:problem('NOT_FOUND','模块版本不存在',404)
        return {'module':module.model_dump(),'fingerprint':digest(module.model_dump())}

    @app.post('/api/modules/replacement-plan')
    def plan(req:ReplacementRequest):return replacement(req,module_map())

    @app.post('/api/harness/prepare-change')
    async def prepare(req:ContextRequest):
        registry=module_map()
        report=validate_workflow(req.workflow,registry,req.inputs)
        if not report['valid']:return {'status':'invalid','validation':report}
        node=next((n for n in req.workflow.nodes if n.id==req.node_id),None)
        if not node or node.kind!='compute':problem('TARGET_NODE','首版一次修改当前图中的一个计算节点')
        base=registry[f'{node.module_id}@{node.module_version}']
        baseline=None
        if req.inputs is not None:
            baseline=await asyncio.to_thread(worker,{'workflow':req.workflow.model_dump(),'inputs':req.inputs,'modules':[m.model_dump() for m in registry.values()]},10)
        actual=next((t for t in (baseline or {}).get('trace',[]) if t['node_id']==req.node_id and t.get('status')=='succeeded'),None)
        packet={'protocol_version':'2.0','instruction':req.instruction,'node_id':req.node_id,
                'workflow':req.workflow.model_dump(),'inputs':req.inputs,'base_module':base.model_dump(),
                'registry':store.modules(),'parameter_definitions':store.definitions(),'parameter_catalogs':store.catalogs(),'naming_rules':store.rules(),'numerical':numeric.catalog(),
                'source_analysis':analyze_source(req.source) if req.source else None,'uploaded_source':req.source,
                'baseline':baseline,'target_sample':{'inputs':actual['inputs'],'expected':actual['outputs'],'tolerance':1e-8} if actual else None,
                'incoming_bindings':{k:v.model_dump() for k,v in node.inputs.items()},
                'downstream_consumers':[{'node_id':n.id,'input':k,'binding':b.model_dump()} for n in req.workflow.nodes for k,b in n.inputs.items() if b.source=='node' and b.node==node.id],
                'workflow_output_consumers':{k:b.model_dump() for k,b in req.workflow.outputs.items() if b.source=='node' and b.node==node.id},
                'suggested_revision':max(m.version for m in registry.values() if m.id==base.id)+1,
                'rules':RULES,'candidate_schema':CandidateRequest.model_json_schema(),
                'steps':['Resolve declared evidence; batch missing semantics once','Produce module + adapter + expected anchors','Submit candidate with context_id','Inspect replacement-plan and validate/run returned workflow','Save with expected_revision; use pinned module versions']}
        return {'status':'prepared','context_id':store.put_context(packet),'context':packet}

    @app.post('/api/harness/submit-candidate')
    async def submit(req:CandidateRequest):
        check_module(req.module)
        try:verify_references(req.module.model_dump(),store.definitions(),store.catalogs(),store.rules())
        except ValueError as exc:problem('DEFINITION_CONFLICT',str(exc))
        registry=module_map()
        latest=max((m.version for m in registry.values() if m.id==req.module.id),default=0)
        if req.module.version!=latest+1:problem('REVISION_CONFLICT',f'请提交 v{latest+1}',409)
        context=store.context(req.context_id) if req.context_id else None
        if req.context_id and not context:problem('CONTEXT_NOT_FOUND','任务上下文不存在',404)
        if context:
            frozen={f"{m['id']}@{m['version']}":digest(m) for m in context['registry']}
            current={k:digest(m.model_dump()) for k,m in registry.items()}
            if frozen!=current or context['parameter_definitions']!=store.definitions() or context.get('parameter_catalogs',store.catalogs())!=store.catalogs() or context.get('naming_rules',store.rules())!=store.rules():problem('STALE_CONTEXT','模块或参数目录已改变，请重新准备任务',409)
        for anchor in req.anchors:
            if anchor.expected.keys()!=req.module.outputs.keys():problem('ANCHOR_OUTPUT','注册样例必须覆盖所有输出')
            fill_inputs(anchor.inputs,req.module.inputs,'/anchors/inputs')
            for key,value in anchor.expected.items():validate_value(value,req.module.outputs[key],f'/anchors/expected/{key}')
        if req.module.transforms and len(req.anchors)<2:problem('TRANSFORM_EVIDENCE','显式变换至少提交两个验证样例')
        from .examples import demo
        wf,inputs=demo()
        payload={'mode':'anchors','workflow':wf.model_dump(),'inputs':inputs,'modules':[], 'module':req.module.model_dump(),'anchors':[a.model_dump() for a in req.anchors]}
        result=await asyncio.to_thread(worker,payload,10)
        if result['status']!='succeeded':return {'status':'rejected','regression':result}
        evidence_id=store.put_context({'kind':'registration_evidence','module':req.module.model_dump(),'anchors':[a.model_dump() for a in req.anchors],'regression':result,'context_id':req.context_id})
        store.add_module(req.module)
        plans=[]
        if context:
            wf=Workflow.model_validate(context['workflow']);updated=module_map()
            for node in wf.nodes:
                if node.kind=='compute':
                    p=replacement(ReplacementRequest(workflow=wf,inputs=context['inputs'],node_id=node.id,module_id=req.module.id,module_version=req.module.version),updated)
                    plans.append({'node_id':node.id,**p})
        return {'status':'registered','module':req.module.model_dump(),'regression':result,'evidence_id':evidence_id,'replacement_plans':plans}

    @app.get('/api/harness/artifacts/{artifact_id}')
    def artifact(artifact_id:str):
        value=store.context(artifact_id)
        if not value:problem('NOT_FOUND','任务或验证证据不存在',404)
        return value

    @app.get('/api/harness/registration-contract')
    def contract():
        return {'protocol_version':'2.0','connected':False,'dictionary_protocol':'/api/harness/dictionary-contract','selected_change_protocol':'/api/harness/change-task-contract','rules':RULES,'schemas':{
            'prepare_change':ContextRequest.model_json_schema(),'submit_candidate':CandidateRequest.model_json_schema(),
            'replacement_plan':ReplacementRequest.model_json_schema(),'source_analysis':SourceRequest.model_json_schema(),
            'parameter_definition':ParameterDefinition.model_json_schema()},'tools':[
            {'name':'prepare_change','method':'POST','path':'/api/harness/prepare-change'},
            {'name':'submit_candidate','method':'POST','path':'/api/harness/submit-candidate'},
            {'name':'analyze_source','method':'POST','path':'/api/modules/analyze-source'},
            {'name':'replacement_plan','method':'POST','path':'/api/modules/replacement-plan'},
            {'name':'module_search','method':'GET','path':'/api/modules/search'},
            {'name':'parameter_definitions','method':'GET','path':'/api/parameter-definitions'}]}
