"""Selected-node change tasks: validate before atomically publishing any changes."""
import asyncio
import re
import uuid
import math
from .models import (ChangeTaskRequest,ChangeApplyRequest,ClarifyRequest,AnswerRequest,
                     Module,Workflow,Binding,Anchor,Port)
from .engine import check_module,compatible,WorkflowError,validate_workflow,fill_inputs,validate_value
from .registration import RULES,verify_references
from .storage import digest,now
from . import numeric

DEMO_LABEL='平台自带手算示例，待课题组核对'


def mismatch(actual,expected,tolerance,path='/outputs'):
    if isinstance(expected,dict):
        if not isinstance(actual,dict) or actual.keys()!=expected.keys():return path
        for key,value in expected.items():
            error=mismatch(actual[key],value,tolerance,path+'/'+key)
            if error:return error
    elif isinstance(expected,list):
        if not isinstance(actual,list) or len(actual)!=len(expected):return path
        for i,(a,b) in enumerate(zip(actual,expected)):
            error=mismatch(a,b,tolerance,path+'/'+str(i))
            if error:return error
    elif type(expected) in (int,float):
        if type(actual) not in (int,float) or not math.isclose(actual,expected,rel_tol=tolerance,abs_tol=tolerance):return path
    elif actual!=expected:return path
    return None


def physical_declaration(value):
    if isinstance(value,dict):return {k:physical_declaration(v) for k,v in value.items() if k not in ('description','chinese_name','semantic_evidence')}
    if isinstance(value,list):return [physical_declaration(v) for v in value]
    return value


def locate(workflow,path):
    graph=workflow;offset=0
    while offset<len(path):
        node=next((n for n in graph.nodes if n.id==path[offset]),None)
        if node is None:raise ValueError(f'节点路径不存在：{path}')
        offset+=1
        if offset==len(path):
            if node.kind!='compute':raise ValueError('必须选择一个计算节点')
            return graph,node
        kind=path[offset];offset+=1
        if kind=='body' and node.body:graph=node.body
        elif kind=='cases' and offset<len(path) and path[offset] in node.cases:
            graph=node.cases[path[offset]];offset+=1
        else:raise ValueError('子图路径必须显式指定 body 或 cases/分支名')
    raise ValueError('缺少目标计算节点')


def alpha_candidate(base,version):
    if base.id!='switch_loss' or base.implementation!='expressions' or 'Tj' not in base.inputs or base.expressions.get('p_sw')!='e_sw * fs':
        raise ValueError('手算模板只适用于包含 Tj 的简化 switch_loss 演示模块')
    pattern=r'\(vce \+ [0-9.eE+-]+ \* \(Tj - 25\)\) \* i_rms \* 0\.5'
    if not re.fullmatch(pattern,base.expressions.get('p_cond','')) or base.expressions.get('p_total')!=base.expressions['p_cond']+' + e_sw * fs':
        raise ValueError('原公式超出手算模板范围；请提供真实 DSH 候选及验收依据')
    candidate=base.model_copy(deep=True);candidate.version=version;candidate.contract_version='2.0'
    candidate.inputs['alpha']=Port(type='number',quantity='voltage_temperature_coefficient',unit='V/degC',
        unit_definition={'dimension':'voltage_temperature_coefficient','scale':1},description='导通压降温度系数；由用户明确给出')
    candidate.expressions['p_cond']='(vce + alpha * (Tj - 25)) * i_rms * 0.5'
    candidate.expressions['p_total']=candidate.expressions['p_cond']+' + e_sw * fs'
    candidate.description=DEMO_LABEL+'；alpha 为显式输入，开关损耗公式不变'
    candidate.provenance=base.provenance+'；'+DEMO_LABEL
    return candidate


