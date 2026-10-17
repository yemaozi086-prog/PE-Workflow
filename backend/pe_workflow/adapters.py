"""Declarative boundary mapping; physical transforms are not adapter operations."""
from .models import Module, Mapping
from . import numeric


def unit_spec(port):
    from .engine import UNITS
    if port.unit_definition:
        u=port.unit_definition
        return u.dimension,u.scale,u.offset
    return UNITS.get(port.unit)


def conversion(value,from_unit,to_unit):
    from .engine import UNITS
    a,b=UNITS.get(from_unit),UNITS.get(to_unit)
    if not a or not b or a[0]!=b[0]:raise ValueError(f"适配器单位未登记或量纲冲突：{from_unit} → {to_unit}")
    if isinstance(value,list):value=numeric.array(value)
    return (value*a[1]+a[2]-b[2])/b[1]


def resolve(mapping:Mapping,root):
    if mapping.assemble is not None:return {k:resolve(v,root) for k,v in mapping.assemble.items()}
    value=root
    for segment in mapping.from_path:
        if isinstance(value,numeric.np.ndarray):raise ValueError('适配器不能截取数值数组；请声明显式变换')
        if isinstance(value,dict):
            if not isinstance(segment,str) or segment not in value:raise ValueError(f"映射字典路径不存在：{mapping.from_path}")
        elif isinstance(value,(list,tuple)) or hasattr(value,"shape"):
            if not isinstance(segment,int) or isinstance(segment,bool):raise ValueError("序列路径必须使用整数下标")
        else:raise ValueError(f"映射路径无法取值：{mapping.from_path}")
        value=value[segment]
    if mapping.conversion:value=conversion(value,mapping.conversion.from_unit,mapping.conversion.to_unit)
    return value


def input_leaf(module,path):
    if len(path)<2 or path[0]!="inputs" or path[1] not in module.inputs:raise ValueError(f"输入映射必须来自声明输入：{path}")
    port=module.inputs[path[1]]
    for field in path[2:]:
        if port.type!="flat_dict" or field not in port.fields:raise ValueError(f"输入映射字段不存在：{path}")
        port=port.fields[field]
    return port


def check_mapping(mapping,module,side,target=None):
    if mapping.assemble is not None:
        if target is not None and (target.type!="flat_dict" or target.fields.keys()!=mapping.assemble.keys()):raise ValueError("输出组装字段必须与外部扁平字典完全对应")
        for key,value in mapping.assemble.items():check_mapping(value,module,side,target.fields[key] if target else None)
        return
    path=mapping.from_path
    if side=="input":
        source=input_leaf(module,path)
        if mapping.conversion and (source.type not in ("number","array") or source.unit!=mapping.conversion.from_unit):raise ValueError("输入映射换算起点必须与外部声明单位一致")
    elif path[0]!="return":raise ValueError("输出映射必须来自 return")
    if side=="output" and mapping.conversion and (target is None or target.type not in ("number","array") or target.unit!=mapping.conversion.to_unit):raise ValueError("输出换算终点必须与外部声明单位一致")
    if mapping.conversion:conversion(0,mapping.conversion.from_unit,mapping.conversion.to_unit)


def check_adapter(module:Module,params,defaults):
    if module.adapter is None:raise ValueError("python_function 必须提供明确的 adapter")
    args=module.adapter.arguments
    if args.keys()-set(params) or set(params)-defaults.keys()-args.keys():raise ValueError("适配器未覆盖全部必需参数或包含未知参数")
    if module.adapter.returns.keys()!=module.outputs.keys():raise ValueError("输出适配器必须覆盖全部外部输出")
    for value in args.values():check_mapping(value,module,"input")
    for key,value in module.adapter.returns.items():check_mapping(value,module,"output",module.outputs[key])


def map_arguments(module,inputs):return {k:resolve(v,{"inputs":inputs}) for k,v in module.adapter.arguments.items()}
def map_returns(module,result):return {k:resolve(v,{"return":result}) for k,v in module.adapter.returns.items()}
