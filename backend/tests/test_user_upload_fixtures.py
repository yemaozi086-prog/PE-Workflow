"""Native function fixtures only: no reference adapter, no DSH calls or registration."""
from pathlib import Path
import pytest
from pe_workflow.expressions import analyze_source,run_function

ROOT=Path(__file__).resolve().parents[2]
PACK=ROOT/'examples/user-upload-tests'

@pytest.mark.parametrize('name',['A_clear_interface.py','B_pending_output_unit.py'])
def test_single_function_supported(name):
    report=analyze_source((PACK/name).read_text(encoding='utf-8'))
    assert report['supported'],report
    assert not report['explicit_transforms_required']

@pytest.mark.parametrize('frequency,current,capacitance',[(70000,10,2e-7),(25000,25,1e-7),(180000,17,4.5e-7)])
def test_native_functions_preserve_reference_formula(frequency,current,capacitance):
    original=(ROOT/'examples/real-capacitor/baseline_single_group.py').read_text(encoding='utf-8')
    expected=run_function(original,'capacitor_group_loss',{'fs_hz':frequency,'i_rms_a':current,'capacitance_f':capacitance,'tan_delta':.001})['p_cap_total']
    a=run_function((PACK/'A_clear_interface.py').read_text(encoding='utf-8'),'calc_capacitor_alt',{'excitation':{'switching':{'F_kHz':frequency/1000},'Ieff_mA':current*1000},'capacitor':{'capacity_nF':capacitance*1e9,'loss_tangent':.001}})
    assert a[0]['group_mW']==pytest.approx(expected*1000)
    assert a[1]==pytest.approx(expected)
    b=run_function((PACK/'B_pending_output_unit.py').read_text(encoding='utf-8'),'calc_capacitor_pending',{'drive':{'F_kHz':frequency/1000,'Ieff_mA':current*1000},'device':{'capacity_nF':capacitance*1e9,'loss_tangent':.001}})
    assert b['raw_group']==pytest.approx(expected*1000)
    assert b['raw_total']==pytest.approx(expected*1000)
