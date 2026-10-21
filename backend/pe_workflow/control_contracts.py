"""Expose runtime-derived control contracts and review-only grid port corrections."""
import copy
from .models import DesignPlan,Node,Workflow
from .finite_control import grid_interface,row_port,GRID_OUTPUT,control_spec
from .engine import interface,WorkflowError
from .planning_protocol import binding_locations


def describe(plan):
    plan=DesignPlan.model_validate(plan) if isinstance(plan,dict) else plan
    steps={s.id:s for s in plan.steps};grids={g.id:g for g in plan.grids};loops={l.id:l for l in plan.loops}
    ids=[s.id for s in plan.steps]+[g.id for g in plan.grids]+[l.id for l in plan.loops]
    if len(ids)!=len(set(ids)):raise ValueError('控制契约需要全局唯一的节点 ID')
    owners={sid:l.id for l in plan.loops for sid in l.step_ids};cache={};inputs_cache={};visiting=set()
    def source(b):
        if b.field or b.field_map or b.source=='constant':raise ValueError('控制输入必须是显式完整字典，不猜字段映射或常量')
        if b.source=='input':return plan.external_inputs[b.port]
        if b.node in owners:raise ValueError('控制输入不能跨子图读取成员节点')
        return outputs(b.node)[b.port]
    def outputs(key):
        if key in cache:return cache[key]
        if key in visiting:raise ValueError('控制接口存在循环依赖')
        visiting.add(key)
        try:
            if key in steps:result=steps[key].outputs
            elif key in grids:
                g=grids[key];inputs={name:source(b) for name,b in g.input_sources.items()}
                _,result=grid_interface(inputs,{name:g.axes[name] for name in g.axis_order});inputs_cache[key]=inputs
            elif key in loops:
                l=loops[key];env={'item':row_port(source(l.rows)),**{n:source(b) for n,b in l.captures.items()}}
                out={}
                for alias,b in l.outputs.items():
                    if b.source!='node' or b.node not in l.step_ids or b.field or b.field_map:raise ValueError('循环结果必须来自显式成员的完整输出')
                    out[alias]=steps[b.node].outputs[b.port]
                body=Workflow(id='contract_'+key,name=l.title,inputs=env,nodes=[],outputs={},output_ports=out)
                inputs_cache[key],result=interface(Node(id=key,kind='batch',body=body),{})
            else:raise ValueError('未知控制来源：'+str(key))
            cache[key]=result;return result
        finally:visiting.discard(key)
    result=[]
    for kind,rows in (('grid',plan.grids),('batch',plan.loops)):
        for node in rows:
            item={'id':node.id,'kind':kind}
            if kind=='grid':item.update(axis_order=list(node.axis_order),axes=copy.deepcopy(node.axes))
            else:item.update(rows_binding=node.rows.model_dump(),captures={k:v.model_dump() for k,v in node.captures.items()},members=list(node.step_ids))
            try:
                out=outputs(node.id);item.update(status='resolved',outputs={k:v.model_dump() for k,v in out.items()},inputs={k:v.model_dump() for k,v in inputs_cache.get(node.id,{}).items()})
            except (ValueError,KeyError,TypeError,WorkflowError) as error:item.update(status='unresolved',message=str(error),outputs={})
            result.append(item)
    return {'specification':control_spec(),'nodes':result,'notice':'运行接口生成的正式声明；不代表物理模型已验证。'}


def port_corrections(plan):
    raw=plan.model_dump() if isinstance(plan,DesignPlan) else copy.deepcopy(plan)
    contracts=describe(raw);known={n['id']:n for n in contracts['nodes'] if n['kind']=='grid' and n['status']=='resolved'}
    result=copy.deepcopy(raw);edits=[]
    for path,b in binding_locations(result):
        if not isinstance(b,dict) or b.get('source')!='node' or b.get('node') not in known or b.get('port')==GRID_OUTPUT:continue
        if b.get('field') or b.get('value') is not None:continue
        declaration=known[b['node']]['outputs']
        if list(declaration)!=[GRID_OUTPUT]:continue
        before=copy.deepcopy(b);b['port']=GRID_OUTPUT
        edits.append({'path':path+'/port','before':before.get('port'),'after':GRID_OUTPUT,'node':b['node'],
                      'basis':'selected_grid_runtime_single_output','contract':declaration[GRID_OUTPUT]})
    return result,edits,contracts


