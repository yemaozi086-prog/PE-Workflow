"""Interpret a single pure function; never eval/exec/import uploaded code."""
import ast
import copy
import math
import operator
from typing import Any
import numpy as np
from . import numeric

FUNCTIONS={n:getattr(math,n) for n in ("sqrt","sin","cos","tan","exp","log","log10","fabs","floor","ceil")}
FUNCTIONS.update(abs=abs,min=min,max=max)
CONSTANTS={"pi":math.pi,"e":math.e}
BINARY={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Mod:operator.mod,ast.Pow:operator.pow,ast.MatMult:operator.matmul}
COMPARE={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge}
ALLOWED=(ast.Expression,ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,ast.IfExp,ast.Call,ast.Name,ast.Load,ast.Constant,ast.Dict,ast.List,ast.Tuple,ast.Subscript,ast.Slice,ast.Attribute,ast.keyword,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Mod,ast.Pow,ast.MatMult,ast.UAdd,ast.USub,ast.Not,ast.And,ast.Or,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE)
DEFAULT_ALIASES={"math":"math","np":"numpy","numpy":"numpy","scipy":"scipy"}
MAX_SOURCE_CHARS=16000
MAX_EXPRESSION_CHARS=4000
MAX_AST_NODES=500
MAX_CALL_ARGS=20
MAX_CONSTANT_CHARS=1000
MAX_TEXT_CHARS=16000
PERMITTED_IMPORTS={"math","numpy","scipy","scipy.integrate","scipy.linalg"}
STATEMENT_NODES=(ast.Assign,ast.AnnAssign,ast.Return,ast.If,ast.Expr)


def syntax_manifest():
    """Generated from the same registries and budgets used by this interpreter."""
    return {'version':'controlled_python_v1','expression_nodes':sorted(n.__name__ for n in ALLOWED),
            'binary_operators':sorted(n.__name__ for n in BINARY),
            'comparison_operators':sorted(n.__name__ for n in COMPARE),
            'statement_nodes':sorted(n.__name__ for n in STATEMENT_NODES),
            'imports':sorted(PERMITTED_IMPORTS),'aliases':DEFAULT_ALIASES.copy(),
            'budgets':{'source_chars':MAX_SOURCE_CHARS,'expression_chars':MAX_EXPRESSION_CHARS,'expression_ast_nodes':MAX_AST_NODES,'call_positional_arguments':MAX_CALL_ARGS,'constant_chars':MAX_CONSTANT_CHARS},
            'function_rules':['exactly one function; no decorators, *args or **kwargs','assign only one local variable; no field assignment or unpacking','Expr statements only allow string documentation','python_subset signature: def compute(inputs); return complete named output dictionaries','Python if/and/or/not require scalar truth values; use the supplied array mask identities for arrays','No loops, comprehensions, helper definitions, bitwise operators or dynamic function calls'],
            'text_rules':{'addition':'str + str concatenates unchanged text, including empty strings; no implicit number conversion',
                          'arrays':'Text arrays are collected by batch; arithmetic and implicit concatenation are not supported',
                          'maximum_result_chars':MAX_TEXT_CHARS,'unsupported':'string repetition, formatting, dynamic methods and mixed str/number addition'},
            'numeric_catalog_ref':'controlled_syntax.numeric_catalog'}



class ExpressionError(ValueError): pass


def qualified(node,aliases):
    if isinstance(node,ast.Name): return aliases.get(node.id,node.id)
    if isinstance(node,ast.Attribute): return qualified(node.value,aliases)+"."+node.attr
    raise ExpressionError("不支持动态函数调用")


def check_tree(tree,aliases=None):
    aliases=aliases or DEFAULT_ALIASES
    nodes=list(ast.walk(tree))
    if len(nodes)>MAX_AST_NODES: raise ExpressionError("表达式太复杂（最多 500 个语法节点）")
    for node in nodes:
        if not isinstance(node,ALLOWED): raise ExpressionError(f"不支持的语法：{type(node).__name__}")
        if isinstance(node,ast.Name) and node.id.startswith("_"): raise ExpressionError("不能访问私有名称")
        if isinstance(node,ast.Attribute):
            path=qualified(node,aliases)
            if path not in numeric.FUNCTIONS and path not in numeric.CONSTANTS and not any(k.startswith(path+".") for k in numeric.FUNCTIONS): raise ExpressionError(f"未登记的库成员：{path}")
        if isinstance(node,ast.Call):
            name=qualified(node.func,aliases)
            canonical="math."+name if name in FUNCTIONS and name not in numeric.FUNCTIONS else name
            if canonical not in numeric.FUNCTIONS or len(node.args)>MAX_CALL_ARGS or any(k.arg is None for k in node.keywords): raise ExpressionError(f"未登记的函数：{name}")
            if {k.arg for k in node.keywords}-numeric.FUNCTIONS[canonical][1]: raise ExpressionError(f"{name} 使用未登记关键字")
        if isinstance(node,ast.Constant) and (not isinstance(node.value,(int,float,bool,str,type(None))) or len(str(node.value))>MAX_CONSTANT_CHARS): raise ExpressionError("不支持的常量")
        if isinstance(node,ast.Dict) and any(k is None for k in node.keys): raise ExpressionError("不支持字典展开")


