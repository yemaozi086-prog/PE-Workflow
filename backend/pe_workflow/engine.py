from __future__ import annotations

import ast
import math
import time
from typing import Any
from .models import Binding, Diagnostic, Module, Node, Port, Workflow
from .expressions import CONSTANTS, FUNCTIONS, DEFAULT_ALIASES, execute_module, parse, python_body, program, qualified
from . import numeric

UNITS = {"": ("none", 1, 0), "1": ("none", 1, 0),
         "A": ("current", 1, 0), "mA": ("current", .001, 0),
         "V": ("voltage", 1, 0), "mV": ("voltage", .001, 0),
         "W": ("power", 1, 0), "mW": ("power", .001, 0), "kW": ("power", 1000, 0),
         "Hz": ("frequency", 1, 0), "kHz": ("frequency", 1000, 0),
         "H": ("inductance", 1, 0), "uH": ("inductance", 1e-6, 0),
         "F": ("capacitance", 1, 0), "nF": ("capacitance", 1e-9, 0),
         "J": ("energy", 1, 0), "mJ": ("energy", .001, 0),
         "ohm": ("resistance", 1, 0), "mohm": ("resistance", .001, 0),
         "degC": ("temperature", 1, 273.15), "K": ("temperature", 1, 0)}
# Thermal resistance uses a temperature DIFFERENCE per power: no Celsius offset.
UNITS.update({'K/W':('thermal_resistance',1,0),'degC/W':('thermal_resistance',1,0),'°C/W':('thermal_resistance',1,0)})
UNITS.update({"s":("time",1,0),"ms":("time",.001,0),"us":("time",1e-6,0)})
UNITS.update({'uF':('capacitance',1e-6,0),'nH':('inductance',1e-9,0),'MHz':('frequency',1e6,0),
    'Ω':('resistance',1,0),'°C':('temperature',1,273.15),'T':('flux_density',1,0),'mT':('flux_density',1e-3,0),
    'm':('length',1,0),'mm':('length',1e-3,0),'m^2':('area',1,0),'mm^2':('area',1e-6,0),
    'm^3':('volume',1,0),'mm^3':('volume',1e-9,0),'m^4':('area_product',1,0),'mm^4':('area_product',1e-12,0),
    'W/m^3':('power_density',1,0),'kW/m^3':('power_density',1000,0),
    '%':('ratio',.01,0),'匝':('turns',1,0),'个':('count',1,0),'只':('count',1,0),'元':('cost',1,0)})