def reference_issues(plan):
    raw=plan.model_dump() if isinstance(plan,DesignPlan) else plan
    contracts=describe(raw);known={n['id']:n for n in contracts['nodes'] if n['status']=='resolved'}
    issues=[]
    for path,b in binding_locations(raw):
        if not isinstance(b,dict) or b.get('source')!='node' or b.get('node') not in known:continue
        available=list(known[b['node']]['outputs'])
        if b.get('port') not in available:
            issues.append({'code':'CONTROL_OUTPUT_REFERENCE','path':path+'/port','message':'节点 '+b['node']+' 的输出端口 '+str(b.get('port'))+' 不存在；正式输出：'+', '.join(available),
                           'node':b['node'],'available_ports':available,'outputs':known[b['node']]['outputs']})
    return issues


def batch_output_issues(plan):
    from .finite_control import BATCH_FIELD_TYPES
    plan=DesignPlan.model_validate(plan) if isinstance(plan,dict) else plan
    steps={s.id:(i,s) for i,s in enumerate(plan.steps)};issues=[]
    for loop in plan.loops:
        for alias,binding in loop.outputs.items():
            if binding.source!='node' or binding.node not in loop.step_ids or binding.node not in steps:continue
            i,step=steps[binding.node];port=step.outputs.get(binding.port)
            if port is None:continue
            for name,field in port.fields.items():
                if field.type not in BATCH_FIELD_TYPES:
                    issues.append({'code':'BATCH_FIELDS','path':f'/steps/{i}/outputs/{binding.port}/fields/{name}',
                      'message':'循环 '+loop.id+' 的结果 '+alias+'.'+name+' 声明为 '+field.type+'；当前批量接口只支持 '+', '.join(BATCH_FIELD_TYPES)+'。保留原字段，需单独确认接口变更或扩展支持，不能删除或伪造为数值。',
                      'loop':loop.id,'member':step.id,'allowed_field_types':list(BATCH_FIELD_TYPES)})
    return issues


def scope_issues(plan):
    """Report all illegal boundaries before dependency or field checks."""
    plan=DesignPlan.model_validate(plan) if isinstance(plan,dict) else plan
    members={sid:l for l in plan.loops for sid in l.step_ids}
    top={s.id for s in plan.steps if s.id not in members}|{g.id for g in plan.grids}|{l.id for l in plan.loops}
    issues=[]
    def check(b,path,nodes,inputs,owner):
        if b.source=='node' and b.node not in nodes or b.source=='input' and b.port not in inputs:
            issues.append({'code':'CONTROL_SCOPE_REFERENCE','path':path,'message':'来源不在当前作用域；循环成员只能读取当前行 item、已声明共享输入及同一子图成员输出。',
                           'owner':owner,'binding':b.model_dump(),'allowed_nodes':sorted(nodes),'allowed_inputs':sorted(inputs)})
    for i,s in enumerate(plan.steps):
        loop=members.get(s.id)
        nodes=set(loop.step_ids) if loop else top
        inputs={'item',*loop.captures} if loop else set(plan.external_inputs)
        for name,b in s.input_sources.items():check(b,f'/steps/{i}/input_sources/{name}',nodes,inputs,loop.id if loop else 'top')
    for i,g in enumerate(plan.grids):
        for name,b in g.input_sources.items():check(b,f'/grids/{i}/input_sources/{name}',top,set(plan.external_inputs),'top')
    for i,l in enumerate(plan.loops):
        check(l.rows,f'/loops/{i}/rows',top,set(plan.external_inputs),'top')
        for name,b in l.captures.items():check(b,f'/loops/{i}/captures/{name}',top,set(plan.external_inputs),'top')
        for name,b in l.outputs.items():
            if b.source!='node':
                issues.append({'code':'CONTROL_SCOPE_REFERENCE','path':f'/loops/{i}/outputs/{name}','message':'子图出口必须来自本子图成员的输出'})
            else:check(b,f'/loops/{i}/outputs/{name}',set(l.step_ids),set(),l.id)
    return issues


