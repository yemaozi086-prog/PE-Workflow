"""Code-audited scene metadata and bounded assembly; no model code execution."""
from pathlib import Path
import hashlib
from .models import (ScenePack, SceneBinding, SceneRule, AssemblyPreviewRequest, AssemblyContextRequest, AssemblyPlan,
                     Binding, Node, Workflow)
from .engine import fail, interface, validate_workflow
from .storage import digest


def scene():
    return ScenePack(
        id='dbsrc_eps_capacitor_candidates', version=1,
        name='DBSRC EPS 单工况候选电流与谐振电容介质损耗',
        topology='DBSRC', task_type='candidate_capacitor_loss',
        review_status='extracted_from_code',
        bindings=[
            SceneBinding(role='candidate_current', module_id='dbsrc_modulation', module_version=1,
                         evidence='dbsrc.py:modulation'),
            SceneBinding(role='capacitor_loss', module_id='dbsrc_loss_capacitor', module_version=1,
                         evidence='dbsrc.py:loss')],
        rules=[SceneRule(id=key, kind=kind, fact=key, expected=value, evidence=source)
               for key, kind, value, source in [
                   ('modulation', 'compatible_profile', 'EPS', 'dbsrc.py:current_model'),
                   ('scope', 'required_fact', 'single_workpoint', 'dbsrc.py:modulation'),
                   ('assessment', 'required_fact', 'loss_only', 'dbsrc.py:loss'),
                   ('upstream_constraints', 'required_fact', 'not_evaluated', 'dbsrc.py:loss'),
                   ('control', 'supported_control', 'finite_batch', 'engine.py:batch')]],
        limitations=[
            '仅复现冻结 WDY 模型；尚无器件实验验证。',
            '器件与设计由用户给定，不执行选型、最优候选筛选或 PPO。',
            'previous.valid=1 仅绕过旧流程上游约束门控，绝不表示约束已通过。',
            'valid=0 的数值填充不可作为有效损耗；无调制解必须明确报告。',
            '同一候选各字段按行对应，不允许重排、跨工况拼接或隐式重采样。'])


def evidence_records():
    root=Path(__file__).parent
    files={'dbsrc.py':root/'dbsrc.py','engine.py':root/'engine.py',
           'capacitor_loss_cal.py':root/'science/dbsrc_v1/component_loss_cal/capacitor_loss_cal/capacitor_loss_cal.py'}
    return {name:{'file':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                  'review_status':'extracted_from_code'} for name,path in files.items()}


def context(instruction, facts, modules):
    pack=scene()
    checks=[]
    for rule in pack.rules:
        actual=facts.get(rule.fact)
        checks.append({'rule_id':rule.id, 'expected':rule.expected, 'actual':actual,
                       'status':'passed' if actual==rule.expected else ('needs_clarification' if actual is None else 'error'),
                       'evidence':rule.evidence})
    candidates=[]
    for binding in pack.bindings:
        key=f'{binding.module_id}@{binding.module_version}'
        if key not in modules:fail('CAPABILITY_MISSING',f'缺少已注册模块 {key}','/scene/bindings')
        module=modules[key]
        candidates.append({'role':binding.role,'module':module.model_dump(exclude_defaults=True),
                           'evidence':binding.evidence})
    return {'protocol':'assembly_plan_v1','instruction':instruction,
            'scene':pack.model_dump(), 'scene_hash':digest(pack.model_dump()),
            'facts':facts, 'rule_report':checks, 'candidates':candidates,
            'evidence':evidence_records(), 'plan_schema':AssemblyPlan.model_json_schema(),
            'permissions':{'generate_code':False,'register_module':False,'publish_workflow':False},
            'binding_format':{
                'input_dictionary':{'source':'input','port':'dictionary_name'},
                'node_dictionary':{'source':'node','node':'upstream_step_id','port':'output_dictionary_name'},
                'explicit_dictionary_constant':{'source':'constant','value':{'valid':1}},
                'notes':['port 表示整个字典的端口；field 仅用于字典内部字段，不能替代 port。',
                         '扁平字典输入必须传字典；常量绑定不能含 node/port/field 地址。',
                         '每个步骤 inputs 键必须严格等于所选模块 inputs 键；不得为损耗模块额外添加 capacitor 输入。']},
            'role_notes':{
                'candidate_current':'本次仅一个给定工况；候选数组轴是调制候选，不是 7×4 工况。模块旧通用描述不代表本次任务范围。',
                'capacitor_loss':'只接收 candidate、settings、previous。电容值和 RMS 电流已在 candidate 内，不另接 capacitor。'},
            'status':('ready' if all(c['status']=='passed' for c in checks) else
                      'blocked' if any(c['status']=='error' for c in checks) else 'needs_clarification'),
            'instructions':[
                '仅提交装配计划，不能提供新模块、代码、契约或声称完成平台验证。',
                '选择两个职责的固定版本模块；candidate_current 使用同名任务输入。',
                'capacitor_loss.candidate 必须引用所选 candidate_current 的 candidates 输出。',
                '损耗其他输入来自同名任务输入；previous 必须显式常量 {valid:1}，其含义仅为未评价上游约束。',
                '平台将逐候选损耗编译为有限 batch；不能用第一行代替所有候选。',
                '解释引用 scene 中真实的 evidence 标识；关键条件缺失时集中提问，不能猜测。']}