def parse(expression):
    if len(expression)>MAX_EXPRESSION_CHARS: raise ExpressionError("表达式长度超过 4000")
    tree=ast.parse(expression,mode="eval");check_tree(tree)
    return tree.body


def evaluate(node,values:dict[str,Any],aliases=None):
    aliases=aliases or DEFAULT_ALIASES
    ev=lambda n:evaluate(n,values,aliases)
    if isinstance(node,ast.Constant): return node.value
    if isinstance(node,ast.Name):
        if node.id in values:return values[node.id]
        if node.id in CONSTANTS:return CONSTANTS[node.id]
        path=qualified(node,aliases)
        if path in numeric.CONSTANTS:return numeric.CONSTANTS[path]
        raise ExpressionError(f"未知变量：{node.id}")
    if isinstance(node,ast.Attribute):
        name=qualified(node,aliases)
        if name not in numeric.CONSTANTS:raise ExpressionError("函数或模块不能作为数据值")
        return numeric.CONSTANTS[name]
    if isinstance(node,ast.List):return [ev(v) for v in node.elts]
    if isinstance(node,ast.Tuple):return tuple(ev(v) for v in node.elts)
    if isinstance(node,ast.Dict):return {ev(k):ev(v) for k,v in zip(node.keys,node.values)}
    if isinstance(node,ast.Slice):return slice(ev(node.lower) if node.lower else None,ev(node.upper) if node.upper else None,ev(node.step) if node.step else None)
    if isinstance(node,ast.Subscript):
        result=ev(node.value)[ev(node.slice)]
        return result.item() if isinstance(result,np.generic) else result
    if isinstance(node,ast.BinOp):
        a,b=ev(node.left),ev(node.right)
        if isinstance(a,str) or isinstance(b,str):
            if isinstance(node.op,ast.Add) and type(a) is str and type(b) is str:
                if len(a)+len(b)>MAX_TEXT_CHARS:raise ExpressionError(f"第 {node.lineno} 行文本拼接结果超过 {MAX_TEXT_CHARS} 字符预算")
                return a+b
            raise ExpressionError(f"第 {node.lineno} 行 {type(node.op).__name__} 类型不匹配：左侧 {type(a).__name__}，右侧 {type(b).__name__}；文本仅支持字符串＋字符串，不进行隐式数值转换")
        if any(isinstance(v,np.ndarray) and v.dtype.kind in ('U','S','O') for v in (a,b)):
            raise ExpressionError(f"第 {node.lineno} 行 {type(node.op).__name__} 不支持文本或对象数组；逐点文本由 batch 原样收集，不参与算术或隐式拼接")
        if not all(type(v) in (int,float) or isinstance(v,np.ndarray) for v in (a,b)):raise ExpressionError("算术运算仅接受数字或数值数组")
        if isinstance(node.op,ast.Pow) and np.any(np.abs(b)>32):raise ExpressionError("幂指数绝对值不能超过 32")
        if isinstance(a,np.ndarray) or isinstance(b,np.ndarray):
            if isinstance(node.op,ast.MatMult):
                if np.ndim(a)!=2 or np.ndim(b) not in (1,2) or np.shape(a)[-1]!=np.shape(b)[0]:raise ExpressionError("矩阵乘法形状不符")
                prospective=(np.shape(a)[0],)+(np.shape(b)[1:] if np.ndim(b)==2 else ())
            else:prospective=np.broadcast_shapes(np.shape(a),np.shape(b))
            if math.prod(prospective)>numeric.MAX_ELEMENTS:raise ExpressionError("运算数组超过预算")
        with np.errstate(over="raise",invalid="raise",divide="raise"): result=BINARY[type(node.op)](a,b)
        if isinstance(result,complex):raise ExpressionError("首版不支持复数")
        return numeric.guard(result)
    if isinstance(node,ast.UnaryOp):
        value=ev(node.operand)
        return not value if isinstance(node.op,ast.Not) else (-value if isinstance(node.op,ast.USub) else +value)
    if isinstance(node,ast.BoolOp):
        for child in node.values:
            value=ev(child)
            if isinstance(node.op,ast.And) and not value:return value
            if isinstance(node.op,ast.Or) and value:return value
        return value
    if isinstance(node,ast.Compare):
        left=ev(node.left)
        for op,right_node in zip(node.ops,node.comparators):
            right=ev(right_node);result=COMPARE[type(op)](left,right)
            if len(node.ops)==1:return result
            if not result:return False
            left=right
        return True
    if isinstance(node,ast.IfExp):return ev(node.body if ev(node.test) else node.orelse)
    if isinstance(node,ast.Call):
        name=qualified(node.func,aliases)
        if name in FUNCTIONS and name not in numeric.FUNCTIONS:name="math."+name
        return numeric.call(name,[ev(a) for a in node.args],{k.arg:ev(k.value) for k in node.keywords})
    raise ExpressionError("未支持的表达式")


