"""Independent frozen numerical reference; never passed to the planning agent."""
import math
from .science.dbsrc_v1.topo_volt_current_cal.DBSRC_EPS import DBSRC_CurrentModel_EPS
from .science.dbsrc_v1.topo_volt_current_cal.DBSRC_EPS_max import batch_calculate_key_params


def reference(inputs):
    if not all(inputs[k]['valid'] for k in ('transformer','inductor','switch','capacitor')):
        raise ValueError('给定器件标记无效，不能生成有效候选')
    d=inputs['design'];p=inputs['point']
    model=DBSRC_CurrentModel_EPS(d['inductance'],d['capacitance'],d['frequency'],d['n'])
    rows=batch_calculate_key_params(model,p['Vin'],p['Vout'],model.calculate_D2(p['Vin'],p['Vout'],p['Pout']))
    if not rows:raise ValueError('单工况无有效 EPS 调制候选')
    packed=[];losses=[]
    for row in rows:
        v=row['key_params']
        if v['fs']<=0 or v['capacitance1']<=0 or v['i_rms_c1']<0:
            raise ValueError('候选频率、电容或 RMS 电流非法')
        loss=v['i_rms_c1']**2*.001/(2*math.pi*v['fs']*v['capacitance1'])
        packed.append({'valid':1.,'D1':row['D1'],'D2':row['D2'],'EPS':float(row['EPS']),**v})
        losses.append({'valid':1.,'cap1_loss':loss,'p_cap_total':loss})
    return {'candidates':{k:[float(row[k]) for row in packed] for k in packed[0]},
            'loss':{k:[float(row[k]) for row in losses] for k in losses[0]}}