def compile_plan(request: AssemblyPreviewRequest, modules):
    packet=context(request.instruction,request.facts,modules)
    if packet['status']!='ready':
        fail('SCENE_FACTS_REQUIRED','场景条件缺失或不兼容；请查看规则报告','/facts')
    roles={s.role:s for s in request.plan.steps}
    if set(roles)!={'candidate_current','capacitor_loss'}:
        fail('REQUIRED_ROLE','必须各包含一个候选电流和电容损耗职责','/plan/steps')
    if len({s.id for s in request.plan.steps})!=2:
        fail('DUPLICATE_NODE','装配步骤 ID 必须唯一','/plan/steps')
    if any(s.id in ('candidate_losses','$input','$output') for s in request.plan.steps):
        fail('RESERVED_NODE','步骤 ID 与平台保留节点冲突','/plan/steps')
    allowed={b.role:(b.module_id,b.module_version,b.evidence) for b in scene().bindings}
    for step in request.plan.steps:
        mid,version,ev=allowed[step.role]
        if (step.module_id,step.module_version)!=(mid,version):
            fail('INCOMPATIBLE_PROFILE','所选版本不在本次冻结的场景能力绑定中','/plan/steps')
        available={b.evidence for b in scene().bindings}|{r.evidence for r in scene().rules}|set(evidence_records())
        if ev not in step.evidence or any(e not in available for e in step.evidence):
            fail('EVIDENCE_REQUIRED',f'本职责须引用 {ev}；可补充的真实依据为 {sorted(available)}','/plan/steps')
    current,loss=roles['candidate_current'],roles['capacitor_loss']
    cm=modules[f'{current.module_id}@{current.module_version}']
    lm=modules[f'{loss.module_id}@{loss.module_version}']
    expected_current={k:Binding(source='input',port=k) for k in cm.inputs}
    if current.inputs!=expected_current:
        fail('INPUT_PROVENANCE','候选电流必须使用同名给定设计、工况和器件输入','/plan/steps')
    expected_loss={
        'candidate':Binding(source='node',node=current.id,port='candidates'),
        'settings':Binding(source='input',port='settings'),
        'previous':Binding(source='constant',value={'valid':1.})}
    if loss.inputs!=expected_loss:
        extra=sorted(set(loss.inputs)-set(expected_loss));missing=sorted(set(expected_loss)-set(loss.inputs))
        fail('CANDIDATE_PROVENANCE',f'损耗输入必须严格为 candidate/settings/previous；多余={extra}，缺少={missing}。'
             f'所需绑定={ {k:v.model_dump(exclude_none=True,exclude_defaults=True) for k,v in expected_loss.items()} }。'
             '电容参数已在 candidate 字典内，不能另接 capacitor；previous 必须是字典常量，不能含 field 地址。',
             '/plan/steps/capacitor_loss/inputs')
    ports={**cm.inputs,'settings':lm.inputs['settings']}
    body=Workflow(schema_version='3.0',id='candidate_capacitor_body',name='单候选谐振电容介质损耗',
        inputs={'item':lm.inputs['candidate'],'settings':lm.inputs['settings']},
        nodes=[Node(id=loss.id,label='谐振电容介质损耗',module_id=loss.module_id,module_version=loss.module_version,
                    inputs={**loss.inputs,'candidate':Binding(source='input',port='item')})],
        outputs={'loss':Binding(source='node',node=loss.id,port='loss')},output_ports=lm.outputs)
    batch=Node(id='candidate_losses',label='逐调制候选计算电容损耗',kind='batch',body=body,
               inputs={'rows':loss.inputs['candidate'],'settings':loss.inputs['settings']})
    _,batch_outputs=interface(batch,modules)
    workflow=Workflow(schema_version='3.0',id='dbsrc_capacitor_assembly',name=scene().name,
        description='仅损耗计算，不代表方案可行；来源 WDY EPS 冻结模型。',inputs=ports,
        nodes=[Node(id=current.id,label='EPS 调制候选与电流特征',module_id=current.module_id,
                    module_version=current.module_version,inputs=current.inputs),batch],
        outputs={'candidates':Binding(source='node',node=current.id,port='candidates'),
                 'loss':Binding(source='node',node=batch.id,port='loss')},
        output_ports={**cm.outputs,**batch_outputs},
        layout={'$input':{'x':-30.,'y':100.},current.id:{'x':330.,'y':100.},
                batch.id:{'x':660.,'y':100.},'$output':{'x':990.,'y':100.}})
    report=validate_workflow(workflow,modules,request.inputs)
    if not report['valid']:
        from .engine import WorkflowError
        from .models import Diagnostic
        raise WorkflowError(Diagnostic.model_validate(report['diagnostics'][0]))
    return {'status':'preview_ready','workflow':workflow.model_dump(),'inputs':request.inputs,
            'configuration':packet,'decision_summary':request.plan.model_dump(),
            'validation':{'interface':'passed','domain_rules':packet['rule_report'],
                          'execution':'not_run','reference':'not_checked',
                          'engineering_applicability':'needs_review'},
            'experience':{'status':'candidate','review_required':True,'scope':scene().task_type,
                          'limitations':scene().limitations}}


