"""Hand-verifiable numerical interface demonstration, not a calibrated device model."""
from .models import Module,Workflow


def fixture():
    t={'kind':'bare','value_type':{'dtype':'float64','shape':['N']},'axes':[{'name':'time','monotonic':True}], 'quantity':'time','unit':'s'}
    current={'kind':'bare','value_type':{'dtype':'float64','shape':['N']},'axes':[{'name':'time','coordinate_ref':'t','monotonic':True}], 'quantity':'current','characteristic':'instantaneous','unit':'A'}
    energy={'type':'number','quantity':'energy','unit':'J'}
    module=Module.model_validate({'id':'waveform_energy','version':1,'contract_version':'2.0','name':'波形能量积分 · SciPy',
        'description':'输入电流数组；原函数使用毫安、毫秒、嵌套字典和元组返回，经声明适配为外部接口。',
        'inputs':{'t':t,'i':current,'voltage':{'type':'number','quantity':'voltage','unit':'V'}},'outputs':{'energy':energy},
        'implementation':'python_function','entrypoint':'integrate_energy','provenance':'平台手算样例：恒定电压与线性电流，不代表器件标定',
        'capabilities':['waveform_energy_integral'],'applies_to':{'voltage_model':['constant']},
        'source':'import numpy as np\nfrom scipy.integrate import trapezoid\ndef integrate_energy(samples, bus):\n    seconds = samples["clock_ms"] / 1000\n    amps = samples["trace"]["milliamps"] / 1000\n    joules = bus * trapezoid(amps, x=seconds)\n    return ({"energy_mJ": joules * 1000},)\n',
        'adapter':{'arguments':{'samples':{'assemble':{'clock_ms':{'from':['inputs','t'],'conversion':{'from_unit':'s','to_unit':'ms'}},'trace':{'assemble':{'milliamps':{'from':['inputs','i'],'conversion':{'from_unit':'A','to_unit':'mA'}}}}}},'bus':{'from':['inputs','voltage']}},
        'returns':{'energy':{'from':['return',0,'energy_mJ'],'conversion':{'from_unit':'mJ','to_unit':'J'}}}}})
    wf=Workflow.model_validate({'schema_version':'2.0','id':'waveform_demo','name':'波形能量 · 函数适配演示','description':'手算假设：10 V 恒压，0–2 A 线性电流，持续 2 ms，积分为 0.02 J。',
        'inputs':{k:p.model_dump() for k,p in module.inputs.items()},'nodes':[{'id':'integrate','kind':'compute','module_id':module.id,'module_version':1,'inputs':{k:{'source':'input','port':k} for k in module.inputs}}],
        'outputs':{'energy':{'source':'node','node':'integrate','port':'energy'}},'output_ports':{'energy':energy},'layout':{'$input':{'x':0,'y':60},'integrate':{'x':330,'y':80},'$output':{'x':660,'y':80}}})
    return module,wf,{'t':[0,.001,.002],'i':[0,1,2],'voltage':10}
