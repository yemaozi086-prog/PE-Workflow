"""Explicit missing numeric outputs; JSON null encodes NaN, never an input default."""
import numpy as np


def masked_values(values, validity):
    from .numeric import guard, MAX_ELEMENTS
    a=np.asarray(values);v=np.asarray(validity)
    if a.ndim!=1 or a.shape!=v.shape or a.size>MAX_ELEMENTS or a.dtype.kind not in 'iuf':
        raise ValueError('masked_values 需要同长的一维有限数值和 0/1 标记')
    guard(a);guard(v)
    if not np.all((v==0)|(v==1)):raise ValueError('有效标记只能是 0 或 1')
    return [float(x) if flag==1 else None for x,flag in zip(a,v)]


def validate_array(value, port, path):
    from .engine import fail
    from .numeric import array
    if not isinstance(value,list):fail('MISSING_ARRAY','声明缺失值的输出必须是一维列表',path)
    clean=[0.0 if x is None else x for x in value]
    # Zeros here are only a temporary shape/type probe, never returned or computed.
    probe=array(clean,port.value_type.dtype)
    if probe.ndim!=1:fail('MISSING_ARRAY','缺失输出仅支持一维数值列',path)
    size=port.value_type.shape[0]
    if isinstance(size,int) and len(value)!=size:fail('ARRAY_SHAPE','缺失输出长度与声明不符',path)
    return [None if x is None else float(probe[i]) for i,x in enumerate(value)]


def validate_masks(values, ports, path):
    from .engine import fail
    for key,p in ports.items():
        if not p.missing_when or key not in values:continue
        mask=values.get(p.missing_when);column=values[key]
        if not isinstance(mask,list) or len(mask)!=len(column):fail('MISSING_MASK','缺失值列与有效标记长度不同',path+'/'+key)
        for i,(x,flag) in enumerate(zip(column,mask)):
            if flag not in (0,1) or (x is None)!=(flag==0):
                fail('MISSING_MASK','有效标记为 0 必须输出缺失值，为 1 必须输出有限数值',path+'/'+key+'/'+str(i))


def check_policy(port,path):
    from .engine import fail
    if port.missing_when:
        if port.type!='array' or port.value_type.dtype not in ('float64','float32') or len(port.value_type.shape)!=1:
            fail('MISSING_POLICY','缺失 NaN 表示只允许显式一维浮点列',path)
    if port.type=='flat_dict':
        for key,p in port.fields.items():
            if not p.missing_when:continue
            mask=port.fields.get(p.missing_when)
            if mask is None or mask.missing_when or mask.type!='array' or mask.value_type.dtype=='string' or mask.value_type.shape!=p.value_type.shape or not mask.required:
                fail('MISSING_POLICY','缺失列必须引用同字典内必填、同形状的数值有效标记列',path+'/fields/'+key)
