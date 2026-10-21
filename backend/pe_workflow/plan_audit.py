"""Read-only planning checks, using the same connection rules as execution."""
import ast
from .engine import dictionary_plan, compatible, WorkflowError
from .expressions import parse, evaluate

def same_checks(left,right):
    """Ignore quote/whitespace spelling only; keep order, operands and thresholds."""
    try:return [ast.dump(parse(x)) for x in left]==[ast.dump(parse(x)) for x in right]
    except (ValueError,SyntaxError):return False


def validate_checks(checks, ports):
    for expression in checks:
        try:tree = parse(expression)
        except (ValueError,SyntaxError) as e:raise ValueError('输入保护语法无效：'+str(e)) from e
        if not isinstance(tree, (ast.Compare, ast.BoolOp)):
            raise ValueError('输入保护必须是比较或逻辑条件')
        lengths=set();length_functions=set()
        for call in (n for n in ast.walk(tree) if isinstance(n,ast.Call)):
            if not(isinstance(call.func,ast.Name) and call.func.id=='len' and len(call.args)==1 and not call.keywords and isinstance(call.args[0],ast.Subscript)):
                raise ValueError('输入保护仅允许 len(字典[数组字段]) 长度检查，不允许计算函数')
            lengths.add(id(call.args[0]))
            length_functions.add(id(call.func))
        if any(isinstance(n, (ast.BinOp, ast.Attribute, ast.IfExp)) for n in ast.walk(tree)):
            raise ValueError('输入保护只允许字段、常量、比较与逻辑运算；不能包含除法或函数调用')
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id not in ports and id(n) not in length_functions:
                raise ValueError('输入保护引用未声明字典：'+n.id)
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name):
                if n.value.id not in ports:raise ValueError('输入保护引用未声明字典：'+n.value.id)
                if not isinstance(n.slice, ast.Constant) or n.slice.value not in ports[n.value.id].fields:
                    raise ValueError('输入保护引用未声明字段')
                field=ports[n.value.id].fields[n.slice.value]
                if id(n) in lengths:
                    if field.type!='array':raise ValueError('长度保护只能读取数组字段')
                elif field.type not in ('number','integer','boolean','string'):
                    raise ValueError('首版输入保护只支持标量字段')


def run_checks(checks, values):
    for expression in checks:
        try:passed=evaluate(parse(expression), values) is True
        except (ValueError,TypeError,KeyError) as e:raise ValueError('无法检查计算前提：'+expression+'；'+str(e)) from e
        if not passed:
            raise ValueError('输入不满足计算前提：'+expression)


def connection_repair(plan, step, port_name, binding, source, seen):
    """Offer evidence and edit obligations; never choose a physical mapping."""
    target=step.inputs[port_name]
    pool=[({'source':'input','port':name},port) for name,port in plan.external_inputs.items()]
    pool.extend(({'source':'node','node':s.id,'port':name},port) for s in seen.values() for name,port in s.outputs.items())
    missing=[];available=[]
    for name,field in target.fields.items():
        origin=binding.field_map.get(name,name)
        if origin in source.fields:
            available.append({'target_field':name,'source_field':origin});continue
        if not field.required and name not in binding.field_map:continue
        candidates=[]
        for address,bag in pool:
            for key,candidate in bag.fields.items():
                if not(field.quantity or field.semantic_id):continue
                try:compatible(candidate,field,'/repair')
                except WorkflowError:continue
                candidates.append({'binding':address,'field':key,'declaration':candidate.model_dump()})
        missing.append({'target_field':name,'attempted_source_field':origin,'declaration':field.model_dump(),'candidates':candidates})
    return {'code':'PORT_SOURCE_INCOMPLETE','step_id':step.id,'port':port_name,'module_ref':step.module_ref,
            'selected_source':binding.model_dump(),'available_fields':list(source.fields),'resolved_fields':available,'missing_fields':missing,
            'strategy':'derive_independent_inputs' if step.module_ref and available and missing else 'review_source_or_interface',
            'required_changes':[
                '字段映射只能在所选来源字典内取值，不能从其他来源补字段；禁止填常量、复制虚假字段到上游或删除语义来绕过检查。',
                '先核对候选来源的物理角色；同量纲不代表同一器件。若不能确定，集中追问，不能猜测。',
                '若实际需要多个来源，module_ref 设为 null，derives_from 指向原版本，重新声明独立输入字典及各自 input_sources。保持物理公式和输出契约，更新 input_checks 的字段路径。',
                '同类实例应共享同一派生工具接口；逐实例通过 field_map 选择各自电流、电阻。用户确认后才生成、验证、注册新版本。'],
            'not_applied':True}


