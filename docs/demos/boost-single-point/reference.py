"""Independent demonstration reference, not a platform tool or generated candidate.
Run from any directory with Python 3.11+; stdlib only.
"""
import copy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def calculate(inputs):
    o, p, s, t, h, limits = (inputs[k] for k in ('operating', 'passive', 'semiconductor', 'timing', 'thermal', 'limits'))
    vin, vout, power = o['Vin'], o['Vout'], o['Pout']
    if not (vout > vin > 0 and power > 0 and p['fs'] > 0 and p['L'] > 0 and p['C'] > 0):
        raise ValueError('invalid operating point')
    d = 1 - vin / vout
    io, il = power / vout, power / vin
    di = vin * d / (p['L'] * p['fs'])
    peak, valley = il + di / 2, il - di / 2
    if valley <= 0:
        raise ValueError('model not applicable: non-CCM')
    mean_square = il ** 2 + di ** 2 / 12
    il2, sw2, diode2 = mean_square, d * mean_square, (1 - d) * mean_square
    cap2 = diode2 - io ** 2
    if cap2 < 0:
        raise ValueError('negative capacitor RMS square')
    # Separate waveform-based check: Simpson integrates each squared linear
    # waveform exactly, including capacitor discharge and diode-off intervals.
    def average_square(a, b):
        return (a*a + 4*((a+b)/2)**2 + b*b) / 6
    on_square = average_square(valley, peak)
    off_square = average_square(peak, valley)
    wave_checks = {
        'IL_rms_squared': (il2, d*on_square + (1-d)*off_square),
        'Isw_rms_squared': (sw2, d*on_square),
        'Id_rms_squared': (diode2, (1-d)*off_square),
        'Ic_rms_squared': (cap2, d*io**2 + (1-d)*average_square(peak-io, valley-io)),
    }
    for name, (analytic, waveform) in wave_checks.items():
        assert math.isclose(analytic, waveform, rel_tol=1e-12, abs_tol=1e-12), name
    assert math.isclose((1-d)*il, io, rel_tol=1e-12)
    sw_cond = sw2 * s['R_on']
    diode = s['Vf'] * io + s['r_d'] * diode2
    copper = il2 * p['R_L']
    esr = cap2 * p['ESR']
    switching = .5 * vout * (valley*t['tr'] + peak*t['tf']) * p['fs']
    ripple_cap = io * d / (p['C'] * p['fs'])
    ripple_esr = peak * p['ESR']
    ripple = ripple_cap + ripple_esr
    total = sw_cond + diode + copper + esr + switching
    efficiency = power / (power + total)
    tj_sw = h['Ta'] + (sw_cond + switching) * h['Rth_sw']
    tj_d = h['Ta'] + diode * h['Rth_d']
    margin_sw, margin_d = limits['Vsw_rated']/vout, limits['Vd_rated']/vout
    flags = {
        'peak_current_pass': int(peak <= limits['Ipk_max']),
        'ripple_pass': int(ripple <= limits['delta_v_max']),
        'switch_temperature_pass': int(tj_sw <= limits['Tj_max']),
        'diode_temperature_pass': int(tj_d <= limits['Tj_max']),
        'efficiency_pass': int(efficiency >= limits['eta_min']),
        'switch_voltage_pass': int(margin_sw >= limits['voltage_margin_min']),
        'diode_voltage_pass': int(margin_d >= limits['voltage_margin_min']),
    }
    return {
        'operating_point': {'D':d, 'Iout':io, 'IL_avg':il},
        'inductor_current': {'delta_i':di, 'Ipk':peak, 'Imin':valley},
        'currents': {'IL_rms':math.sqrt(il2), 'Isw_rms':math.sqrt(sw2), 'Id_rms':math.sqrt(diode2), 'Ic_rms':math.sqrt(cap2), 'Id_avg':io},
        'mosfet_conduction': {'P_sw_cond':sw_cond},
        'diode_conduction': {'P_diode':diode},
        'inductor_copper': {'P_L_cu':copper},
        'capacitor_esr': {'P_C_esr':esr},
        'mosfet_switching': {'P_sw_switch':switching},
        'ripple': {'delta_v_cap':ripple_cap, 'delta_v_esr':ripple_esr, 'delta_v_bound':ripple},
        'loss_summary': {'P_total':total, 'eta':efficiency},
        'mosfet_temperature': {'Tj_sw':tj_sw},
        'diode_temperature': {'Tj_d':tj_d},
        'voltage_stress': {'Vsw_block':vout, 'Vd_reverse':vout, 'margin_sw':margin_sw, 'margin_d':margin_d},
        'evaluation': {**flags, 'overall_pass':int(all(flags.values())), 'failed_reasons':'; '.join(k for k,v in flags.items() if not v)},
    }

