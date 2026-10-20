"""LLC rehearsal reference. Standard library; never accesses the platform database.

Complex-impedance reference cross-checks the real-scalar recipe given to DSH.
This checks implementation consistency, not laboratory/model accuracy.
"""
import copy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def point(design, losses, limits, row):
    lr, lm, cr, n = (design[k] for k in ('Lr', 'Lm', 'Cr', 'n'))
    vin, load, fs = (row[k] for k in ('Vin', 'R_load', 'fs'))
    if not all(math.isfinite(v) and v > 0 for v in (lr, lm, cr, n, vin, load, fs)):
        raise ValueError('positive finite circuit parameters required')
    if any(not math.isfinite(v) or v < 0 for v in losses.values()):
        raise ValueError('loss resistances must be finite and nonnegative')
    w = 2 * math.pi * fs
    fr = 1 / (2 * math.pi * math.sqrt(lr * cr))
    rac = 8 * n * n * load / math.pi**2
    zp = 1 / (1 / rac + 1 / complex(0, w * lm))
    zin = complex(0, w * lr - 1 / (w * cr)) + zp
    v1 = math.sqrt(2) * vin / math.pi
    ir = v1 / zin
    vp = ir * zp
    im = vp / complex(0, w * lm)
    load_current = vp / rac
    gain = abs(vp) / v1
    vo = gain * vin / (2 * n)
    # Independent normalized closed-form gain and energy identities.
    fn, ln = fs / fr, lm / lr
    q = math.sqrt(lr / cr) / rac
    closed_gain = 1 / math.sqrt((1 + (1 - 1/fn**2)/ln)**2 + (q*(fn-1/fn))**2)
    assert math.isclose(gain, closed_gain, rel_tol=1e-11, abs_tol=1e-12)
    assert math.isclose(abs(vp)**2/rac, vo**2/load, rel_tol=1e-11)
    assert abs(ir-im-load_current) < 1e-10
    r, x = rac, w*lm
    pr, px = r*x*x/(r*r+x*x), r*r*x/(r*r+x*x)
    zr, zx = pr, w*lr-1/(w*cr)+px
    scalar_ir = v1/math.sqrt(zr*zr+zx*zx)
    assert math.isclose(abs(ir), scalar_ir, rel_tol=1e-11)
    p_lr = abs(ir)**2 * losses['R_Lr']
    p_cr = abs(ir)**2 * losses['ESR_Cr']
    p_sw = abs(ir)**2 * losses['R_on']
    checks = {
        'voltage': abs(vo-limits['V_target'])/limits['V_target'] <= limits['voltage_tolerance'],
        'current': abs(ir) <= limits['Ir_max'],
        'capacitor_voltage': math.sqrt(2)*abs(ir)/(w*cr) <= limits['Vcr_ac_peak_max'],
        'inductive': zin.imag > 0,
    }
    return {
        'row': row, 'tank': {'fr': fr, 'fn': fn, 'Ln': ln, 'Q': q, 'Rac': rac},
        'impedance': {'Rp': zp.real, 'Xp': zp.imag, 'Rin': zin.real, 'Xin': zin.imag},
        'transfer': {'V1_rms': v1, 'gain': gain, 'Vout': vo, 'Pout': vo*vo/load},
        'stress': {'Ir_rms': abs(ir), 'Im_rms': abs(im), 'Iload_primary_rms': abs(load_current),
                   'Vcr_ac_rms': abs(ir)/(w*cr), 'Vcr_ac_peak': math.sqrt(2)*abs(ir)/(w*cr)},
        'loss': {'P_Lr': p_lr, 'P_Cr': p_cr, 'P_bridge_cond': p_sw,
                 'P_selected': p_lr+p_cr+p_sw},
        'checks': {**{k: int(v) for k,v in checks.items()}, 'overall_pass': int(all(checks.values()))},
    }


def sweep(inputs):
    results=[]
    s=inputs['scan']
    for vin in s['vin_values']:
        for load in s['load_values']:
            for fs in s['frequency_values']:
                results.append(point(inputs['design'], inputs['losses'], inputs['limits'],
                                     {'Vin':vin, 'R_load':load, 'fs':fs}))
    summary=[]
    for vin in s['vin_values']:
        for load in s['load_values']:
            candidates=[r for r in results if r['row']['Vin']==vin and r['row']['R_load']==load and r['checks']['overall_pass']]
            # Rank only finite supplied grid points; never claim a continuous optimum.
            candidates.sort(key=lambda r:(abs(r['transfer']['Vout']-inputs['limits']['V_target']),r['loss']['P_selected'],r['row']['fs']))
            summary.append({'Vin':vin,'R_load':load,'qualified_count':len(candidates),
                            'recommended':candidates[0]['row'] if candidates else None,
                            'status':'grid_candidate' if candidates else 'no_qualified_grid_point'})
    return {'point_count':len(results),'summary':summary,'results':results}


def main():
    a={'design':{'Lr':20e-6,'Lm':100e-6,'Cr':1/((2*math.pi*100000)**2*20e-6),'n':4},
       'losses':{'R_Lr':0.08,'ESR_Cr':0.03,'R_on':0.12},
       'limits':{'V_target':50,'voltage_tolerance':0.05,'Ir_max':10,'Vcr_ac_peak_max':500},
       'scan':{'vin_values':[360,400,440],'load_values':[8,16],
               'frequency_values':list(range(60000,160001,5000))}}
    baseline=sweep(a)
    tight=copy.deepcopy(a); tight['limits']['Ir_max']=0.1
    tight_result=sweep(tight)
    assert baseline['point_count']==126
    assert all(r['qualified_count']==0 for r in tight_result['summary'])
    resonance=point(a['design'],a['losses'],a['limits'],{'Vin':400,'R_load':8,'fs':100000})
    assert math.isclose(resonance['transfer']['gain'],1,abs_tol=1e-12)
    assert math.isclose(resonance['transfer']['Vout'],50,abs_tol=1e-12)
    invalid=copy.deepcopy(a); invalid['design']['Cr']=0
    try: sweep(invalid)
    except ValueError: pass
    else: raise AssertionError('invalid capacitance accepted')
    for filename,data in [('02-运行输入-A.json',a),('03-独立核算样例.json',{
        'notice':'参考数据，不是平台注册/导入格式；不构成实物验证',
        'resonance_anchor':resonance,'baseline':baseline,
        'tight_current_limit':{'change':{'Ir_max':0.1},'summary':tight_result['summary']},
        'invalid_case':{'change':{'Cr':0},'expected':'停止，电容量必须为正'}})]:
        (ROOT/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'point_count':baseline['point_count'],'summary':baseline['summary'],
                      'anchor':resonance},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