def install(app,module_map):
    @app.get('/api/examples/assembly-inputs')
    def sample_inputs():
        import json
        from . import dbsrc
        _,_,original=dbsrc.fixture()
        design=dbsrc.action({'action':original['action']})['design']
        electrical=dbsrc.sizing({'design':design,'settings':original['settings']})['electrical']
        inputs={'design':design,'settings':original['settings'],
                'point':{'Vin':original['settings']['Vin'],'Vout':350.,'Pout':1000.,'vi':0.,'pi':0.}}
        for kind in ('transformer','inductor','switch','capacitor'):
            inputs[kind]=dbsrc.selection({'electrical':electrical},kind)['component']
        return json.loads(json.dumps({'inputs':inputs,'facts':{r.fact:r.expected for r in scene().rules},
            'instruction':'给定设计、器件与单个工况，使用 DBSRC EPS 模型计算全部调制候选的谐振电容介质损耗。仅计算损耗，不判断方案可行性。',
            'label':'平台示例：器件输入来自冻结旧程序，电容器件可缺失；仅用于兼容性演示，待课题组核对。'},default=lambda v:v.item()))
    @app.get('/api/scenes')
    def list_scenes():
        return {'items':[scene().model_dump()],'count':1}

    @app.post('/api/assembly/context')
    def task_context(request: AssemblyContextRequest):
        packet=context(request.instruction,request.facts,module_map())
        packet['inputs']=request.inputs
        packet['context_hash']=digest(packet)
        return packet

    @app.post('/api/assembly/preview')
    def preview(request: AssemblyPreviewRequest):
        return compile_plan(request,module_map())