def main():
    baseline = {
        'operating': {'Vin':48, 'Vout':96, 'Pout':480},
        'passive': {'fs':100000, 'L':0.0001, 'C':0.00047, 'ESR':0.02, 'R_L':0.03},
        'semiconductor': {'R_on':0.015, 'Vf':0.7, 'r_d':0.01},
        'timing': {'tr':0.00000002, 'tf':0.00000003},
        'thermal': {'Ta':40, 'Rth_sw':4, 'Rth_d':6},
        'limits': {'Ipk_max':15, 'delta_v_max':0.3, 'Tj_max':100, 'eta_min':0.95, 'Vsw_rated':150, 'Vd_rated':150, 'voltage_margin_min':1.2},
    }
    second = copy.deepcopy(baseline)
    second['operating'] = {'Vin':60, 'Vout':120, 'Pout':600}
    second['passive'] = {'fs':80000, 'L':0.00015, 'C':0.00033, 'ESR':0.015, 'R_L':0.04}
    second['semiconductor'] = {'R_on':0.02, 'Vf':0.8, 'r_d':0.008}
    second['timing'] = {'tr':0.000000025, 'tf':0.000000035}
    second['thermal'] = {'Ta':35, 'Rth_sw':3, 'Rth_d':4}
    second['limits']['Vsw_rated'] = second['limits']['Vd_rated'] = 200
    changed = copy.deepcopy(baseline)
    changed['passive']['ESR'] = 0.04
    cases=[]
    for label,inputs in [('A_baseline_pass',baseline),('B_independent_point_pass',second),('C_ESR_double_constraint_fail',changed)]:
        expected=calculate(inputs)
        cases.append({'name':label, 'inputs':inputs, 'expected_by_step':expected, 'numeric_comparison':{'relative_tolerance':1e-8,'absolute_tolerance':1e-10}})
        print(label, json.dumps({**expected['loss_summary'],**expected['ripple'],**expected['evaluation']},ensure_ascii=False))
    assert cases[0]['expected_by_step']['evaluation']['overall_pass']==1
    assert cases[1]['expected_by_step']['evaluation']['overall_pass']==1
    assert cases[2]['expected_by_step']['evaluation']['overall_pass']==0
    assert cases[2]['expected_by_step']['evaluation']['failed_reasons']=='ripple_pass'
    for name,change in [('D_non_CCM',{'Pout':20}),('E_invalid_voltage',{'Vin':96})]:
        bad=copy.deepcopy(baseline)
        bad['operating'].update(change)
        try: calculate(bad)
        except ValueError as error:
            cases.append({'name':name,'inputs':bad,'expected_behavior':'stop_with_clear_error','reason':str(error)})
        else: raise AssertionError(name+' should stop')
    (ROOT/'02-运行输入-A.json').write_text(json.dumps(baseline,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'03-独立核算样例.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('5 scenarios checked; RMS formulas independently checked against piecewise waveform integrals.')

if __name__ == '__main__':
    main()