class WorkflowError(Exception):
    def __init__(self, diagnostic: Diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


def fail(code, message, path, node_id=None, port=None, suggestion=""):
    raise WorkflowError(Diagnostic(code=code, message=message, path=path, node_id=node_id, port=port, suggestion=suggestion))


def validate_value(value, port: Port, path: str):
    if port.type == "number":
        try:
            valid = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            valid = False
    elif port.type == "string":
        valid = isinstance(value, str) and len(value) <= 10000
    elif port.type == "boolean":
        valid = type(value) is bool
    elif port.type == "array":
        try:
            if not isinstance(value,(list,numeric.np.ndarray)):
                fail("ARRAY_TYPE", "数组输入必须是 JSON 数组或数值数组", path)
            data=numeric.array(value,port.value_type.dtype)
            if data.ndim!=len(port.value_type.shape):
                fail("ARRAY_RANK", f"数组维数不符：{data.shape} → {port.value_type.shape}", path)
            for actual,expected in zip(data.shape,port.value_type.shape):
                if isinstance(expected,int) and actual!=expected:fail("ARRAY_SHAPE", f"固定形状不符：{data.shape} → {port.value_type.shape}",path)
            return data.tolist()
        except WorkflowError:raise
        except (ValueError,TypeError,OverflowError) as exc:fail("ARRAY_VALUE",str(exc),path)
    elif port.type == "flat_dict":
        valid = isinstance(value, dict)
        if valid:
            value = fill_inputs(value, port.fields, path)
    else:
        valid = isinstance(value, list) and len(value) <= 1000
        if valid:
            value = [validate_value(item, port.item, f"{path}/{i}") for i, item in enumerate(value)]
    if not valid:
        fail("VALUE_TYPE", f"需要 {port.type} 类型的有效值，收到 {str(value)[:120]}", path)
    return value


def fill_inputs(values: dict, ports: dict[str, Port], path: str) -> dict:
    if not isinstance(values, dict):
        fail("VALUE_TYPE", "输入必须是字典", path)
    for key in values.keys() - ports.keys():
        fail("UNKNOWN_INPUT", f"未声明的输入：{key}", f"{path}/{key}")
    result = dict(values)
    for name, port in ports.items():
        if name not in result:
            if not port.required:
                if port.default is not None:result[name] = port.default
                else:continue
            else:
                fail("MISSING_INPUT", f"缺少输入：{name}；没有可使用的显式默认值", f"{path}/{name}", port=name)
        result[name] = validate_value(result[name], port, f"{path}/{name}")
    shape_dimensions(result,ports,path)
    return result


def shape_dimensions(values,ports,path,dimensions=None):
    dimensions=dimensions if dimensions is not None else {}
    for name,port in ports.items():
        if name not in values:continue
        value=values[name]
        if port.type=="flat_dict":shape_dimensions(value,port.fields,path+"/"+name,dimensions)
        if port.type!="array":continue
        array=numeric.np.asarray(value)
        for index,(size,declared,axis) in enumerate(zip(array.shape,port.value_type.shape,port.axes)):
            if isinstance(declared,str):
                if declared in dimensions and dimensions[declared]!=size:fail("SHARED_DIMENSION",f"共享维度 {declared} 不一致：{dimensions[declared]} 与 {size}",path+"/"+name)
                dimensions[declared]=size
            if axis.coordinate_ref:
                coordinate=values;coordinate_port=ports
                parts=axis.coordinate_ref.split('.')
                for i,key in enumerate(parts):
                    if not isinstance(coordinate,dict) or key not in coordinate or key not in coordinate_port:fail("COORDINATE_MISSING",f"坐标参数不存在：{axis.coordinate_ref}",path+"/"+name)
                    coordinate=coordinate[key];cp=coordinate_port[key]
                    coordinate_port=cp.fields
                data=numeric.np.asarray(coordinate)
                if cp.type!="array" or data.ndim!=1 or len(data)!=size:fail("COORDINATE_SHAPE",f"轴 {axis.name} 的坐标长度/类型不符",path+"/"+name)
                if axis.name=="time" and cp.quantity!="time":fail("COORDINATE_SEMANTIC","时间轴必须引用声明为 time 的坐标参数",path+"/"+name)
                if axis.monotonic and not numeric.np.all(numeric.np.diff(data)>0):fail("COORDINATE_ORDER","坐标必须严格递增",path+"/"+name)
    return dimensions


def compatible(source: Port, target: Port, path: str):
    from .adapters import unit_spec
    if source.unit==target.unit and unit_spec(source)!=unit_spec(target):fail('UNIT_CONFLICT','相同单位名称的定义不一致',path)
    if source.type != target.type:
        fail("PORT_TYPE", f"端口类型不同：{source.type} → {target.type}", path)
    if source.type == "flat_dict":
        # Shape is exact: no surprise hidden keys accepted at runtime.
        if source.fields.keys() != target.fields.keys():
            fail("DICT_FIELDS", f"字典字段不一致：{list(source.fields)} → {list(target.fields)}", path)
        for key in target.fields:
            compatible(source.fields[key], target.fields[key], f"{path}/{key}")
    elif source.type == "collection":
        compatible(source.item, target.item, f"{path}/item")
    else:
        if source.type=="array":
            if source.value_type.dtype!=target.value_type.dtype:fail("ARRAY_DTYPE","数组 dtype 不同；需显式转换模块",path)
            if len(source.axes)!=len(target.axes) or [a.name for a in source.axes]!=[a.name for a in target.axes]:fail("ARRAY_AXES","数组轴含义或顺序不一致；不能自动转置",path)
            for a,b in zip(source.value_type.shape,target.value_type.shape):
                if isinstance(a,int) and isinstance(b,int) and a!=b:fail("ARRAY_SHAPE","固定数组形状不一致",path)
            if any(bool(a.coordinate_ref)!=bool(b.coordinate_ref) for a,b in zip(source.axes,target.axes)):fail("ARRAY_COORDINATES","数组坐标声明不完整",path)
        for key in ("quantity", "characteristic", "scope", "role", "index",'semantic_id','family_id','device','nature','statistic','event','direction','aggregation','loss_component','instance_id'):
            a, b = getattr(source, key), getattr(target, key)
            if b and a != b:
                fail("SEMANTIC_CONFLICT", f"{key} 不匹配：{a} → {b}", path)
        if source.unit != target.unit:
            from .adapters import unit_spec
            a,b=unit_spec(source),unit_spec(target)
            if not a or not b or a[0] != b[0] or source.type not in ("number","array"):
                if not(source.quantity==target.quantity=='ratio' and source.unit in ('','1','%') and target.unit in ('','1','%')):
                    fail("UNIT_CONFLICT", f"不能换算单位：{source.unit} → {target.unit}", path)


def dictionary_plan(source,target,field_map,path):
    if source.type!='flat_dict' or target.type!='flat_dict':fail('DICTIONARY_PORT_REQUIRED','新版计算连接必须传递扁平字典',path)
    if field_map.keys()-target.fields.keys():fail('UNKNOWN_FIELD_MAP','映射包含未声明的目标字段',path)
    result=[]
    for name,port in target.fields.items():
        origin=field_map.get(name,name)
        if origin not in source.fields:
            if name not in field_map and not port.required:
                if port.default is not None:result.append({'target_field':name,'source_field':None,'default':port.default,'conversion':None})
                continue
            fail('MISSING_DICT_FIELD',f'缺少所需字段：{name}（来源 {origin}）',path+'/fields/'+name,port=name)
        other=source.fields[origin];compatible(other,port,path+'/fields/'+name)
        result.append({'target_field':name,'source_field':origin,'conversion':f'{other.unit} → {port.unit}' if other.unit!=port.unit else None})
    origins={r['target_field']:r['source_field'] for r in result}
    for name,port in target.fields.items():
        if port.type!='array' or not origins.get(name):continue
        other=source.fields[origins[name]]
        for sa,ta in zip(other.axes,port.axes):
            if ta.coordinate_ref and origins.get(ta.coordinate_ref)!=sa.coordinate_ref:fail('COORDINATE_BINDING','字典内波形与坐标的映射必须保持同一采样关系',path+'/fields/'+name)
    return result


def project_dictionary(value,source,target,field_map,path):
    plan=dictionary_plan(source,target,field_map,path)
    # Validate all supplied fields against the producer, including unused fields.
    value=validate_value(value,source,path+'/source')
    result={}
    for row in plan:
        name=row['target_field'];origin=row['source_field'];port=target.fields[name]
        if origin is None:result[name]=row['default'];continue
        if origin not in value:
            if not port.required:
                if port.default is not None:result[name]=port.default
                continue
            fail('MISSING_DICT_FIELD',f'缺少所需字段：{name}（来源 {origin}）',path+'/fields/'+name,port=name)
        result[name]=convert(value[origin],source.fields[origin],port)
    return validate_value(result,target,path+'/target')


def convert(value, source: Port, target: Port):
    if source.type == "flat_dict":
        return {k: convert(value[k], source.fields[k], target.fields[k]) for k in target.fields if k in value}
    if source.type == "collection":
        return [convert(v, source.item, target.item) for v in value]
    if source.unit != target.unit:
        from .adapters import unit_spec
        a,b=unit_spec(source),unit_spec(target)
        if source.type=="array":return ((numeric.array(value,source.value_type.dtype)*a[1]+a[2]-b[2])/b[1]).tolist()
        return (value*a[1]+a[2]-b[2])/b[1]
    return value


def check_module(module: Module):
    if module.contract_version=='3.0':
        for side,ports in (('inputs',module.inputs),('outputs',module.outputs)):
            for name,port in ports.items():
                if port.type!='flat_dict':fail('DICTIONARY_PORT_REQUIRED','新版计算端口必须是有名字的扁平字典',f'/module/{side}/{name}')
                for key,field in port.fields.items():
                    if not field.quantity and not field.semantic_id:fail('FIELD_SEMANTIC_REQUIRED','每个字典字段必须声明语义',f'/module/{side}/{name}/fields/{key}')
    from .naming import check_module_rules
    check_module_rules(module)
    if module.implementation != 'trusted_builtin' and (module.builtin_id or module.builtin_hash):
        fail('BUILTIN_REFERENCE','普通计算模块不能声明内置执行入口','/module')
    if module.implementation == "expressions" and module.source:
        fail("MODULE_IMPLEMENTATION", "expressions 模块不能同时提供 Python source", "/module")
    if module.implementation in ("python_subset","python_function") and module.expressions:
        fail("MODULE_IMPLEMENTATION", "python_subset 模块不能同时提供 expressions", "/module")
    if not module.outputs:
        fail("MODULE_OUTPUTS", "模块必须至少声明一个输出", "/module/outputs")
    for name, port in {**module.inputs, **module.outputs}.items():
        if not name.isidentifier() or name.startswith("_") or name in (*FUNCTIONS, *CONSTANTS, *DEFAULT_ALIASES):
            fail("PORT_NAME", f"端口名称不能作为受控表达式变量：{name}", "/module")
        check_port(port, f"/module/ports/{name}")
        if not port.required and port.default is not None:
            validate_value(port.default, port, f"/module/inputs/{name}/default")
    try:
        if module.implementation == 'trusted_builtin':
            from .dbsrc import check_builtin
            check_builtin(module)
        elif module.implementation == "expressions":
            if module.outputs.keys() != module.expressions.keys():
                fail("EXPRESSION_OUTPUTS", "输出声明和表达式名称必须完全相同", "/module/expressions")
            for name, expression in module.expressions.items():
                tree = parse(expression)
                used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
                unknown = used - module.inputs.keys() - FUNCTIONS.keys() - CONSTANTS.keys() - DEFAULT_ALIASES.keys() - numeric.FUNCTIONS.keys()
                if unknown:
                    fail("EXPRESSION_NAME", f"未知变量：{', '.join(sorted(unknown))}", f"/module/expressions/{name}")
                required=set()
                for n in ast.walk(tree):
                    if isinstance(n,ast.Call):
                        op=numeric.TRANSFORMS.get(qualified(n.func,DEFAULT_ALIASES))
                        if op:required.add(op)
                    if isinstance(n,ast.Slice):required.add("slice")
                    if isinstance(n,ast.Subscript) and not (isinstance(n.slice,ast.Constant) and isinstance(n.slice.value,str)):required.add('slice')
                if required-{t.operation for t in module.transforms}:fail("UNDECLARED_TRANSFORM",f"需要显式变换声明：{sorted(required)}","/module/transforms")
        elif module.implementation=="python_function":
            if not module.entrypoint:fail("ENTRYPOINT_REQUIRED","必须明确声明原函数入口","/module/entrypoint")
            fn,aliases,params,defaults,transforms=program(module.source,module.entrypoint)
            from .adapters import check_adapter
            check_adapter(module,params,defaults)
            if transforms-{t.operation for t in module.transforms}:fail("UNDECLARED_TRANSFORM",f"需要显式变换声明：{sorted(transforms)}","/module/transforms")
        else:
            _,_,_,_,transforms=program(module.source,legacy=True)
            if transforms-{t.operation for t in module.transforms}:fail("UNDECLARED_TRANSFORM","需要显式变换声明","/module/transforms")
    except WorkflowError:
        raise
    except (ValueError, SyntaxError) as exc:
        fail("MODULE_SYNTAX", str(exc), "/module")


def check_port(port: Port, path):
    if port.definition_ref and not port.quantity and not port.fields:fail("UNRESOLVED_DEFINITION","参数定义引用尚未解析","/ports")
    if port.unit_definition and port.unit in UNITS:
        known=UNITS[port.unit];declared=port.unit_definition
        if (declared.dimension,declared.scale,declared.offset)!=known:fail("UNIT_DECLARATION","自定义声明不能覆盖内置单位含义",path)
    if port.unit and port.unit not in UNITS and not port.unit_definition:
        fail("UNKNOWN_UNIT", f"尚未登记单位：{port.unit}；需先扩展单位表", path)
    dimension = port.unit_definition.dimension if port.unit_definition else UNITS.get(port.unit,("none",))[0]
    known = {unit[0] for unit in UNITS.values()} - {"none"}
    if port.type in ("number","array") and (port.quantity in known or port.unit_definition) and dimension != port.quantity and not(port.quantity=='ratio' and dimension=='none'):
        fail("UNIT_DECLARATION", f"物理量 {port.quantity} 与单位 {port.unit or '(缺失)'} 不一致", path)
    if not port.required and port.default is not None:
        validate_value(port.default, port, path + "/default")
    for name, field in port.fields.items():
        check_port(field, path + "/fields/" + name)
    if port.item:
        check_port(port.item, path + "/item")


def interface(node: Node, modules: dict[str, Module]):
    if node.kind == "compute":
        key = f"{node.module_id}@{node.module_version}"
        if key not in modules:
            fail("MODULE_NOT_FOUND", f"模块版本不存在：{key}", f"/nodes/{node.id}", node.id)
        module = modules[key]
        check_module(module)
        return module.inputs, module.outputs
    if node.kind == "switch":
        cases = list(node.cases.values())
        first = cases[0]
        if "selector" in first.inputs:
            fail("RESERVED_INPUT", "分支内部不能使用保留输入 selector", f"/nodes/{node.id}", node.id)
        for case in cases[1:]:
            if case.inputs != first.inputs or case.output_ports != first.output_ports:
                fail("BRANCH_CONTRACT", "所有分支必须具有完全相同的输入输出声明", f"/nodes/{node.id}", node.id)
        return {"selector": Port(type="string"), **first.inputs}, first.output_ports
    if node.kind == 'batch':
        if 'item' not in node.body.inputs or 'rows' in node.body.inputs:
            fail('BATCH_ITEM','批量子图必须声明 item，不能声明 rows',f'/nodes/{node.id}')
        def columns(port):
            from .models import ValueType, Axis
            if port.type != 'flat_dict' or any(p.type not in ('number','array') for p in port.fields.values()):
                fail('BATCH_FIELDS','批量字典只允许数值字段或数值数组',f'/nodes/{node.id}')
            fields={}
            for key,p in port.fields.items():
                shape=p.value_type.shape if p.type=='array' else []
                if len(shape)>=4:fail('BATCH_RANK','批量堆叠后数组不能超过四维',f'/nodes/{node.id}')
                dtype=p.value_type.dtype if p.value_type else 'float64'
                fields[key]=p.model_copy(update={'type':'array','kind':'bare','value_type':ValueType(dtype=dtype,shape=['batch_rows',*shape]),'axes':[Axis(name='batch_rows'),*p.axes]})
            return Port(type='flat_dict',fields=fields)
        return {'rows':columns(node.body.inputs['item']),**{k:p for k,p in node.body.inputs.items() if k!='item'}},{k:columns(p) for k,p in node.body.output_ports.items()}
    if node.kind == "foreach":
        if "item" not in node.body.inputs:
            fail("LOOP_ITEM", "遍历子图必须声明 item 输入", f"/nodes/{node.id}", node.id)
        others = {k: p for k, p in node.body.inputs.items() if k != "item"}
        if "items" in others:
            fail("RESERVED_INPUT", "遍历子图不能使用保留输入 items", f"/nodes/{node.id}", node.id)
        if node.body.schema_version=='3.0':
            if any(p.type!='flat_dict' for p in node.body.output_ports.values()):fail('LOOP_OUTPUT','新版遍历子图应返回扁平字典',f'/nodes/{node.id}')
            return {'items':Port(type='collection',item=node.body.inputs['item']),**others},{k:Port(type='collection',item=p) for k,p in node.body.output_ports.items()}
        if any(p.type in ("flat_dict", "collection") for p in node.body.output_ports.values()):
            fail("LOOP_OUTPUT", "首版遍历子图的输出必须是裸参数", f"/nodes/{node.id}", node.id)
        return {"items": Port(type="collection", item=node.body.inputs["item"]), **others}, {
            "results": Port(type="collection", item=Port(type="flat_dict", fields=node.body.output_ports))}
    # Inferred from actual connected collection, filled during compile.
    return {"items": Port(type="collection", item=Port())}, {"value": Port()}


def binding_port(binding: Binding, wf: Workflow, outputs, path):
    if binding.source == "constant":
        return None
    ports = wf.inputs if binding.source == "input" else outputs.get(binding.node)
    if ports is None or binding.port not in ports:
        fail("UNKNOWN_REFERENCE", f"来源不存在：{binding.node or '$input'}.{binding.port}", path)
    port = ports[binding.port]
    if binding.field:
        if port.type != "flat_dict" or binding.field not in port.fields:
            fail("UNKNOWN_FIELD", f"字典字段不存在：{binding.field}", path)
        port = port.fields[binding.field]
    return port


def compile_workflow(wf: Workflow, modules, depth=0, connection_plans=None):
    if depth > 6:
        fail("DEPTH_LIMIT", "子图嵌套不能超过 6 层", "/nodes")
    if wf.schema_version=='3.0':
        for side,ports in (('inputs',wf.inputs),('output_ports',wf.output_ports)):
            for name,port in ports.items():
                if port.type not in ('flat_dict','collection'):fail('DICTIONARY_PORT_REQUIRED','新版工作流物理接口必须为字典；集合仅用于流程控制',f'/{side}/{name}')
        for node in wf.nodes:
            if node.kind=='compute' and f'{node.module_id}@{node.module_version}' in modules and modules[f'{node.module_id}@{node.module_version}'].contract_version!='3.0':fail('LEGACY_MODULE_IN_V3','新版图需引用明确迁移到字典接口的模块版本',f'/nodes/{node.id}')
    ids = [n.id for n in wf.nodes]
    if len(ids) != len(set(ids)):
        fail("DUPLICATE_NODE", "节点 ID 重复", "/nodes")
    for name, port in wf.inputs.items():
        check_port(port, f"/inputs/{name}")
    for name, port in wf.output_ports.items():
        check_port(port, f"/output_ports/{name}")
    if wf.outputs.keys() != wf.output_ports.keys() or not wf.outputs:
        fail("OUTPUT_CONTRACT", "工作流输出绑定和声明必须完全对应且非空", "/outputs")
    interfaces = {n.id: interface(n, modules) for n in wf.nodes}
    outputs = {key: pair[1] for key, pair in interfaces.items()}
    remaining = {n.id: {b.node for b in n.inputs.values() if b.source == "node"} for n in wf.nodes}
    for node_id, deps in remaining.items():
        if deps - set(ids):
            fail("UNKNOWN_REFERENCE", f"引用不存在的节点：{sorted(deps - set(ids))}", f"/nodes/{node_id}", node_id)
    order = []
    while remaining:
        ready = sorted(k for k, deps in remaining.items() if not deps)
        if not ready:
            fail("CYCLE", f"存在循环依赖：{', '.join(sorted(remaining))}；请使用显式控制节点", "/nodes")
        order.extend(ready)
        for key in ready:
            remaining.pop(key)
        for deps in remaining.values():
            deps.difference_update(ready)
    by_id = {n.id: n for n in wf.nodes}
    warnings = []
    for node_id in order:
        node = by_id[node_id]
        path = f"/nodes/{node_id}"
        ins, outs = interfaces[node_id]
        if node.kind == "aggregate" and "items" in node.inputs:
            src = binding_port(node.inputs["items"], wf, outputs, f"{path}/inputs/items")
            if src is None or src.type != "collection":
                fail("AGGREGATE_SOURCE", "汇总必须连接声明过元素类型的集合", path, node_id)
            element = src.item
            if node.field:
                if element.type != "flat_dict" or node.field not in element.fields:
                    fail("UNKNOWN_FIELD", f"汇总字段不存在：{node.field}", path, node_id)
                element = element.fields[node.field]
            if element.type != "number":
                fail("AGGREGATE_TYPE", "汇总只支持数字元素或数字字段", path, node_id)
            characteristic = {"max": "max", "min": "min", "mean": "average", "sum": "sum"}[node.operation]
            ins, outs = {"items": src}, {"value": element.model_copy(update={"characteristic": characteristic})}
            if wf.schema_version=='3.0':outs={'summary':Port(type='flat_dict',fields=outs)}
            interfaces[node_id] = (ins, outs)
            outputs[node_id] = outs
        for key in node.inputs.keys() - ins.keys():
            fail("UNKNOWN_INPUT", f"节点没有此输入：{key}", f"{path}/inputs/{key}", node_id, key)
        for key, port in ins.items():
            loc = f"{path}/inputs/{key}"
            if key not in node.inputs:
                if port.required or port.default is None:
                    fail("MISSING_BINDING", f"输入 {key} 没有连接或显式常量", loc, node_id, key)
                continue
            binding = node.inputs[key]
            try:
                source = binding_port(binding, wf, outputs, loc)
                if source is None:
                    validate_value(binding.value, port, loc)
                else:
                    is_dictionary=node.kind=='compute' and modules[f'{node.module_id}@{node.module_version}'].contract_version=='3.0'
                    if binding.field_map and not is_dictionary:fail('FIELD_MAP_VERSION','字段映射仅适用于新版计算字典端口',loc)
                    if is_dictionary:
                        plan=dictionary_plan(source,port,binding.field_map,loc)
                        if connection_plans is not None:connection_plans.append({'node_id':node_id,'port':key,'path':loc,'source':binding.model_dump(),'fields':plan,'ignored_fields':sorted(source.fields.keys()-{p['source_field'] for p in plan if p['source_field']})})
                        for item in plan:
                            if item.get('conversion'):warnings.append(Diagnostic(code='UNIT_CONVERSION',message=f"{item['source_field']} → {item['target_field']}：{item['conversion']}",severity='warning',path=loc,node_id=node_id,port=key))
                    else:compatible(source, port, loc)
                    if port.type=='array':
                        for sa,ta in zip(source.axes,port.axes):
                            if not ta.coordinate_ref:continue
                            coordinate_binding=node.inputs.get(ta.coordinate_ref)
                            # Whole flat dictionaries preserve their local coordinate relation.
                            if binding.field:
                                expected=Binding(source=binding.source,node=binding.node,port=binding.port,field=sa.coordinate_ref)
                            else:
                                expected=Binding(source=binding.source,node=binding.node,port=sa.coordinate_ref)
                            if coordinate_binding!=expected:fail('COORDINATE_BINDING','波形与坐标必须来自同一声明来源；不同采样网格需显式变换',loc,node_id,key)
                    if source.unit != port.unit:
                        warnings.append(Diagnostic(code="UNIT_CONVERSION", message=f"运行时显式换算 {source.unit} → {port.unit}", severity="warning", path=loc, node_id=node_id, port=key))
            except WorkflowError as exc:
                exc.diagnostic.node_id = node_id
                exc.diagnostic.port = key
                raise
        for case, nested in node.cases.items():
            try:
                nested_plans=[]
                _, _, nested_warnings = compile_workflow(nested, modules, depth + 1,nested_plans)
                if connection_plans is not None:connection_plans.extend([{**p,'path':f'{path}/cases/{case}'+p['path']} for p in nested_plans])
                for warning in nested_warnings:
                    warning.path = f"{path}/cases/{case}" + warning.path
                warnings.extend(nested_warnings)
            except WorkflowError as exc:
                exc.diagnostic.path = f"{path}/cases/{case}" + exc.diagnostic.path
                raise
        if node.body:
            try:
                nested_plans=[]
                _, _, nested_warnings = compile_workflow(node.body, modules, depth + 1,nested_plans)
                if connection_plans is not None:connection_plans.extend([{**p,'path':f'{path}/body'+p['path']} for p in nested_plans])
                for warning in nested_warnings:
                    warning.path = f"{path}/body" + warning.path
                warnings.extend(nested_warnings)
            except WorkflowError as exc:
                exc.diagnostic.path = f"{path}/body" + exc.diagnostic.path
                raise
    for key, binding in wf.outputs.items():
        source = binding_port(binding, wf, outputs, f"/outputs/{key}")
        if source:
            if wf.schema_version=='3.0' and wf.output_ports[key].type=='flat_dict':dictionary_plan(source,wf.output_ports[key],binding.field_map,f'/outputs/{key}')
            else:
                if binding.field_map:fail('FIELD_MAP_VERSION','输出字段映射需要新版字典接口',f'/outputs/{key}')
                compatible(source, wf.output_ports[key], f"/outputs/{key}")
        else:
            validate_value(binding.value, wf.output_ports[key], f"/outputs/{key}")
    return order, interfaces, warnings


def validate_workflow(wf, modules, inputs=None):
    try:
        plans=[]
        order, _, warnings = compile_workflow(wf, modules,connection_plans=plans)
        if inputs is not None:
            fill_inputs(inputs, wf.inputs, "/inputs")
        return {"valid": True, "order": order, "diagnostics": [d.model_dump() for d in warnings],'connection_plans':plans}
    except WorkflowError as exc:
        return {"valid": False, "order": [], "diagnostics": [exc.diagnostic.model_dump()]}


def execute(wf: Workflow, modules: dict, inputs: dict, trace=None, prefix="", budget=None, compiled=None):
    trace = trace if trace is not None else []
    budget = budget if budget is not None else [0]
    # Per-run cache only: workflows/modules remain fixed for the whole run.
    # Recompile in a new run so edits and replay snapshots cannot reuse a plan.
    compiled=compiled if compiled is not None else {}
    if id(wf) not in compiled:
        plans=[]
        order, interfaces, _ = compile_workflow(wf, modules,connection_plans=plans)
        compiled[id(wf)]=(order,interfaces,plans)
    order,interfaces,plans=compiled[id(wf)]
    supplied = fill_inputs(inputs, wf.inputs, "/inputs")
    results = {}
    outputs = {key: pair[1] for key, pair in interfaces.items()}
    def resolve(binding, target, projection=False):
        if binding.source == "constant":
            return binding.value
        value = supplied[binding.port] if binding.source == "input" else results[binding.node][binding.port]
        if binding.field:
            value = value[binding.field]
        source=binding_port(binding,wf,outputs,'')
        if projection:return project_dictionary(value,source,target,binding.field_map,'/bindings')
        return convert(value, source, target)
    by_id = {n.id: n for n in wf.nodes}
    for node_id in order:
        budget[0] += 1
        if budget[0] > 20000:
            fail("STEP_LIMIT", "单次运行超过 20000 个节点步骤", f"/nodes/{node_id}", node_id)
        node = by_id[node_id]
        ins, outs = interfaces[node_id]
        entry = {"node_id": node_id, "path": prefix + node_id, "kind": node.kind, "status": "running", "inputs": {}, "outputs": {}, "duration_ms": 0}
        trace.append(entry)
        start = time.perf_counter()
        try:
            projection=node.kind=='compute' and modules[f'{node.module_id}@{node.module_version}'].contract_version=='3.0'
            args = fill_inputs({k: resolve(b, ins[k],projection) for k, b in node.inputs.items()}, ins, f"/nodes/{node_id}/inputs")
            entry["inputs"] = dict(args)
            if projection:
                # Repeated batch rows share one compiled mapping. Keep its full
                # evidence once; reference that trace path in subsequent rows.
                mapping_refs=compiled.setdefault('mapping_references',{})
                mapping_key=(id(wf),node_id)
                if mapping_key in mapping_refs:entry['field_mappings_ref']=mapping_refs[mapping_key]
                else:
                    entry['field_mappings']=[p for p in plans if p['path'].startswith(f'/nodes/{node_id}/inputs/')]
                    mapping_refs[mapping_key]=entry['path']
            if node.kind == "compute":
                module=modules[f"{node.module_id}@{node.module_version}"]
                check_domains(args,module.domains,f"/nodes/{node_id}/inputs")
                value = execute_module(module,args)
            elif node.kind == "switch":
                selector = args["selector"]
                if selector not in node.cases:
                    fail("BRANCH_NOT_FOUND", f"没有分支：{selector}；可用：{list(node.cases)}", f"/nodes/{node_id}", node_id)
                entry["selected_case"] = selector
                value = execute(node.cases[selector], modules, {k:v for k,v in args.items() if k!="selector"}, trace, prefix + node_id + "/" + selector + "/", budget,compiled)
            elif node.kind == 'batch':
                rows=args['rows']; count=len(next(iter(rows.values())))
                if count>1000:fail('BATCH_LIMIT','单个批次最多 1000 行',f'/nodes/{node_id}',node_id)
                evaluations=[execute(node.body,modules,{**{k:v for k,v in args.items() if k!='rows'},'item':{k:v[i] for k,v in rows.items()}},trace,f'{prefix}{node_id}/{i}/',budget,compiled) for i in range(count)]
                if not evaluations:fail('EMPTY_BATCH','批量计算需要至少一个显式候选；无解需走不可行分支',f'/nodes/{node_id}',node_id)
                value={name:{key:[v[name][key] for v in evaluations] for key in port.fields} for name,port in outs.items()}
            elif node.kind == "foreach":
                items = args["items"]
                evaluations=[execute(node.body, modules, {**{k:v for k,v in args.items() if k!='items'},'item':item},trace,f'{prefix}{node_id}/{i}/',budget,compiled) for i,item in enumerate(items)]
                value={k:[v[k] for v in evaluations] for k in outs} if node.body.schema_version=='3.0' else {'results':evaluations}
            else:
                data = [v[node.field] if node.field else v for v in args["items"]]
                if not data:
                    fail("EMPTY_AGGREGATE", "空集合无法汇总", f"/nodes/{node_id}", node_id)
                value = {"value": {"sum": lambda: math.fsum(data), "mean": lambda: math.fsum(data) / len(data), "max": lambda: max(data), "min": lambda: min(data)}[node.operation]()}
                if wf.schema_version=='3.0':value={'summary':value}
            value = fill_inputs(value, outs, f"/nodes/{node_id}/outputs")
            shape_dimensions(value,outs,f"/nodes/{node_id}/outputs",shape_dimensions(args,ins,f"/nodes/{node_id}/inputs"))
            results[node_id] = value
            entry.update(status="succeeded", outputs=value)
        except Exception as exc:
            diagnostic = exc.diagnostic if isinstance(exc, WorkflowError) else Diagnostic(code="COMPUTATION_ERROR", message=str(exc), path=f"/nodes/{node_id}", node_id=node_id)
            entry.update(status="failed", diagnostic=diagnostic.model_dump())
            raise WorkflowError(diagnostic) from exc
        finally:
            entry["duration_ms"] = round((time.perf_counter() - start) * 1000, 3)
    final=fill_inputs({k:resolve(b,wf.output_ports[k],wf.schema_version=='3.0' and wf.output_ports[k].type=='flat_dict') for k,b in wf.outputs.items()},wf.output_ports,"/outputs")
    shape_dimensions(final,wf.output_ports,"/outputs",shape_dimensions(supplied,wf.inputs,"/inputs"))
    return final


def check_domains(values,domains,path):
    for address,limits in domains.items():
        value=values
        for key in address.split('.'):
            if not isinstance(value,dict) or key not in value:fail("DOMAIN_REFERENCE",f"适用范围引用不存在：{address}",path)
            value=value[key]
        data=numeric.np.asarray(value)
        if data.dtype.kind not in "iuf" or (limits.minimum is not None and numeric.np.any(data<limits.minimum)) or (limits.maximum is not None and numeric.np.any(data>limits.maximum)):
            fail("DOMAIN_VIOLATION",f"{address} 超出模块声明的适用范围",path+"/"+address)
