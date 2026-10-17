"""Task evidence only. The new implementation must be written by DSH."""
import json
import math
from pathlib import Path
from .dbsrc import fixture,OPERATIONS


def recipe():
    modules,wf,inputs=fixture()
    reference=json.loads((Path(__file__).parent/'science/dbsrc_thermal_reference.json').read_text(encoding='utf-8'))
    design=OPERATIONS['action']({'action':inputs['action']})['design']
    from .dbsrc import packed,current_model
    first=packed(current_model(design),{'Vin':390.,'Vout':200.,'Pout':4400.})[0]
    candidate={'valid':1.,'D1':float(first['D1']),'D2':float(first['D2']),'EPS':float(first['EPS']),**{k:float(v) for k,v in first['key_params'].items()}}
    candidate.update(fs=100000.,capacitance1=200e-9,i_rms_c1=10.)
    def thermal(t):return {'temperature':t,'reference_temperature':25.,'alpha':.002}
    anchors=[]
    for temperature,expected in ((25.,.7957747154594768),(75.,.8753521870054245),(125.,.9549296585513721)):
        sample={'candidate':candidate,'previous':{'valid':1.},'settings':inputs['settings'],'thermal':thermal(temperature)}
        anchors.append({'inputs':sample,'expected':{'loss':{'valid':1.,'cap1_loss':expected,'p_cap_total':expected}},'tolerance':1e-9})
    for guard in ('candidate','previous'):
        sample=json.loads(json.dumps(anchors[1]['inputs']));sample[guard]['valid']=0.
        anchors.append({'inputs':sample,'expected':{'loss':{'valid':0.,'cap1_loss':0.,'p_cap_total':0.}},'tolerance':1e-9})
    root=Path(__file__).resolve().parents[2]
    source=(root/'examples/dbsrc/alternate_capacitor.py').read_text(encoding='utf-8')
    thermal_port={'type':'flat_dict','fields':{
        'temperature':{'type':'number','quantity':'temperature','unit':'degC','semantic_id':'capacitor.temperature'},
        'reference_temperature':{'type':'number','quantity':'temperature','unit':'degC','semantic_id':'capacitor.reference_temperature'},
        'alpha':{'type':'number','quantity':'temperature_coefficient','unit':'1/degC','unit_definition':{'dimension':'temperature_coefficient','scale':1},'semantic_id':'capacitor.loss_tangent_temperature_coefficient'}}}
    return {'mode':'model','source_policy':'modify','source':source,'acceptance_label':reference['label'],
        'instruction':'派生同模块族新版本，仅修改所选 DBSRC 单组谐振电容介质损耗公式。新增独立 thermal 扁平字典，P=I_rms²*0.001/(2*pi*fs*capacitance1)*(1+alpha*(temperature-reference_temperature))。保留 candidate/previous 有效性门控和全部原有输入输出；其余模块、独立约束与遍历逻辑不变。新源码由你生成，不使用内置执行权限。25/75/125℃均须通过完整工况验证。',
        'facts':json.dumps({'formula':reference['formula'],'thermal_port':thermal_port,'units':'fs Hz, capacitance1 F, i_rms_c1 A, output W; capacitor material temperature, not semiconductor junction temperature. alpha is per temperature difference in degrees Celsius.','guards':'candidate.valid==0 or previous.valid==0 => loss.valid=0 and inert zero padding. Otherwise valid=1 before downstream independent constraint.','assumption':reference['label'],'base_function_role':'Uploaded function is an interface-only baseline; explicit source_policy=modify authorizes a new temperature-dependent implementation.'},ensure_ascii=False),
        'anchors':anchors,'added_bindings':{'thermal':{'source':'constant','value':thermal(75.)}},'added_input_ports':{'thermal':thermal_port},
        'evaluation_cases':[{'name':f"{int(c['temperature'])}℃完整计算",'added_bindings':{'thermal':{'source':'constant','value':thermal(c['temperature'])}},'expected':c['expected'],'tolerance':1e-9} for c in reference['cases']],
        'node_path':['workpoints','body','modulations','body','loss_capacitor']}