def program(source,entrypoint=None,legacy=False):
    if len(source)>MAX_SOURCE_CHARS:raise ExpressionError("源码超过 16000 字符")
    tree=ast.parse(source);aliases={"math":"math"};definitions=[]
    permitted=PERMITTED_IMPORTS
    for stmt in tree.body:
        if isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Constant) and isinstance(stmt.value.value,str):continue
        if isinstance(stmt,ast.Import):
            for item in stmt.names:
                if item.name not in permitted:raise ExpressionError(f"禁止导入：{item.name}")
                aliases[item.asname or item.name.split('.')[0]]=item.name if item.asname else item.name.split('.')[0]
        elif isinstance(stmt,ast.ImportFrom) and stmt.module in permitted and not stmt.level:
            for item in stmt.names:
                path=stmt.module+"."+item.name
                if path not in numeric.FUNCTIONS and path not in numeric.CONSTANTS and path not in permitted:raise ExpressionError(f"未登记导入：{path}")
                aliases[item.asname or item.name]=path
        elif isinstance(stmt,ast.FunctionDef):definitions.append(stmt)
        else:raise ExpressionError("源码顶层只允许受控导入和单个纯计算函数")
    if len(definitions)!=1:raise ExpressionError("必须有且只有一个纯计算函数")
    fn=definitions[0]
    if entrypoint is not None and fn.name!=entrypoint:raise ExpressionError(f"未找到入口：{entrypoint}")
    params=[a.arg for a in fn.args.posonlyargs+fn.args.args+fn.args.kwonlyargs]
    if fn.decorator_list or fn.args.vararg or fn.args.kwarg:raise ExpressionError("不支持装饰器、*args 或 **kwargs")
    if legacy and (fn.name!="compute" or params!=["inputs"] or fn.args.defaults or fn.args.kwonlyargs or fn.args.posonlyargs):raise ExpressionError("兼容入口签名必须为 compute(inputs)")
    reserved=set(aliases)|set(numeric.FUNCTIONS)|set(FUNCTIONS)|set(CONSTANTS)
    if any(p.startswith('_') or p in reserved for p in params):raise ExpressionError("参数名称与受控库/函数冲突")
    transforms=set()
    def expression(expr):
        check_tree(expr,aliases)
        for n in ast.walk(expr):
            if isinstance(n,ast.Call):
                op=numeric.TRANSFORMS.get(qualified(n.func,aliases))
                if op:transforms.add(op)
            if isinstance(n,ast.Slice):transforms.add("slice")
            if isinstance(n,ast.Subscript) and not (isinstance(n.slice,ast.Constant) and isinstance(n.slice.value,str)):transforms.add("slice")
    def validate(stmts):
        for stmt in stmts:
            if not isinstance(stmt,STATEMENT_NODES):raise ExpressionError(f"Python 子集不支持：{type(stmt).__name__}")
            if isinstance(stmt,(ast.Assign,ast.AnnAssign)):
                targets=stmt.targets if isinstance(stmt,ast.Assign) else [stmt.target]
                if len(targets)!=1 or not isinstance(targets[0],ast.Name):raise ExpressionError(f"第 {stmt.lineno} 行仅允许给单个局部变量赋值，不支持解包或字段赋值")
                if targets[0].id in reserved:raise ExpressionError(f"第 {stmt.lineno} 行变量 {targets[0].id!r} 与受控函数或常量冲突；请换用其他局部变量名，或直接使用已提供的常量")
                if targets[0].id.startswith('_'):raise ExpressionError(f"第 {stmt.lineno} 行局部变量不得以下划线开头")
                if stmt.value is None:raise ExpressionError("赋值必须有值")
                expression(stmt.value)
            elif isinstance(stmt,ast.Return) and stmt.value is not None:expression(stmt.value)
            elif isinstance(stmt,ast.If):expression(stmt.test);validate(stmt.body);validate(stmt.orelse)
            elif isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Constant) and isinstance(stmt.value.value,str):pass
            else:raise ExpressionError(f"Python 子集不支持：{type(stmt).__name__}")
    validate(fn.body);defaults={};positional=fn.args.posonlyargs+fn.args.args
    for arg,value in zip(positional[len(positional)-len(fn.args.defaults):],fn.args.defaults):expression(value);defaults[arg.arg]=evaluate(value,{},aliases)
    for arg,value in zip(fn.args.kwonlyargs,fn.args.kw_defaults):
        if value is not None:expression(value);defaults[arg.arg]=evaluate(value,{},aliases)
    return fn,aliases,params,defaults,transforms