def check_repair(step):
    references=[]
    for expression in step.input_checks:
        try:tree=parse(expression)
        except (ValueError,SyntaxError):continue
        for node in ast.walk(tree):
            if not(isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name) and isinstance(node.slice,ast.Constant)):continue
            bag,key=node.value.id,node.slice.value
            if bag in step.inputs and key in step.inputs[bag].fields:continue
            local=[]
            for name,binding in step.input_sources.items():
                if binding.port!=bag or name not in step.inputs:continue
                for target in step.inputs[name].fields:
                    if binding.field_map.get(target,target)==key:
                        local.append({'port':name,'field':target,'evidence':'已声明 input_sources 与 field_map 的精确对应'})
            references.append({'expression':expression,'invalid_reference':{'port':bag,'field':key},'candidate_local_paths':local})
    return {'code':'INPUT_CHECK_SCOPE','step_id':step.id,'strategy':'repair_input_checks','references':references,
            'declared_inputs':{name:list(p.fields) for name,p in step.inputs.items()},
            'required_changes':['输入保护在节点局部输入上执行，不能直接引用外部字典名称。按已确认 input_sources/field_map 改用局部字典和字段名，保持比较条件和阈值不变。不能删除保护来通过检查；无明确对应则追问。'],
            'not_applied':True}


def guard_signature(expression,step,plan):
    class Sources(ast.NodeTransformer):
        def visit_Subscript(self,node):
            if isinstance(node.value,ast.Name) and isinstance(node.slice,ast.Constant):
                name,field=node.value.id,node.slice.value
                if name in step.input_sources:
                    b=step.input_sources[name];field=b.field_map.get(field,field)
                    origin=(b.source,b.node,b.port,field)
                elif name in plan.external_inputs:origin=('input',None,name,field)
                else:origin=('unresolved',step.id,name,field)
                return ast.Constant(value=repr(origin))
            return self.generic_visit(node)
    try:return ast.dump(Sources().visit(parse(expression)),include_attributes=False)
    except (ValueError,SyntaxError):return 'invalid:'+expression


def declaration_issues(plan):
    """Check executable contracts before asking for code, without inferring semantics."""
    from .engine import check_port
    issues=[]
    def visit(port,path,needs_semantics):
        if port.type=='flat_dict':
            for name,field in port.fields.items():visit(field,path+'/fields/'+name,needs_semantics)
            return
        if needs_semantics and not (port.quantity or port.semantic_id):
            issues.append({'code':'FIELD_SEMANTIC_REQUIRED','path':path,'message':'字段缺少含义声明；请核对已有说明并确认语义，不能自动猜测。'})
        try:check_port(port,path)
        except WorkflowError as error:
            item=error.diagnostic.model_dump()
            if item['code']=='UNKNOWN_UNIT':item.update(severity='warning',next_action='unit_extension',message=item['message']+'；生成工具前进入现有单位扩展确认流程，不猜测定义。')
            issues.append(item)
    for i,step in enumerate(plan.steps):
        for side in ('inputs','outputs'):
            for name,port in getattr(step,side).items():visit(port,f'/steps/{i}/{side}/{name}',not step.module_ref)
    return issues


