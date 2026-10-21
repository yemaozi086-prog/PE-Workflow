"""Compile one-level, explicitly scoped finite subgraphs; no inferred bindings."""
import copy
from .models import Workflow, Node, Binding, Diagnostic
from .engine import dictionary_plan, interface, WorkflowError
from .finite_control import row_port


class ControlDeclarationError(ValueError):
    def __init__(self,path,message):
        self.diagnostic=Diagnostic(code='PLAN_DEPENDENCY_MISMATCH',path=path,message=message)
        super().__init__(message)


def compile_plan(plan, workflow_id, project_id='local', registry=None):
    from .plan_audit import validate_checks
    registry=registry or {}
    steps={s.id:s for s in plan.steps};grids={g.id:g for g in plan.grids};loops={l.id:l for l in plan.loops}
    ids=[s.id for s in plan.steps]+[g.id for g in plan.grids]+[l.id for l in plan.loops]
    if len(set(ids))!=len(ids):raise ValueError('步骤及控制节点 ID 必须全局唯一')
    owners={}
    for loop in plan.loops:
        if len(set(loop.step_ids))!=len(loop.step_ids):raise ValueError('循环成员不能重复')
        if 'item' in loop.captures or 'rows' in loop.captures:raise ValueError('循环共享输入不能使用保留名 item/rows')
        for sid in loop.step_ids:
            if sid not in steps or sid in owners:raise ValueError('循环成员必须为唯一的计算步骤；不支持嵌套循环')
            owners[sid]=loop.id
    def graph(step_ids, externals, controls, graph_id, name, selected_outputs=None):
        scope={sid:steps[sid] for sid in step_ids};ports={};nodes={};visiting=set()
        available=set(scope)|set(controls)
        def source(binding):
            if binding.source=='constant' or binding.field:raise ValueError('方案不得使用常量或隐式标量来源')
            if binding.source=='input':
                if binding.port not in externals:raise ValueError('来源不在当前子图作用域：'+str(binding.port))
                return externals[binding.port]
            if binding.node not in available:raise ValueError('跨作用域或未知节点：'+str(binding.node))
            out=build(binding.node)
            if binding.port not in out:raise ValueError('节点输出不存在：'+str(binding.node)+'/'+str(binding.port))
            return out[binding.port]
        def control_source(binding):
            if binding.field_map:raise ValueError('控制节点共享输入须完整直通；字段映射放在计算节点')
            return copy.deepcopy(source(binding))
        def build(key):
            if key in ports:return ports[key]
            if key in visiting:raise ValueError('存在未声明的循环依赖：'+key)
            visiting.add(key)
            if key in scope:
                s=scope[key]
                if set(s.input_sources)!=set(s.inputs):raise ValueError('请逐输入声明明确来源：'+key)
                deps={b.node for b in s.input_sources.values() if b.source=='node'}
                if deps!=set(s.dependencies):raise ControlDeclarationError('/steps/'+str(next(i for i,v in enumerate(plan.steps) if v.id==key))+'/dependencies','步骤 '+key+' 的依赖声明与显式来源不一致；声明：'+str(sorted(s.dependencies))+'；实际来源节点：'+str(sorted(deps))+'。仅核对依赖列表，不修改来源或公式。')
                validate_checks(s.input_checks,s.inputs)
                for port,b in s.input_sources.items():dictionary_plan(source(b),s.inputs[port],b.field_map,'/steps/'+key+'/input_sources/'+port)
                if any(p.default is not None for p in [*s.inputs.values(),*s.outputs.values()]):raise ValueError('方案不能包含运行默认值')
                if s.module_ref:
                    mid,ver=s.module_ref.rsplit('@',1)
                    if registry:
                        m=registry.get(s.module_ref)
                        if m is None or m.inputs!=s.inputs or m.outputs!=s.outputs:raise ValueError('冻结模块接口发生变化：'+key)
                else:mid,ver='pending_'+key,1
                node=Node(id=key,label=s.title,module_id=mid,module_version=int(ver),inputs=s.input_sources,input_checks=s.input_checks)
                outs=copy.deepcopy(s.outputs)
            elif key in grids:
                g=grids[key]
                node=Node(id=key,label=g.title,kind='grid',inputs=g.input_sources,
                          grid_ports={k:control_source(b) for k,b in g.input_sources.items()},grid_axes=g.axes,grid_order=g.axis_order)
                _,outs=interface(node,{})
            else:
                loop=loops[key]
                columns=control_source(loop.rows)
                env={'item':row_port(columns),**{k:control_source(b) for k,b in loop.captures.items()}}
                body=graph(loop.step_ids,env,{},graph_id+'_'+key,loop.title,loop.outputs)
                node=Node(id=key,label=loop.title,kind='batch',body=body,inputs={'rows':loop.rows,**loop.captures})
                _,outs=interface(node,{})
            nodes[key]=node;ports[key]=outs;visiting.remove(key);return outs
        for key in sorted(available):build(key)
        if selected_outputs is None:
            bindings={key+'__'+port:Binding(source='node',node=key,port=port) for key in sorted(ports) for port in ports[key]}
        else:bindings=selected_outputs
        declared={name:control_source(b) for name,b in bindings.items()}
        if any(b.source!='node' for b in bindings.values()):raise ValueError('子图结果必须明确来自本子图计算节点')
        return Workflow(schema_version='3.0',id=graph_id,name=name,inputs=copy.deepcopy(externals),nodes=list(nodes.values()),outputs=copy.deepcopy(bindings),output_ports=declared)
    wf=graph([sid for sid in steps if sid not in owners],plan.external_inputs,{**grids,**loops},workflow_id,plan.title)
    wf.description=plan.summary;wf.project_id=project_id
    return wf


def preflight_controls(plan):
    try:
        from .control_contracts import reference_issues,batch_output_issues,connection_issues,scope_issues
        scope=scope_issues(plan)
        if scope:return {'valid':False,'issues':scope,'repairs':[],'scope':'finite_subgraphs','notice':'先修正全部作用域来源，再检查字段与依赖；已有公式和保护条件保留，来源变更须确认。'}
        issues=reference_issues(plan)+batch_output_issues(plan)+connection_issues(plan)
        if issues:return {'valid':False,'issues':issues,'repairs':[],'scope':'finite_subgraphs','notice':'按正式控制契约定位无效端口；仅预览，不自动修改。'}
        compile_plan(plan,'control_preview')
        return {'valid':True,'issues':[],'repairs':[],'scope':'finite_subgraphs','notice':'已检查有限子图作用域和显式接线；未验证物理模型。'}
    except (ValueError,WorkflowError) as error:
        d=getattr(error,'diagnostic',None)
        return {'valid':False,'issues':[{'code':getattr(d,'code','CONTROL_PLAN_INVALID'),'message':str(error),'path':getattr(d,'path','/plan/controls')}],
                'repairs':[],'scope':'finite_subgraphs','notice':'循环控制结构尚未通过检查，不进行隐式修补。'}