def python_body(source):return program(source,legacy=True)[0].body


def run_function(source,entrypoint,arguments,legacy=False):
    fn,aliases,params,defaults,_=program(source,entrypoint,legacy)
    if set(arguments)-set(params):raise ExpressionError("适配器传入原函数没有的参数")
    values={**copy.deepcopy(defaults),**copy.deepcopy(arguments)}
    missing=set(params)-values.keys()
    if missing:raise ExpressionError(f"原函数缺少参数：{sorted(missing)}")
    def run(stmts):
        for stmt in stmts:
            if not isinstance(stmt,STATEMENT_NODES):raise ExpressionError(f"Python 子集不支持：{type(stmt).__name__}")
            if isinstance(stmt,(ast.Assign,ast.AnnAssign)):
                target=stmt.targets[0] if isinstance(stmt,ast.Assign) else stmt.target
                values[target.id]=evaluate(stmt.value,values,aliases)
            elif isinstance(stmt,ast.Return):return True,evaluate(stmt.value,values,aliases)
            elif isinstance(stmt,ast.If):
                done,value=run(stmt.body if evaluate(stmt.test,values,aliases) else stmt.orelse)
                if done:return done,value
        return False,None
    done,value=run(fn.body)
    if not done:raise ExpressionError("原函数没有返回结果")
    return value


def execute_module(module,inputs):
    from .plan_audit import run_checks
    run_checks(module.input_checks,inputs)
    def coerce(value,port):
        if port.type=="array":return numeric.array(value,port.value_type.dtype)
        if port.type=="flat_dict":return {k:coerce(value[k],p) for k,p in port.fields.items() if k in value}
        return value
    inputs={k:coerce(inputs[k],p) for k,p in module.inputs.items() if k in inputs}
    if module.implementation=='trusted_builtin':
        from .dbsrc import execute_builtin
        value=execute_builtin(module,inputs)
    elif module.implementation=="expressions":value={name:evaluate(parse(expr),inputs) for name,expr in module.expressions.items()}
    elif module.implementation=="python_function":
        from .adapters import map_arguments,map_returns
        value=run_function(module.source,module.entrypoint,map_arguments(module,inputs));value=map_returns(module,value)
    else:value=run_function(module.source,"compute",{"inputs":inputs},legacy=True)
    if not isinstance(value,dict):raise ExpressionError("外部输出必须是声明过的字典")
    return numeric.json_value(value)


def analyze_source(source,entrypoint=None):
    try:
        fn,aliases,params,defaults,transforms=program(source,entrypoint)
        reads=[];returns=[];calls=[]
        for node in ast.walk(fn):
            if isinstance(node,ast.Call):calls.append(qualified(node.func,aliases))
            if isinstance(node,ast.Subscript):reads.append(ast.unparse(node))
            if isinstance(node,ast.Return):returns.append(ast.unparse(node.value))
        return {"supported":True,"entrypoint":fn.name,"parameters":params,"required_parameters":[p for p in params if p not in defaults],"defaults":numeric.json_value(defaults),"reads":sorted(set(reads)),"return_expressions":returns,"calls":sorted(set(calls)),"explicit_transforms_required":sorted(transforms),"docstring":ast.get_docstring(fn) or "","semantic_questions":[{"parameter":p,"question":f"请声明 {p} 的物理含义、单位，以及它在外部接口中的来源","evidence":"函数签名只能证明参数名称，不能证明物理含义"} for p in params],"facts_only":True}
    except (ValueError,SyntaxError,TypeError) as exc:return {"supported":False,"diagnostics":[{"code":"UNSUPPORTED_SOURCE","message":str(exc),"path":"/source","severity":"error"}]}