def preflight(plan, previous=None):
    if plan.grids or plan.loops:
        from .plan_controls import preflight_controls
        result=preflight_controls(plan)
        def defaults(p):return p.default is not None or any(defaults(f) for f in p.fields.values()) or bool(p.item and defaults(p.item))
        for name,p in plan.external_inputs.items():
            if p.type!='flat_dict' or defaults(p):result['issues'].append({'code':'PLAN_INPUT_INVALID','message':'外部接口必须为无默认值的命名字典','path':'/external_inputs/'+name})
        if previous:
            current={s.id:s for s in plan.steps}
            for old in previous.steps:
                step=current.get(old.id)
                if old.input_checks and (step is None or {guard_signature(c,old,previous) for c in old.input_checks}-{guard_signature(c,step,plan) for c in step.input_checks}):
                    result['issues'].append({'code':'PLAN_GUARD_REGRESSION','message':'不能删除或修改冻结保护条件：'+old.id,'path':'/steps/'+old.id+'/input_checks'})
        result['valid']=not result['issues']
        if not any(i['code']=='CONTROL_SCOPE_REFERENCE' for i in result['issues']):
            lint=declaration_issues(plan)
            result['issues'].extend(i for i in lint if i.get('severity')!='warning')
            result['warnings']=[i for i in lint if i.get('severity')=='warning']
            result['valid']=not result['issues']
        return result
    issues=[];repairs=[]
    def issue(code,message,path):
        issues.append(dict(code=code,message=message,path=path))
    explicit=bool(plan.external_inputs) or any(s.input_sources for s in plan.steps)
    def has_default(port):return port.default is not None or any(has_default(p) for p in port.fields.values())
    for name,port in plan.external_inputs.items():
        if port.type!='flat_dict':issue('DICTIONARY_PORT_REQUIRED','外部接口必须为命名字典','/external_inputs/'+name)
        if has_default(port):issue('PLAN_RUNTIME_DEFAULT','规划阶段不允许植入运行默认值','/external_inputs/'+name)
    seen={}
    for index,step in enumerate(plan.steps):
        base=f'/steps/{index}'
        try:validate_checks(step.input_checks,step.inputs)
        except ValueError as e:
            issue('INPUT_CHECK_INVALID',str(e),base+'/input_checks')
            repairs.append(check_repair(step))
        # Legacy independent plans have no edges to audit. Dependent plans must
        # explicitly describe sources, never infer them from similar names.
        if explicit or step.dependencies:
            if set(step.input_sources)!=set(step.inputs):
                issue('PLAN_SOURCES_REQUIRED','请逐字典声明唯一来源；需要多个来源时拆成独立输入字典或提供显式变换工具',base+'/input_sources')
            dependencies=set()
            for name,binding in step.input_sources.items():
                path=base+'/input_sources/'+name
                if name not in step.inputs:
                    issue('UNKNOWN_INPUT','来源引用了未声明的输入字典',path);continue
                if binding.source=='constant' or binding.field:
                    issue('PLAN_SOURCE_INVALID','方案只能引用外部字典或上游完整字典，并使用显式字段映射；数值在运行时填写',path);continue
                if binding.source=='input':source=plan.external_inputs.get(binding.port)
                else:
                    dependencies.add(binding.node)
                    if binding.node in seen and binding.port not in seen[binding.node].outputs:
                        issue('PLAN_SOURCE_PORT_NOT_FOUND','节点 '+binding.node+' 的输出字典不存在：'+str(binding.port)+'；可用输出：'+', '.join(seen[binding.node].outputs),path)
                        repairs.append({'code':'PLAN_SOURCE_PORT_NOT_FOUND','step_id':step.id,'port':name,'strategy':'select_declared_output',
                            'node':binding.node,'invalid_output':binding.port,'allowed_outputs':list(seen[binding.node].outputs),
                            'required_changes':['Binding.node 为节点 ID，Binding.port 为该节点声明的输出字典名，两者不是同一概念。请从 allowed_outputs 中选取有证据的字典，不改冻结步骤或输出。'],'not_applied':True})
                        continue
                    source=seen[binding.node].outputs.get(binding.port) if binding.node in seen else None
                if source is None:
                    issue('PLAN_SOURCE_NOT_FOUND','来源不存在或不是前序步骤：'+str(binding.node or binding.port),path);continue
                try:dictionary_plan(source,step.inputs[name],binding.field_map,path)
                except WorkflowError as e:
                    issue(e.diagnostic.code,e.diagnostic.message,e.diagnostic.path)
                    if e.diagnostic.code=='MISSING_DICT_FIELD':repairs.append(connection_repair(plan,step,name,binding,source,seen))
            if set(step.input_sources)==set(step.inputs) and dependencies!=set(step.dependencies):issue('PLAN_DEPENDENCIES','显式来源与步骤依赖不一致',base+'/dependencies')
        seen[step.id]=step
    if previous:
        steps={s.id:s for s in plan.steps}
        for old in previous.steps:
            if not old.input_checks:continue
            step=steps.get(old.id)
            signatures={guard_signature(c,step,plan) for c in step.input_checks} if step else set()
            lost=[c for c in old.input_checks if guard_signature(c,old,previous) not in signatures]
            if lost:
                issue('PLAN_GUARD_REGRESSION','修正时不能删除原有保护条件或改名受保护步骤：'+old.id,'/steps/'+old.id+'/input_checks')
                repairs.append({'code':'PLAN_GUARD_REGRESSION','step_id':old.id,'strategy':'preserve_input_checks',
                    'required_conditions':lost,'previous_inputs':{n:list(p.fields) for n,p in old.inputs.items()},
                    'previous_sources':{n:b.model_dump() for n,b in old.input_sources.items()},
                    'required_changes':['保留原步骤 ID；按新端口及其显式 field_map 翻译保护条件，保持比较符和阈值。禁止删除保护条件来通过检查。'], 'not_applied':True})
    lint=declaration_issues(plan);issues.extend(i for i in lint if i.get('severity')!='warning')
    return {'valid':not issues,'scope':'explicit_connections' if explicit else 'legacy_independent_inputs','issues':issues,'repairs':repairs,'warnings':[i for i in lint if i.get('severity')=='warning'],
            'notice':'只检查声明接线和已声明的输入保护；不证明公式、物理适用性或全部数学定义域正确。'}
