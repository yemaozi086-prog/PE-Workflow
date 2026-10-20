"""Compile only explicitly declared connections; never infer a physical source."""
import copy
import json
import uuid
from .models import DesignPlan, Module, Workflow, Node, Binding, BindingAssemblyConfirm
from .storage import Conflict, canonical, digest, now
from .engine import validate_workflow
from .plan_audit import preflight
from .repair_routing import budget_snapshot


def assemble(task, registry):
    plan=DesignPlan.model_validate(task['plan'])
    if plan.grids or plan.loops:
        from .plan_controls import compile_plan
        issues=list(preflight(plan)['issues'])
        for step in plan.steps:
            if not step.module_ref or step.module_ref not in registry:issues.append({'code':'BINDING_MODULE_MISSING','message':'缺少明确的已注册版本：'+step.id,'path':'/steps/'+step.id+'/module_ref'})
        workflow=None;modules={}
        if not issues:
            try:
                workflow=compile_plan(plan,'design_'+task['id'],task['request'].get('project_id','local'),registry)
                modules={s.module_ref:registry[s.module_ref] for s in plan.steps}
                report=validate_workflow(workflow,modules)
                if not report['valid']:issues.extend(report['diagnostics'])
            except Exception as error:
                # Surface compiler failures; never fall back to guessed flat wiring.
                issues.append({'code':'CONTROL_ASSEMBLY','message':str(error),'path':'/plan/controls'})
        return {'valid':not issues,'issues':issues,'workflow':workflow.model_dump() if workflow and not issues else None,
                'modules':[m.model_dump() for m in modules.values()],
                'notice':'程序按显式网格、循环成员与捕获输入构造原生子图；未运行或验证物理模型。'}
    issues=list(preflight(plan)['issues']);modules={};nodes=[];outputs={};ports={}
    for step in plan.steps:
        if not step.module_ref or step.module_ref not in registry:
            issues.append({'code':'BINDING_MODULE_MISSING','message':'缺少明确的已注册版本：'+str(step.module_ref),'path':'/steps/'+step.id+'/module_ref'});continue
        module=registry[step.module_ref];modules[step.module_ref]=module
        if step.inputs!=module.inputs or step.outputs!=module.outputs:
            issues.append({'code':'BINDING_CONTRACT_CHANGED','message':'方案接口与冻结模块声明不一致，请先核对修订','path':'/steps/'+step.id});continue
        if set(step.input_sources)!=set(step.inputs):
            issues.append({'code':'BINDING_SOURCE_REQUIRED','message':'缺少明确来源，不按名称或单位猜测连接','path':'/steps/'+step.id+'/input_sources'});continue
        nodes.append(Node(id=step.id,label=step.title,module_id=module.id,module_version=module.version,
                          inputs=copy.deepcopy(step.input_sources),input_checks=list(step.input_checks)))
        # Expose each complete dictionary with a collision-free, reviewable alias.
        for name,port in step.outputs.items():
            alias=step.id+'__'+name
            if alias in outputs:
                issues.append({'code':'BINDING_OUTPUT_COLLISION','message':'结果别名冲突，请先明确结果命名','path':'/outputs/'+alias});continue
            outputs[alias]=Binding(source='node',node=step.id,port=name);ports[alias]=copy.deepcopy(port)
    workflow=Workflow(schema_version='3.0',id='design_'+task['id'],name=plan.title,
        description=plan.summary,project_id=task['request'].get('project_id','local'),inputs=copy.deepcopy(plan.external_inputs),
        nodes=nodes,outputs=outputs,output_ports=ports)
    if not issues:
        report=validate_workflow(workflow,modules)
        issues.extend(report['diagnostics'] if not report['valid'] else [])
    return {'valid':not issues,'issues':issues,'workflow':workflow.model_dump() if not issues else None,
            'modules':[m.model_dump() for m in modules.values()],
            'notice':'按确认来源复制接线与保护条件；预览列出每步完整输出字典，不填运行默认值。未进行数值运行或工程正确性验证。'}


class Assembly:
    def __init__(self,service):self.service=service

    def preview(self,key):
        t=self.service.get(key)
        if t.get('archived') or not t.get('plan'):raise Conflict('请打开未归档的计算方案')
        registry={f'{m["id"]}@{m["version"]}':Module.model_validate(m) for m in t['frozen_modules']} if t.get('frozen_modules') else self.service.modules()
        data=assemble(t,registry)
        data.update(source_id=key,revision=t['revision'],calls=t.get('calls',0),remaining_calls=max(0,t.get('call_limit',4)-t.get('calls',0)))
        data['preview_hash']=digest(data)
        return data

    def confirm(self,key,req):
        old=self.service.get(key);receipt_key=key+':'+digest(req.model_dump())
        with self.service.connect() as con:
            row=con.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.binding_assembly_receipt')=?",(receipt_key,)).fetchone()
        if row:return self.service.public(json.loads(row[0]))
        self.service.check_revision(old,req.revision)
        if old['status'] in ('dispatch_ready','dispatching','awaiting_worker'):raise Conflict('请先明确停止旧调用或等待结果，再切换到程序组装')
        if old['status'] not in ('awaiting_confirmation','missing_tools','paused','blocked','needs_clarification','delivery_unknown'):raise Conflict('当前阶段不能重新组装')
        view=self.preview(key)
        if view['preview_hash']!=req.preview_hash:raise Conflict('接线预览或模块版本已变化，请重新核对')
        if not view['valid']:raise Conflict('存在未确定来源或接口问题，请先补齐方案')
        child_id=uuid.uuid4().hex;workflow=copy.deepcopy(view['workflow']);workflow['id']='design_'+child_id
        child={'id':child_id,'parent_id':key,'revision':1,'phase':'bind','status':'ready_to_save','request':copy.deepcopy(old['request']),
               'plan':copy.deepcopy(old['plan']),'workflow':workflow,'frozen_modules':view['modules'],
               'user_selected_unverified':True,'assembly_origin':'platform_explicit_bindings','binding_assembly_receipt':receipt_key,
               'stage_budgets':budget_snapshot(old),'total_calls':old.get('total_calls',old.get('calls',0)),
               'calls':old.get('calls',0) if old.get('phase')=='bind' else 0,'created_at':now(),'updated_at':now(),
               'validation':{'structure':'passed','execution':'not_run','scientific_applicability':'user_review_required'},
               'message':'已按确认的来源完成程序组装；未调用模型。请核对后另行保存，尚未运行。',
               'events':[{'event':'user_confirmed_platform_assembly','actor':'user','at':now(),'revision':1,'preview_hash':req.preview_hash}]}
        if old.get('phase')=='bind' and 'call_limit' in old:child['call_limit']=old['call_limit']
        with self.service.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            live=json.loads(con.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()[0])
            if live['revision']!=req.revision:raise Conflict('任务已更新，请重新核对')
            # A late callback is retained separately, never allowed to replace this revision.
            live.update(superseded_by=child_id,status='paused',message='已切换到确认后的程序组装修订；旧调用迟到结果仅留作记录')
            from .completion_recovery import Recovery
            Recovery.persist(con,live,'user_switched_to_platform_assembly')
            con.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child_id,1,canonical(child)))
        return self.service.public(child)


def install(app,service):
    assembly=Assembly(service)
    @app.get('/api/workflow-designs/{key}/binding-preview')
    def preview(key:str):return assembly.preview(key)
    @app.post('/api/workflow-designs/{key}/assemble')
    def confirm(key:str,req:BindingAssemblyConfirm):return assembly.confirm(key,req)
    return assembly
