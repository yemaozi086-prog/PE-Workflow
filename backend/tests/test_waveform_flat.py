from pathlib import Path
import copy
import numpy as np
import pytest
from pe_workflow.waveform_flat import fixture
from pe_workflow.engine import check_module,execute,validate_workflow,WorkflowError
from pe_workflow.expressions import analyze_source,run_function
from pe_workflow.evaluation import grade

ROOT=Path(__file__).resolve().parents[2]
SAMPLES=[([0.,.001,.002],[0.,1.,2.],10.,.02),
         ([0.,.0005,.002],[3.,3.,3.],12.,.072),
         ([0.,.001,.004],[0.,2.,0.],5.,.02)]


@pytest.mark.parametrize('t,i,v,expected',SAMPLES)
def test_flat_baseline_and_native_variants_match_hand_area(t,i,v,expected):
    module,wf,_=fixture();check_module(module)
    inputs={'waveform':{'time':t,'current':i},'electrical':{'voltage':v}}
    assert execute(wf,{f'{module.id}@1':module},inputs)['result']['energy']==pytest.approx(expected)
    a=(ROOT/'examples/array-evaluation/A_nested_ms_ma.py').read_text(encoding='utf-8')
    b=(ROOT/'examples/array-evaluation/B_arguments_us_mv.py').read_text(encoding='utf-8')
    av=run_function(a,'integrate_uploaded',{'packet':{'clock_ms':np.array(t)*1000,'channel':{'current_mA':np.array(i)*1000}},'bus':v})
    bv=run_function(b,'calculate_energy',{'clock_us':np.array(t)*1e6,'trace_amperes':np.array(i),'dc_millivolts':v*1000})
    assert av[0]['energy_mJ']/1000==pytest.approx(expected)
    assert bv['measurement']['joules']==pytest.approx(expected)


@pytest.mark.parametrize('field,value,code',[('current',[0.,1.],'SHARED_DIMENSION'),('time',[0.,.002,.001],'COORDINATE_ORDER'),('time',[0.,.001,.001],'COORDINATE_ORDER')])
def test_dictionary_array_shape_and_time_constraints(field,value,code):
    m,wf,inputs=fixture();inputs['waveform'][field]=value
    report=validate_workflow(wf,{f'{m.id}@1':m},inputs)
    assert not report['valid'] and report['diagnostics'][0]['code']==code


def test_resampling_source_cannot_hide_as_adapter_without_evidence():
    m,_,_=fixture();data=m.model_dump()
    data['source']=(ROOT/'examples/array-evaluation/C_resampling_gap.py').read_text(encoding='utf-8')
    data['entrypoint']='energy_on_new_grid'
    time={'from':['inputs','waveform','time'],'conversion':{'from_unit':'s','to_unit':'ms'}}
    data['adapter']={'arguments':{'record':{'assemble':{'clock_ms':time,'current_mA':{'from':['inputs','waveform','current'],'conversion':{'from_unit':'A','to_unit':'mA'}}}},
                                  'dc_voltage':{'from':['inputs','electrical','voltage']},'new_grid_ms':time},
                     'returns':{'result':{'assemble':{'energy':{'from':['return','energy_mJ'],'conversion':{'from_unit':'mJ','to_unit':'J'}}}}}}
    assert analyze_source(data['source'])['explicit_transforms_required']==['resample']
    from pe_workflow.models import Module
    with pytest.raises(WorkflowError) as error:check_module(Module.model_validate(data))
    assert error.value.diagnostic.code=='UNDECLARED_TRANSFORM'


def test_transform_guard_grade_requires_reason_and_no_publication():
    case={'kind':'transform_gap'}
    outcome={'status':'blocked','generation_count':1,'explanation':'重采样的目标网格和验证依据未明确',
             'published':False,'module_keys_unchanged':True}
    assert grade(case,outcome)['passed']
    for extra in ({'published':True},{'explanation':'不支持'},{'status':'applied'}):
        bad=copy.deepcopy(outcome);bad.update(extra);assert not grade(case,bad)['passed']
