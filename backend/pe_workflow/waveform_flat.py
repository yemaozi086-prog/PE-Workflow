"""Version 3 flat-dictionary waveform baseline; old v2 fixture stays immutable."""
from .models import Module,Workflow


def fixture():
    clock={'type':'array','quantity':'time','unit':'s','value_type':{'shape':['N']},
           'axes':[{'name':'time','coordinate_ref':'time','monotonic':True}]}
    current={'type':'array','quantity':'current','unit':'A','characteristic':'instantaneous',
             'value_type':{'shape':['N']},'axes':[{'name':'time','coordinate_ref':'time','monotonic':True}]}
    inputs={'waveform':{'type':'flat_dict','fields':{'time':clock,'current':current}},
            'electrical':{'type':'flat_dict','fields':{'voltage':{'type':'number','quantity':'voltage','unit':'V'}}}}
    outputs={'result':{'type':'flat_dict','fields':{'energy':{'type':'number','quantity':'energy','unit':'J'}}}}
    module=Module.model_validate({'id':'waveform_energy_flat','version':1,'contract_version':'3.0',
        'name':'波形能量积分 · 扁平字典','description':'恒压与分段线性电流的能量积分；共享时间坐标，不隐式重采样。',
        'inputs':inputs,'outputs':outputs,'implementation':'python_function','entrypoint':'waveform_energy',
        'source':'from scipy.integrate import trapezoid\ndef waveform_energy(waveform, electrical):\n    energy = electrical["voltage"] * trapezoid(waveform["current"], x=waveform["time"])\n    return {"energy": energy}\n',
        'adapter':{'arguments':{'waveform':{'from':['inputs','waveform']},'electrical':{'from':['inputs','electrical']}},
                   'returns':{'result':{'from':['return']}}},
        'capabilities':['waveform_energy_integral'],'applies_to':{'voltage_model':['constant'],'sampling':['shared_time_coordinate']},
        'provenance':'平台独立手算：E=V×电流时间曲线面积；待课题组核对，不代表器件标定'})
    workflow=Workflow.model_validate({'schema_version':'3.0','id':'waveform_flat_demo','name':'波形能量 · 扁平字典基准',
        'inputs':inputs,'output_ports':outputs,'nodes':[{'id':'integrate','module_id':module.id,'module_version':1,
        'inputs':{k:{'source':'input','port':k} for k in inputs}}],
        'outputs':{'result':{'source':'node','node':'integrate','port':'result'}},
        'layout':{'$input':{'x':0,'y':60},'integrate':{'x':350,'y':60},'$output':{'x':700,'y':60}}})
    return module,workflow,{'waveform':{'time':[0.,.001,.002],'current':[0.,1.,2.]},'electrical':{'voltage':10.}}
