"""Platform-owned numeric function registry; no dynamic user imports."""
import math
import numpy as np
from scipy import integrate, linalg

MAX_ELEMENTS = 100000
NUMERIC_DTYPES = {"float64", "float32", "int64"}


def guard(value):
    if isinstance(value, np.ndarray):
        if value.size > MAX_ELEMENTS or value.dtype.kind not in "biuf" or not np.isfinite(value).all():
            raise ValueError("数组超过预算或包含非有限/非实数值")
    elif isinstance(value, dict):
        if len(value) > 1000: raise ValueError("字典超过预算")
        for item in value.values(): guard(item)
    elif isinstance(value, (list, tuple)):
        if len(value) > MAX_ELEMENTS: raise ValueError("序列超过预算")
        for item in value: guard(item)
    elif isinstance(value, (float, np.floating)) and not math.isfinite(value):
        raise ValueError("非有限数字")
    return value


def json_value(value):
    guard(value)
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, np.generic): return value.item()
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value): raise ValueError("输出字典键必须是字符串")
        return {k: json_value(v) for k,v in value.items()}
    if isinstance(value, (tuple, list)): return [json_value(v) for v in value]
    if value is not None and type(value) not in (str, bool, int, float): raise ValueError(f"不能序列化的计算结果：{type(value).__name__}")
    return value


def allocation(shape):
    sizes = (shape,) if isinstance(shape, int) else tuple(shape)
    if not sizes or len(sizes) > 4 or any(type(v) not in (int, np.int64) or v < 1 for v in sizes) or math.prod(sizes) > MAX_ELEMENTS:
        raise ValueError("数组分配超出形状/元素预算")


def array(value, dtype="float64"):
    if dtype not in NUMERIC_DTYPES: raise ValueError("仅支持 float64/float32/int64 数组")
    guard(value)
    result = np.asarray(value)
    if result.dtype.kind not in "iuf" or result.size > MAX_ELEMENTS: raise ValueError("需要矩形实数数组，禁止 object/字符串/布尔数组")
    if dtype == "int64" and (not np.isfinite(result).all() or np.any(result != np.floor(result)) or np.any(result < -(2**63)) or np.any(result >= 2**63)):
        raise ValueError("int64 转换会丢失数值")
    with np.errstate(over="raise", invalid="raise"): return guard(result.astype(dtype, copy=True))


def zeros(shape, dtype="float64"):
    allocation(shape)
    if dtype not in NUMERIC_DTYPES: raise ValueError("不支持的 dtype")
    return np.zeros(shape, dtype=dtype)


def linspace(start, stop, num=50, endpoint=True):
    allocation(num)
    return guard(np.linspace(start, stop, num=num, endpoint=endpoint))


def solve(a, b, assume_a=None):
    a,b=array(a),array(b)
    if a.ndim!=2 or a.shape[0]!=a.shape[1] or a.shape[0]>512 or b.ndim not in (1,2) or b.shape[0]!=a.shape[0]:
        raise ValueError("solve 需要至多 512 阶方阵及匹配右端向量/矩阵，不自动广播")
    return linalg.solve(a,b,assume_a=assume_a,check_finite=True)


from .missing_values import masked_values

FUNCTIONS = {
    "pe.masked_values":(masked_values,set()),
    **{f"math.{n}": (getattr(math,n),set()) for n in ("sqrt","sin","cos","tan","exp","log","log10","fabs","floor","ceil")},
    "abs":(abs,set()),"min":(min,set()),"max":(max,set()),"float":(float,set()),"int":(int,set()),"len":(len,set()),
    "numpy.array":(array,{"dtype"}),"numpy.asarray":(array,{"dtype"}),"numpy.zeros":(zeros,{"dtype"}),"numpy.linspace":(linspace,{"num","endpoint"}),
    **{f"numpy.{n}": (getattr(np,n),set()) for n in ("sqrt","sin","cos","exp","abs","square","diff","all","any")},
    **{f"numpy.{n}": (getattr(np,n),{"axis","keepdims"}) for n in ("mean","sum","max","min","std")},
    "numpy.clip":(np.clip,set()),"numpy.transpose":(np.transpose,{"axes"}),"numpy.reshape":(np.reshape,{"shape"}),"numpy.interp":(np.interp,{"left","right"}),
    "scipy.integrate.trapezoid":(integrate.trapezoid,{"x","dx","axis"}),"scipy.integrate.simpson":(integrate.simpson,{"x","dx","axis"}),"scipy.linalg.solve":(solve,{"assume_a"}),
}
CONSTANTS={"math.pi":math.pi,"math.e":math.e,"numpy.pi":np.pi,"numpy.float64":"float64","numpy.float32":"float32","numpy.int64":"int64"}
TRANSFORMS={"numpy.transpose":"transpose","numpy.reshape":"reshape","numpy.interp":"resample"}


def call(name,args,kwargs):
    fn,allowed=FUNCTIONS[name]
    if kwargs.keys()-allowed: raise ValueError(f"{name} 不支持这些关键字：{sorted(kwargs.keys()-allowed)}")
    for value in (*args,*kwargs.values()): guard(value)
    if name=="numpy.reshape": allocation(args[1] if len(args)>1 else kwargs["shape"])
    arrays=[v for v in args if isinstance(v,np.ndarray)]
    if name=="numpy.clip" and arrays and math.prod(np.broadcast_shapes(*(v.shape for v in arrays)))>MAX_ELEMENTS: raise ValueError("广播结果超过数组预算")
    result=guard(fn(*args,**kwargs))
    return result.item() if isinstance(result,np.generic) else result


def catalog():
    import scipy
    return {"numpy_version":np.__version__,"scipy_version":scipy.__version__,"max_elements":MAX_ELEMENTS,"functions":[{"name":name,"keywords":sorted(keys),"explicit_transform":TRANSFORMS.get(name)} for name,(_,keys) in FUNCTIONS.items()]}
