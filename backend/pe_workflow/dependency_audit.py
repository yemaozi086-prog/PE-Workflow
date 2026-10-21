"""Conservative field-read audit, not a proof of physical model correctness.

Only literal dictionary paths, simple aliases and explicit membership guards are
resolved. Ambiguous access blocks automatic registration instead of guessing.
"""
import ast
from .models import DependencyAudit
from .engine import fail


def audit_dependencies(module):
    # Determine the calling convention before interpreting parameter names as paths.
    if module.implementation=='python_subset':
        try:
            functions=[n for n in ast.parse(module.source).body if isinstance(n,ast.FunctionDef) and n.name=='compute']
        except SyntaxError:
            functions=[]
        if len(functions)==1:
            args=functions[0].args
            names=[a.arg for a in args.posonlyargs+args.args]
            if names!=['inputs'] or args.vararg or args.kwarg or args.kwonlyargs or args.defaults:
                fail('MODULE_ENTRYPOINT_MISMATCH',
                     '实现类型与函数入口不一致：python_subset 必须使用 compute(inputs)，并通过 inputs["字典名"]["字段名"] 读取。当前入口为 '+ast.unparse(functions[0]).split('\n')[0]+'。若保留多个函数参数，应由智能体改为 python_function 并提供显式适配器。不能据此判定字段未读取；无需修改用户声明和样例。',
                     '/module/source')
    declared={f'{bag}.{key}':field for bag,p in module.inputs.items() for key,field in p.fields.items()}
    required={key for key,p in declared.items() if p.required and module.inputs[key.split('.')[0]].required}
    reads=set();guarded=set();unsafe=set();unknown=[]
    aliases={}
    eager=set()

    def mapped(mapping):
        if mapping.assemble is not None:return {key:mapped(value) for key,value in mapping.assemble.items()}
        path=mapping.from_path or []
        if not path or path[0]!='inputs' or any(not isinstance(x,str) for x in path):
            unknown.append('输入适配器路径无法静态解析');return None
        result=tuple(path[1:])
        if len(result)==2:eager.add('.'.join(result))
        return result

    if module.implementation=='python_function':
        if not module.adapter:unknown.append('缺少显式适配器')
        else:aliases={key:mapped(value) for key,value in module.adapter.arguments.items()}
    elif module.implementation=='python_subset':aliases={'inputs':()}
    elif module.implementation=='expressions':aliases={key:(key,) for key in module.inputs}
    else:unknown.append('此实现类型尚无静态依赖分析器')

    def path(node,env):
        if isinstance(node,ast.Name):return env.get(node.id)
        if isinstance(node,ast.Subscript):
            root=path(node.value,env)
            if isinstance(node.slice,ast.Constant) and isinstance(node.slice.value,str):
                if isinstance(root,dict):return root.get(node.slice.value)
                if isinstance(root,tuple):return (*root,node.slice.value)
        return None

    def has_explicit_default(key):
        return key in declared and declared[key].default is not None and module.inputs[key.split('.')[0]].required

    def mark(location,guards):
        if isinstance(location,tuple) and len(location)>=2:
            # Array indexing is not an additional dictionary field.
            key='.'.join(location[:2]);reads.add(key)
            if key in guards:guarded.add(key)
            elif key in declared and key not in required and not has_explicit_default(key):unsafe.add(key)
        else:unknown.append('整个字典被当作值使用，无法确定实际读取字段')

    def guards_for(node,env):
        if isinstance(node,ast.BoolOp) and isinstance(node.op,ast.And):
            return set().union(*(guards_for(v,env) for v in node.values))
        if isinstance(node,ast.Compare) and len(node.ops)==1 and isinstance(node.ops[0],ast.In) and isinstance(node.left,ast.Constant) and isinstance(node.left.value,str):
            base=path(node.comparators[0],env)
            if isinstance(base,tuple) and len(base)==1:return {'.'.join((*base,node.left.value))}
        return set()

    def expr(node,env,guards):
        if node is None:return
        location=path(node,env)
        if location is not None:
            mark(location,guards);return
        if isinstance(node,ast.Subscript):
            base=path(node.value,env)
            if isinstance(base,tuple) and len(base)>=2:
                mark(base,guards);expr(node.slice,env,guards);return
            if base is not None:unknown.append('动态字典字段名：'+ast.unparse(node));return
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='get':
            base=path(node.func.value,env)
            if base is not None:
                if not node.args or not isinstance(node.args[0],ast.Constant) or not isinstance(node.args[0].value,str):unknown.append('动态 get 字段名');return
                if len(node.args)<2:unknown.append('get 未提供明确缺省处理：'+ast.unparse(node));return
                key=node.args[0].value
                target=base.get(key) if isinstance(base,dict) else (*base,key)
                mark(target,guards|({'.'.join(target)} if isinstance(target,tuple) else set()))
                expr(node.args[1],env,guards);return
        if isinstance(node,ast.Compare) and guards_for(node,env):return
        if isinstance(node,ast.IfExp):
            expr(node.test,env,guards);expr(node.body,env,guards|guards_for(node.test,env));expr(node.orelse,env,guards);return
        if isinstance(node,ast.BoolOp):
            current=set(guards)
            for child in node.values:
                expr(child,env,current)
                if isinstance(node.op,ast.And):current|=guards_for(child,env)
            return
        for child in ast.iter_child_nodes(node):expr(child,env,guards)

    def statements(nodes,env,guards):
        for node in nodes:
            if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
                value=path(node.value,env);name=node.targets[0].id
                if isinstance(value,(tuple,dict)) and (isinstance(value,dict) or len(value)<2):env[name]=value
                else:
                    expr(node.value,env,guards);env.pop(name,None)
            elif isinstance(node,ast.If):
                expr(node.test,env,guards)
                left=dict(env);right=dict(env)
                statements(node.body,left,guards|guards_for(node.test,env));statements(node.orelse,right,guards)
                # Branch-dependent aliases are intentionally not guessed.
                for key in left.keys()|right.keys():
                    if left.get(key)!=right.get(key):
                        if key in env:env.pop(key,None)
                        unknown.append('条件分支改变了变量绑定：'+key)
                    elif key in left:env[key]=left[key]
            elif isinstance(node,(ast.Return,ast.Expr)):expr(node.value,env,guards)
            else:unknown.append('暂不支持的语句：'+type(node).__name__)

    try:
        if module.implementation=='expressions':
            for source in module.expressions.values():expr(ast.parse(source,mode='eval').body,aliases,set())
        elif module.implementation in ('python_subset','python_function'):
            tree=ast.parse(module.source);name=module.entrypoint if module.implementation=='python_function' else 'compute'
            functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name]
            if len(functions)!=1:unknown.append('无法唯一确定函数入口')
            else:statements(functions[0].body,dict(aliases),set())
    except (SyntaxError,ValueError,TypeError):unknown.append('源码无法静态解析')
    # Scalar adapter mappings are evaluated before the function, even if guarded inside it.
    unsafe|={key for key in eager if key in declared and key not in required and not has_explicit_default(key)}
    from .plan_audit import validate_checks
    check_fields=set()
    try:
        validate_checks(module.input_checks,module.inputs)
        for expression in module.input_checks:
            for n in ast.walk(ast.parse(expression,mode='eval')):
                if isinstance(n,ast.Subscript) and isinstance(n.value,ast.Name) and isinstance(n.slice,ast.Constant):
                    check_fields.add(n.value.id+'.'+str(n.slice.value))
    except (ValueError,SyntaxError) as e:unknown.append('保护条件无法解析：'+str(e))
    unsafe|={key for key in check_fields if key in declared and key not in required and not has_explicit_default(key)}
    for key in module.domains:
        if key in declared:check_fields.add(key)
    undeclared=(reads|check_fields)-declared.keys();unused=required-reads-check_fields
    optional=set(declared)-required
    # Required describes call-time presence, not compulsory use by the code.
    # Redundancy cannot establish missing physics or justify fake reads.
    failed=bool(undeclared or unsafe)
    warnings=[{'code':'UNUSED_REQUIRED_INPUT','severity':'warning','fields':sorted(unused),
               'message':'部分必填输入未被当前代码或保护条件使用；保留原契约继续检查，可另行评审简化。该提示不能证明公式遗漏，也不要求添加无效读取。'}] if unused else []
    return DependencyAudit(status='uncertain' if unknown else 'failed' if failed else 'passed',
        warnings=warnings,
        required_fields=sorted(required),optional_fields=sorted(optional),read_fields=sorted(reads),
        check_fields=sorted(check_fields),carried_fields=sorted(set(declared)-reads-check_fields),
        guarded_fields=sorted(guarded),unused_required_fields=sorted(unused),undeclared_fields=sorted(undeclared),
        unsafe_optional_fields=sorted(unsafe),uncertainties=sorted(set(unknown))).model_dump()