def boundary_corrections(plan):
    """Review-only proposal: exact loop rows -> explicitly named item input.

    No field/name/unit inference. Non-item aliases, different producers and
    incompatible row contracts remain unresolved for explicit clarification.
    """
    from .engine import dictionary_plan
    raw=plan.model_dump() if isinstance(plan,DesignPlan) else copy.deepcopy(plan)
    p=DesignPlan.model_validate(raw);formal={n['id']:n for n in describe(p)['nodes'] if n['status']=='resolved'}
    from .models import Binding,Port
    edits=[]
    for loop in p.loops:
        declaration=formal.get(loop.id)
        if not declaration:continue
        row=row_port(Port.model_validate(declaration['inputs']['rows']))
        for i,step in enumerate(p.steps):
            if step.id not in loop.step_ids:continue
            b=step.input_sources.get('item')
            if not b or b.source!='node' or b.node in loop.step_ids or b.node!=loop.rows.node or b.port!=loop.rows.port or b.field or b.value is not None:continue
            if 'item' not in step.inputs:continue
            try:dictionary_plan(row,step.inputs['item'],b.field_map,'/proposal')
            except WorkflowError:continue
            target=raw['steps'][i];before=copy.deepcopy(target['input_sources']['item'])
            after=Binding(source='input',port='item',field_map=b.field_map).model_dump()
            target['input_sources']['item']=after
            edits.append({'kind':'scope','path':f'/steps/{i}/input_sources/item','step':step.id,'title':step.title,'node':loop.id,
                          'before':before,'after':after,'basis':'explicit_item_alias_exact_loop_rows_compatible_contract'})
            # Remove only the obsolete outer dependency; preserve other declarations.
            if b.node in target['dependencies'] and not any(x.get('source')=='node' and x.get('node')==b.node for x in target['input_sources'].values()):
                before_deps=copy.deepcopy(target['dependencies']);target['dependencies']=[d for d in before_deps if d!=b.node]
                edits.append({'kind':'dependency','path':f'/steps/{i}/dependencies','step':step.id,'node':loop.id,'before':before_deps,'after':copy.deepcopy(target['dependencies']),'basis':'remove_only_obsolete_outer_rows_dependency'})
    return raw,edits


def reviewed_corrections(plan):
    raw,ports,contracts=port_corrections(plan)
    raw,boundaries=boundary_corrections(raw)
    return raw,ports+boundaries,contracts


def connection_issues(plan):
    """Collect independent connection failures without repairing declarations.

    Formal batch outputs, not member scalar outputs, cross the loop boundary.
    Unresolved producers are reported once rather than guessed downstream.
    """
    from .engine import dictionary_plan, compatible
    plan=DesignPlan.model_validate(plan) if isinstance(plan,dict) else plan
    steps={s.id:s for s in plan.steps}
    owners={sid:loop for loop in plan.loops for sid in loop.step_ids}
    formal={n['id']:n for n in describe(plan)['nodes'] if n['status']=='resolved'}
    from .models import Port
    issues=[]
    for step in plan.steps:
        loop=owners.get(step.id)
        env=plan.external_inputs
        if loop:
            contract=formal.get(loop.id)
            if not contract:continue
            env={k:Port.model_validate(v) for k,v in contract['inputs'].items() if k!='rows'}
            env['item']=row_port(Port.model_validate(contract['inputs']['rows']))
        for name,b in step.input_sources.items():
            path='/steps/'+step.id+'/input_sources/'+name
            if name not in step.inputs:continue  # compiler diagnoses invalid structure
            source=None
            if b.source=='input':source=env.get(b.port)
            elif b.source=='node':
                if loop and b.node in loop.step_ids:source=steps[b.node].outputs.get(b.port)
                elif not loop and b.node in steps and b.node not in owners:source=steps[b.node].outputs.get(b.port)
                elif not loop and b.node in formal:
                    raw=formal[b.node]['outputs'].get(b.port)
                    if raw:source=Port.model_validate(raw)
            if source is None:continue  # scope/reference diagnostics belong to compiler
            target=step.inputs[name]
            before=len(issues)
            if source.type=='flat_dict' and target.type=='flat_dict':
                for field,wanted in target.fields.items():
                    origin=b.field_map.get(field,field)
                    try:
                        if origin not in source.fields:
                            if not wanted.required and field not in b.field_map:continue
                            issues.append({'code':'MISSING_DICT_FIELD','path':path+'/fields/'+field,'message':'缺少所需字段：'+field+'（来源 '+origin+'）'})
                            continue
                        compatible(source.fields[origin],wanted,path+'/fields/'+field)
                    except WorkflowError as error:
                        issues.append(error.diagnostic.model_dump())
            if len(issues)==before:
                try:dictionary_plan(source,target,b.field_map,path)
                except WorkflowError as error:issues.append(error.diagnostic.model_dump())
    return issues