def install(app,get_store,module_map,worker,problem):
    def task_or_fail(key):
        task=get_store().task(key)
        if not task:problem('TASK_NOT_FOUND','修改任务不存在',404)
        return task

    @app.post('/api/change-tasks')
    async def prepare(req:ChangeTaskRequest):
        store=get_store();registry=module_map()
        try:verify_references(req.workflow.model_dump(),store.definitions(),store.catalogs(),store.rules())
        except ValueError as exc:problem('DEFINITION_CONFLICT',str(exc))
        try:graph,node=locate(req.workflow,req.node_path)
        except ValueError as exc:problem('TARGET_PATH',str(exc))
        report=validate_workflow(req.workflow,registry,req.inputs or {})
        if not report['valid']:return {'status':'invalid','diagnostics':report['diagnostics']}
        current=store.workflow(req.workflow.id)
        if (current['revision'] if current else 0)!=req.expected_revision:problem('REVISION_CONFLICT','工作流版本已改变，请重新加载',409)
        base=registry[f'{node.module_id}@{node.module_version}']
        version=max(m.version for m in registry.values() if m.id==base.id)+1
        suggested=None;anchors=req.anchors;bindings=req.added_bindings
        unchanged=req.unchanged_outputs;label=req.acceptance_label
        if req.alpha_demo:
            try:suggested=alpha_candidate(base,version)
            except ValueError as exc:problem('DEMO_SCOPE',str(exc))
            if req.alpha is None:
                return {'status':'needs_clarification','questions':{'alpha':'请明确 alpha 的数值（V/℃）；没有明确值时不能自动填入接线。'},'label':DEMO_LABEL}
            if req.alpha!=.002:problem('DEMO_ANCHORS','手算样例目前只覆盖 alpha=0.002 V/℃；其他数值请提交自己的验收样例')
            # Explicit alpha form field is user evidence. Never extract an arbitrary number from prose.
            bindings={'alpha':Binding(source='constant',value=req.alpha)};unchanged=['p_sw'];label=DEMO_LABEL
            common={'i_rms':8,'vce':1.7,'e_sw':.001,'fs':100000,'alpha':.002}
            anchors=[Anchor(inputs={**common,'Tj':25},expected={'p_cond':6.8,'p_sw':100,'p_total':106.8}),
                     Anchor(inputs={**common,'Tj':125},expected={'p_cond':7.6,'p_sw':100,'p_total':107.6})]
        if len(anchors)<2:problem('ACCEPTANCE_REQUIRED','请提供至少两个完整的模块验收样例')
        if set(unchanged)-base.outputs.keys():problem('UNCHANGED_OUTPUT','保持不变的输出必须属于原模块')
        for name in bindings:
            if name in base.inputs:problem('BINDING_SCOPE','本次新增接线不能改写原有输入来源')
        if req.added_input_ports and req.added_input_ports.keys()!=bindings.keys():problem('ADDED_INPUT_CONTRACT','明确声明的新输入须覆盖所有新增绑定')
        for case in req.evaluation_cases:
            if case.added_bindings.keys()!=bindings.keys() or any(b.source!='constant' for b in case.added_bindings.values()):problem('CASE_BINDING_SCOPE','工况仅允许覆盖本次明确新增的常量绑定')
            if case.expected.keys()!=req.workflow.output_ports.keys():problem('CASE_EXPECTED','完整工况必须覆盖全部工作流输出')
            fill_inputs(case.expected,req.workflow.output_ports,'/evaluation_cases/expected')
        baseline_payload={'workflow':req.workflow.model_dump(),'inputs':req.inputs or {},'modules':[m.model_dump() for m in registry.values()]}
        baseline=await asyncio.to_thread(worker,baseline_payload,req.timeout_seconds)
        if baseline['status']!='succeeded':return {'status':'baseline_failed','diagnostics':baseline.get('diagnostics',[]),'baseline':baseline}
        path='/'.join(req.node_path)
        def trace_path(parts):
            return '/'.join(p for p in parts if p not in ('body','cases'))
        # foreach trace paths contain iteration indexes; keep all matching samples.
        expected_parts=trace_path(req.node_path).split('/')
        samples=[]
        for trace in baseline['trace']:
            parts=[p for p in trace['path'].split('/') if not p.isdigit()]
            if parts==expected_parts:samples.append({'inputs':trace['inputs'],'expected':trace['outputs']})
        packet={'protocol_version':'3.0','task_kind':'selected_node_formula_change','instruction':req.instruction,
            'node_path':req.node_path,'workflow':req.workflow.model_dump(),'inputs':req.inputs or {},'expected_revision':req.expected_revision,
            'base_module':base.model_dump(),'next_module_version':version,'registry':store.modules(),'parameter_definitions':store.definitions(),'parameter_catalogs':store.catalogs(),'naming_rules':store.rules(),
            'baseline':baseline,'target_samples':samples,'anchors':[a.model_dump() for a in anchors],
            'acceptance_label':label,'unchanged_outputs':unchanged,'authorized_added_bindings':{k:b.model_dump() for k,b in bindings.items()},
            'evaluation_cases':[case.model_dump() for case in req.evaluation_cases],
            'authorized_added_ports':{k:p.model_dump() for k,p in req.added_input_ports.items()},
            'template_specification':'Vce(Tj)=vce+alpha*(Tj-25); P_cond=Vce(Tj)*i_rms*0.5; P_sw=e_sw*fs; P_total=P_cond+P_sw; alpha=0.002 V/degC' if req.alpha_demo else None,
            'target_bindings':{k:b.model_dump() for k,b in node.inputs.items()},
            'downstream_consumers':[{'node':n.id,'input':k,'binding':b.model_dump()} for n in graph.nodes for k,b in n.inputs.items() if b.source=='node' and b.node==node.id],
            'output_consumers':{k:b.model_dump() for k,b in graph.outputs.items() if b.source=='node' and b.node==node.id},
            'numerical':numeric.catalog(),'rules':{**RULES,'response':'Return ChangeApplyRequest JSON. Do not call legacy module registration: platform applies module + selected-node wiring atomically.','scope':'Only this node_path; retain all old inputs, outputs and bindings. Additional bindings must equal authorized_added_bindings.','clarification':'Call task clarification once for all critical gaps; unresolved questions block candidate application.'},
            'candidate_schema':ChangeApplyRequest.model_json_schema(),
            'execution_plan':['Read frozen task evidence','Return candidate module and exactly the authorized added bindings','Platform checks module samples and unchanged outputs','Platform validates and executes complete candidate workflow','Atomically publish module, selected-node wiring, graph revision and frozen run']}
        context_id=store.put_context(packet)
        task={'id':uuid.uuid4().hex,'status':'awaiting_candidate','created_at':now(),'context_id':context_id,
              'expected_revision':req.expected_revision,'registry_fingerprint':digest(store.modules()),'definitions_fingerprint':digest(store.definitions()),
              'label':label,'instruction':req.instruction,'node_path':req.node_path,'timeout_seconds':req.timeout_seconds,'clarification_used':False,
              'workflow_id':req.workflow.id,'baseline_outputs':baseline['outputs'],'catalogs_fingerprint':digest(store.catalogs()),'rules_fingerprint':digest(store.rules())}
        task['demo_candidate']={'context_id':context_id,'module':suggested.model_dump(),'added_bindings':packet['authorized_added_bindings']} if suggested else None
        task['workspace_epoch']=store.workflow_epoch(req.workflow.id)
        store.save_task(task)
        return {**task,'context':packet}

    @app.get('/api/change-tasks')
    def tasks():return get_store().tasks()

    @app.get('/api/change-tasks/{task_id}')
    def read(task_id:str):
        task=task_or_fail(task_id)
        return {**task,'context':get_store().context(task['context_id'])}

    @app.post('/api/change-tasks/{task_id}/clarify')
    def clarify(task_id:str,req:ClarifyRequest):
        task=task_or_fail(task_id)
        if task['status']=='applied' or task['clarification_used']:problem('CLARIFICATION_LIMIT','该任务已应用或已使用一次集中追问；未解关键项请报告阻断',409)
        task.update(status='needs_clarification',clarification_used=True,questions=req.questions)
        get_store().save_task(task)
        return task

    @app.post('/api/change-tasks/{task_id}/answer')
    def answer(task_id:str,req:AnswerRequest):
        task=task_or_fail(task_id)
        if task['status']!='needs_clarification' or req.answers.keys()!=task['questions'].keys() or any(not a.strip() for a in req.answers.values()):problem('ANSWER_REQUIRED','必须回答本轮全部关键问题')
        packet=get_store().context(task['context_id']);packet['clarification']={'questions':task['questions'],'answers':req.answers}
        task.update(status='awaiting_candidate',answers=req.answers,context_id=get_store().put_context(packet))
        get_store().save_task(task)
        return {**task,'context':packet}

    @app.post('/api/change-tasks/{task_id}/apply')
    async def apply(task_id:str,req:ChangeApplyRequest):
        store=get_store();task=task_or_fail(task_id)
        candidate_hash=digest(req.model_dump())
        if task['status']=='applied':
            if task.get('candidate_hash')!=candidate_hash:problem('TASK_APPLIED','任务已应用，不能再提交不同候选',409)
            record=store.run(task['run_id']);saved=store.workflow(task['workflow_id'],task['revision'])
            return {**task,**saved,'run':{k:v for k,v in record.items() if k!='snapshot'}}
        if task['status']=='needs_clarification':problem('UNRESOLVED_SEMANTICS','关键问题尚未全部回答')
        if req.context_id!=task['context_id']:problem('STALE_CONTEXT','请使用最新任务上下文',409)
        if ('catalogs_fingerprint' in task and digest(store.catalogs())!=task['catalogs_fingerprint']) or ('rules_fingerprint' in task and digest(store.rules())!=task['rules_fingerprint']):problem('STALE_CONTEXT','词典或命名规则已改变，请重新创建任务',409)
        if digest(store.modules())!=task['registry_fingerprint'] or digest(store.definitions())!=task['definitions_fingerprint']:problem('STALE_CONTEXT','目录已改变，请重新创建任务',409)
        current=store.workflow(task['workflow_id'])
        if (current['revision'] if current else 0)!=task['expected_revision']:problem('REVISION_CONFLICT','工作流已改变，请重新创建任务',409)
        packet=store.context(task['context_id']);wf=Workflow.model_validate(packet['workflow']);graph,node=locate(wf,packet['node_path'])
        base=Module.model_validate(packet['base_module']);module=req.module
        def reject(stage,diagnostics,**details):
            task.update(status='rejected',stage=stage,diagnostics=diagnostics,**details);store.save_task(task)
            return task
        try:
            if module.id!=base.id or module.version!=packet['next_module_version']:problem('CANDIDATE_VERSION','必须按冻结上下文派生同模块族的指定新版本')
            check_module(module);verify_references(module.model_dump(),store.definitions(),store.catalogs(),store.rules())
            fields=('inputs','expressions','source','adapter','implementation','entrypoint')
            if all(module.model_dump()[k]==base.model_dump()[k] for k in fields):problem('NO_IMPLEMENTATION_CHANGE','候选仍是原实现；请提交实际公式修改，不能用结构骨架完成任务')
            if not base.inputs.keys()<=module.inputs.keys() or module.outputs.keys()!=base.outputs.keys():problem('CHANGE_SCOPE','本任务须保留原有输入与全部输出')
            for key in base.inputs:compatible(base.inputs[key],module.inputs[key],f'/module/inputs/{key}');compatible(module.inputs[key],base.inputs[key],f'/module/inputs/{key}')
            for key in base.outputs:compatible(module.outputs[key],base.outputs[key],f'/module/outputs/{key}');compatible(base.outputs[key],module.outputs[key],f'/module/outputs/{key}')
            actual_bindings={k:b.model_dump() for k,b in req.added_bindings.items()}
            if actual_bindings!=packet['authorized_added_bindings']:problem('UNAUTHORIZED_BINDING','新增接线必须与用户明确声明一致，不能自行填值')
            if set(req.added_bindings)!=module.inputs.keys()-base.inputs.keys():problem('MISSING_BINDING','新增输入必须全部显式接线')
            for key,port in packet.get('authorized_added_ports',{}).items():
                if physical_declaration(module.inputs[key].model_dump())!=physical_declaration(port):problem('ADDED_INPUT_CONTRACT',f'新增字典 {key} 的物理声明必须与任务给定声明一致')
            anchors=packet['anchors']
            for anchor in anchors:
                if anchor['expected'].keys()!=module.outputs.keys():problem('ANCHOR_OUTPUT','验收样例必须覆盖全部输出')
                fill_inputs(anchor['inputs'],module.inputs,'/anchors/inputs')
                for k,v in anchor['expected'].items():validate_value(v,module.outputs[k],f'/anchors/expected/{k}')
            node.module_version=module.version;node.inputs.update(req.added_bindings)
            registry=module_map();registry[f'{module.id}@{module.version}']=module
            report=validate_workflow(wf,registry,packet['inputs'])
            if not report['valid']:return reject('workflow_validation',report['diagnostics'])
            payload={'mode':'anchors','workflow':wf.model_dump(),'inputs':packet['inputs'],'modules':[], 'module':module.model_dump(),'anchors':anchors}
            regression=await asyncio.to_thread(worker,payload,task['timeout_seconds'])
            if regression['status']!='succeeded':return reject('anchors',regression.get('diagnostics',[]),regression=regression)
            if packet['unchanged_outputs']:
                baseline_anchors=[{'inputs':{k:v for k,v in a['inputs'].items() if k in base.inputs},'expected':{k:a['expected'][k] for k in packet['unchanged_outputs']},'tolerance':a['tolerance']} for a in anchors]
                unchanged=await asyncio.to_thread(worker,{**payload,'module':base.model_dump(),'anchors':baseline_anchors},task['timeout_seconds'])
                if unchanged['status']!='succeeded':return reject('unchanged_outputs',unchanged.get('diagnostics',[]),regression=unchanged)
            snapshot={'workflow':wf.model_dump(),'inputs':packet['inputs'],'modules':[m.model_dump() for m in registry.values()],'parameter_catalogs':packet.get('parameter_catalogs',[])}
            result=await asyncio.to_thread(worker,snapshot,task['timeout_seconds'])
            if result['status']!='succeeded':return reject('workflow_execution',result.get('diagnostics',[]),candidate_run=result)
            run={'id':uuid.uuid4().hex,'created_at':now(),'finished_at':now(),'workflow_name':wf.name,'output_ports':{k:p.model_dump() for k,p in wf.output_ports.items()},
                 'snapshot_hash':digest(snapshot),'snapshot':snapshot,'timeout_seconds':task['timeout_seconds'],'change_task_id':task_id,**result}
            case_runs=[];case_results=[]
            for case in packet.get('evaluation_cases',[]):
                case_wf=wf.model_copy(deep=True);_,case_node=locate(case_wf,packet['node_path'])
                case_node.inputs.update({k:Binding.model_validate(b) for k,b in case['added_bindings'].items()})
                case_snapshot={**snapshot,'workflow':case_wf.model_dump()}
                case_result=result if case_wf==wf else await asyncio.to_thread(worker,case_snapshot,task['timeout_seconds'])
                error=mismatch(case_result.get('outputs',{}),case['expected'],case['tolerance']) if case_result['status']=='succeeded' else '/'
                case_results.append({'name':case['name'],'passed':error is None,'outputs':case_result.get('outputs',{}),'mismatch_path':error,'acceptance_label':packet['acceptance_label']})
                if error:return reject('evaluation_cases',[{'code':'CASE_MISMATCH','message':f"完整工况 {case['name']} 验证失败；候选未发布",'path':error}],case_results=case_results,case_diagnostics=case_result.get('diagnostics',[]))
                case_runs.append({'id':uuid.uuid4().hex,'created_at':now(),'finished_at':now(),'workflow_name':wf.name+' · '+case['name'],'output_ports':run['output_ports'],'snapshot_hash':digest(case_snapshot),'snapshot':case_snapshot,'timeout_seconds':task['timeout_seconds'],'change_task_id':task_id,'acceptance_label':packet['acceptance_label'],**case_result})
            task['case_results']=case_results
            task.update(candidate_hash=candidate_hash,regression=regression,validation=report,module_ref=f'{module.id}@{module.version}',after_outputs=result['outputs'],diagnostics=[],stage='complete')
            revision=store.commit_change(module,wf,packet['inputs'],task,run,case_runs) if case_runs else store.commit_change(module,wf,packet['inputs'],task,run)
            return {**task,'workflow':wf.model_dump(),'inputs':packet['inputs'],'revision':revision,'run':{k:v for k,v in run.items() if k!='snapshot'}}
        except WorkflowError as exc:return reject('contract',[exc.diagnostic.model_dump()])
        except ValueError as exc:return reject('contract',[{'code':'CANDIDATE_CONTRACT','message':str(exc),'path':'/module','severity':'error'}])

    @app.get('/api/harness/change-task-contract')
    def contract():return {'protocol_version':'3.0','connected':False,'label':'接口候选验收；DSH 尚未接入','rules':RULES,
        'schemas':{'prepare':ChangeTaskRequest.model_json_schema(),'apply':ChangeApplyRequest.model_json_schema(),'clarify':ClarifyRequest.model_json_schema(),'answer':AnswerRequest.model_json_schema()},
        'tools':[{'name':'prepare_selected_change','method':'POST','path':'/api/change-tasks'}, {'name':'read_change_task','method':'GET','path':'/api/change-tasks/{task_id}'},
                 {'name':'clarify_once','method':'POST','path':'/api/change-tasks/{task_id}/clarify'},{'name':'answer_questions','method':'POST','path':'/api/change-tasks/{task_id}/answer'},
                 {'name':'validate_publish_run','method':'POST','path':'/api/change-tasks/{task_id}/apply'}]}
