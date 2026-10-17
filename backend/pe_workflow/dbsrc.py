"""Audited, pinned DBSRC primitives. No user-selected imports or execution.

All numerical algorithms below use the frozen WDY source. Invalid candidates
carry valid=0; their finite padding is explicitly inert, never a valid result.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from .science.dbsrc_v1.topo_var_sel_demon.DBSRC_var_sel_denorm import build_dbsrc_action_preprocessor
from .science.dbsrc_v1.topo_volt_current_cal.DBSRC_EPS import DBSRC_CurrentModel_EPS
from .science.dbsrc_v1.topo_volt_current_cal.DBSRC_EPS_max import batch_calculate_key_params, summarize_packed_key_params, summarize_across_workpoints_simple
from .science.dbsrc_v1.key_component_sel.transformer_sel import TransformerSelector
from .science.dbsrc_v1.key_component_sel.inductor_sel import InductorSelector
from .science.dbsrc_v1.key_component_sel.IGBT_sel import select_switch
from .science.dbsrc_v1.key_component_sel.capacitor_sel import select_resonant_caps_from_dict
from .science.dbsrc_v1.key_component_sel.capacitor_filter_sel import select_capacitance_with_metrics
from .science.dbsrc_v1.component_loss_cal.switch_loss_cal.formula.IGBT_loss_cal import calc_switch_loss
from .science.dbsrc_v1.component_loss_cal.transformer_loss_cal.formula.transformer_loss_cal import TransformerLoss
from .science.dbsrc_v1.component_loss_cal.inductor_loss_cal.formula.inductor_loss_cal import InductorLoss
from .science.dbsrc_v1.component_loss_cal.capacitor_loss_cal.capacitor_loss_cal import calc_Cr_loss
from .science.dbsrc_v1.constraint_check.switch_check import SwitchLossConstraintChecker
from .science.dbsrc_v1.constraint_check.transformer_check import TransformerConstraintChecker
from .science.dbsrc_v1.constraint_check.inductor_check import InductorConstraintChecker
from .science.dbsrc_v1.constraint_check.capacitor_check import CapacitorLossConstraintChecker
from .science.dbsrc_v1.performance_cal.ConverterMetricsCalculator import ConverterMetricsCalculator
from .science.dbsrc_v1.reward_cal.ConverterRewardConfig import build_converter_reward_calculator

ROOT=Path(__file__).resolve().parent


def flatten(value, prefix=''):
    result={}
    for key,item in value.items():
        name=f'{prefix}__{key}' if prefix else str(key)
        if isinstance(item,dict):result.update(flatten(item,name))
        elif item is not None:result[name]=item
    return result


def unflatten(value):
    result={}
    for name,item in value.items():
        if name in ('valid','reason'):continue
        target=result; parts=name.split('__')
        for part in parts[:-1]:target=target.setdefault(part,{})
        target[parts[-1]]=item
    return result


def packed(model,point):
    return batch_calculate_key_params(model,point['Vin'],point['Vout'],model.calculate_D2(point['Vin'],point['Vout'],point['Pout'])) or []


def current_model(design):
    return DBSRC_CurrentModel_EPS(design['inductance'],design['capacitance'],design['frequency'],design['n'])


def action(inputs):
    return {'design':build_dbsrc_action_preprocessor({}).transform(inputs['action']['values'])}


def sizing(inputs):
    design=inputs['design']; cfg=inputs['settings']; summaries=[]; cin=cout=0.
    for voltage in np.arange(200,501,50):
        power=min(cfg['Pout'],voltage*cfg['I_limit'])
        candidates=packed(current_model(design),{'Vin':cfg['Vin'],'Vout':voltage,'Pout':power})
        if not candidates:return {'electrical':{'valid':0.,'reason':'no sizing modulation solution'}}
        summary=summarize_packed_key_params(candidates)
        cin=max(cin,abs(summary['i_rms_p']-power/cfg['Vin'])*(.5/design['frequency'])/(cfg['ripple_limit']/100*cfg['Vin']))
        cout=max(cout,abs(design['n']*summary['i_rms_p']-power/voltage)*(.5/design['frequency'])/(cfg['ripple_limit']/100*voltage))
        summaries.append(summary)
    return {'electrical':{'valid':1.,'reason':'',**summarize_across_workpoints_simple(summaries),'Cin':cin,'Cout':cout}}


def selection(inputs,kind):
    e=inputs['electrical']
    if not e['valid']:return {'component':{'valid':0.,'reason':e['reason']}}
    raw={k:v for k,v in e.items() if k not in ('valid','reason','Cin','Cout')}
    if kind=='transformer':value=TransformerSelector().select_transformer(raw,False)
    elif kind=='inductor':value=InductorSelector().select_inductor(raw,False)
    elif kind=='switch':value={'model':select_switch(raw['v_s_max'],raw['i_rms_s_max'])}
    elif kind=='filter_input':value=select_capacitance_with_metrics(e['Cin'])
    elif kind=='filter_output':value=select_capacitance_with_metrics(e['Cout'])
    else:
        value=select_resonant_caps_from_dict(raw)
        # Original ENV checks == {}, so None is accepted and metrics use 0.
        # Expose this legacy behavior instead of pretending a device was selected.
        return {'component':{'valid':float(value!={}), 'available':float(value is not None),
                             'reason':'legacy accepts None; no capacitor metrics' if value is None else '',
                             'volume':float((value or {}).get('volume',0)), 'cost':float((value or {}).get('cost',0))}}
    valid=bool(value) and not(kind=='switch' and value['model'] is None)
    return {'component':{'valid':float(valid),'reason':'' if valid else 'no component selection',**flatten(value or {})}}


def workpoints(inputs):
    cfg=inputs['settings']; rows=[]
    for vi,v in enumerate(range(200,501,50)):
        for pi,level in enumerate((1.,.75,.5,.25)):
            rows.append({'Vin':cfg['Vin'],'Vout':float(v),'Pout':min(cfg['Pout'],v*cfg['I_limit'])*level,'vi':float(vi),'pi':float(pi)})
    return {'points':{k:[r[k] for r in rows] for k in rows[0]}}


def modulation(inputs):
    ready=all(inputs[k]['valid'] for k in ('transformer','inductor','switch','capacitor'))
    values=packed(current_model(inputs['design']),inputs['point']) if ready else []
    rows=[{'valid':1.,'D1':v['D1'],'D2':v['D2'],'EPS':float(v['EPS']),**v['key_params']} for v in values]
    if not rows:return {'candidates':{'valid':[0.]}}
    return {'candidates':{k:[r[k] for r in rows] for k in rows[0]}}


def loss(inputs,kind):
    c=inputs['candidate']; previous=inputs.get('previous',{'valid':1.})
    if not c['valid'] or not previous['valid']:return {'loss':{'valid':0.}}
    raw={k:v for k,v in c.items() if k not in ('valid','D1','D2','EPS')}; cfg=inputs['settings']
    if kind=='switch':
        value=calc_switch_loss(inputs['component']['model'],raw)
        valid=True
        # Only numerical loss fields are part of this computation interface.
        value={'summary':value['summary'],'per_switch':{k:{f:v for f,v in d.items() if f!='cond_mode'} for k,d in value['per_switch'].items()}}
    elif kind=='transformer':
        component=unflatten(inputs['component']); value=TransformerLoss().calculate_loss_B(raw,component)
        valid=True
    elif kind=='inductor':
        component=unflatten(inputs['component']); value=InductorLoss().calculate_loss_B(raw,component)
        valid=True
    else:
        value=calc_Cr_loss(raw); valid=True
    return {'loss':{'valid':float(valid),**flatten(value)}}


def constraint(inputs,kind):
    value=dict(inputs['loss'])
    if not value['valid']:return {'loss':value}
    raw=unflatten(value); cfg=inputs['settings']
    if kind=='switch':valid=SwitchLossConstraintChecker(cfg['p_s_limit']).verify_constraints(raw)
    elif kind=='transformer':valid=TransformerConstraintChecker(cfg['trans_loss_density']).verify_constraints(unflatten(inputs['component']),raw)
    elif kind=='inductor':valid=InductorConstraintChecker(cfg['inductor_loss_density']).verify_constraints(unflatten(inputs['component']),raw)
    else:valid=CapacitorLossConstraintChecker(cfg['p_cap_limit']).verify_constraints(raw)
    value['valid']=float(valid)
    return {'loss':value}


def candidate_efficiency(inputs):
    c=inputs['candidate']; s=inputs['switch_loss']; t=inputs['transformer_loss']; l=inputs['inductor_loss']; cap=inputs['capacitor_loss']
    if not all(x['valid'] for x in (c,s,t,l,cap)):return {'result':{'valid':0.}}
    total=s['summary__p_s_total']+t['p_transformer']+l['p_inductor_total']+cap['p_cap_total']
    detail=[[c[k] for k in ('i_on_s1','i_on_s4','i_on_s5','i_on_s8')],
            [c['i_rms_p'],c['D1'],c['D2'],c['EPS']],
            [total,s['summary__p_s_total'],t['p_transformer'],l['inductors__inductor1__p_inductor']]]
    for switch in ('s1','s4','s5','s8'):
        detail.append([s[f'per_switch__{switch}__{k}'] for k in ('p_total','p_cond','p_on','p_off')])
    detail.extend([[t[k] for k in ('p_transformer','p_cu_T','p_fe_T','B_T')],
                   [l['inductors__inductor1__'+k] for k in ('p_inductor','p_cu_L','p_fe_L','B_L')]])
    return {'result':{'valid':1.,'efficiency':inputs['point']['Pout']/(inputs['point']['Pout']+total),'detail':detail}}


def best(inputs):
    values=inputs['evaluations']; indices=np.flatnonzero(values['valid'])
    if not len(indices):return {'result':{'valid':0.,'efficiency':0.,'detail':np.zeros((9,4))}}
    i=int(indices[np.argmax(np.asarray(values['efficiency'])[indices])])
    return {'result':{'valid':1.,'efficiency':values['efficiency'][i],'detail':values['detail'][i]}}


WEIGHTS=np.array([[2,.3,.3,.3],[2,.3,.3,.3],[1.8,1.8,.3,.3],[1.8,1.8,.3,.3],[1.8,1.8,.3,.3],[1.8,1.8,.3,.3],[.3,1.2,.3,2]])


def metrics(inputs):
    points=inputs['evaluations']; efficiencies=np.asarray(points['efficiency']).reshape(7,4)
    details=np.asarray(points['detail']).reshape(7,4,9,4)
    env=SimpleNamespace(efficiency_matrix=efficiencies,filter_capacitance_Cin=unflatten(inputs['filter_input']),
                        filter_capacitance_Cout=unflatten(inputs['filter_output']),transformer_params_dict=unflatten(inputs['transformer']),
                        inductor_params_dict=unflatten(inputs['inductor']),capacitor_params_dict=None if not inputs['capacitor']['available'] else unflatten(inputs['capacitor']),switch=inputs['switch']['model'])
    value=ConverterMetricsCalculator(WEIGHTS).calculate(env)
    valid=all(points['valid']) and all(inputs[k]['valid'] for k in ('transformer','inductor','switch','capacitor'))
    return {'metrics':{'valid':float(valid),**{k:value[k] for k in ('efficiency','volume','cost')},'efficiency_matrix':efficiencies,'detail_matrix':details}}


def reward(inputs):
    m=inputs['metrics']; score=build_converter_reward_calculator({}).calculate(m)['reward'] if m['valid'] else -1.
    return {'result':{**m,'reward':score}}


OPERATIONS={'action':action,'sizing':sizing,'workpoints':workpoints,'modulation':modulation,'candidate_efficiency':candidate_efficiency,'best':best,'metrics':metrics,'reward':reward}
for _kind in ('transformer','inductor','switch','capacitor','filter_input','filter_output'):
    OPERATIONS['select_'+_kind]=lambda inputs,kind=_kind:selection(inputs,kind)
for _kind in ('switch','transformer','inductor','capacitor'):
    OPERATIONS['loss_'+_kind]=lambda inputs,kind=_kind:loss(inputs,kind)
    OPERATIONS['check_'+_kind]=lambda inputs,kind=_kind:constraint(inputs,kind)


@lru_cache(maxsize=1)
def runtime_hash():
    files=[Path(__file__),*sorted((ROOT/'science/dbsrc_v1').rglob('*.py'))]
    return hashlib.sha256(b''.join(p.relative_to(ROOT).as_posix().encode()+b'\0'+p.read_bytes() for p in files)).hexdigest()


@lru_cache(maxsize=1)
def registry():
    from .models import Module
    document=json.loads((ROOT/'science/dbsrc_modules.json').read_text(encoding='utf-8'))
    return {m['builtin_id']:Module.model_validate(m) for m in document}


def check_builtin(module):
    expected=registry().get(module.builtin_id)
    if expected is None or expected!=module or module.builtin_hash!=runtime_hash():
        raise ValueError('内置模块声明或冻结科学代码不匹配，不能执行或回放；必须发布新版本')


def execute_builtin(module,inputs):
    check_builtin(module)
    value=OPERATIONS[module.builtin_id](inputs)
    # Rejected branches use documented inert padding to keep a fixed numerical
    # contract. Only platform primitives can create it; all downstream gates
    # must check valid, and final result carries valid=0/reward=-1.
    for name,port in module.outputs.items():
        bag=value[name]
        if not bag.get('valid',1) or ('valid' in bag and isinstance(bag['valid'],list) and bag['valid']==[0.]):
            for key,field in port.fields.items():
                if key in bag:continue
                if field.type=='string':bag[key]=''
                elif field.type=='array':bag[key]=np.zeros([s if isinstance(s,int) else 1 for s in field.value_type.shape])
                else:bag[key]=0.
    return value


def fixture():
    from .models import Workflow
    data=json.loads((ROOT/'science/dbsrc_workflow.json').read_text(encoding='utf-8'))
    return list(registry().values()),Workflow.model_validate(data['workflow']),data['inputs']
