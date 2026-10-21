import copy
import pytest
from pe_workflow.models import Module,Port,Workflow,Node,Binding
from pe_workflow.dependency_audit import audit_dependencies
from pe_workflow.engine import execute


def field(quantity,unit,required=True):
    return Port(type='number',quantity=quantity,unit=unit,required=required)


def buck():
    return Module(id='buck_ripple_test',version=1,name='Buck CCM 容性纹波',contract_version='3.0',
        inputs={'design':Port(type='flat_dict',fields={'C':field('capacitance','F'),'L':field('inductance','H',False)}),
                'operating':Port(type='flat_dict',fields={'fs':field('frequency','Hz'),'Vin':field('voltage','V',False),'Vout':field('voltage','V',False)}),
                'ripple':Port(type='flat_dict',fields={'delta_i':field('current_ripple','A'),'D':field('duty_cycle','1',False)})},
        outputs={'result':Port(type='flat_dict',fields={'delta_v':field('voltage_ripple','V')})},
        implementation='python_subset',source='def compute(inputs):\n    C = inputs["design"]["C"]\n    fs = inputs["operating"]["fs"]\n    delta_i = inputs["ripple"]["delta_i"]\n    return {"result": {"delta_v": delta_i / (8 * fs * C)}}\n',provenance='独立手算教学模型，非器件验证')


def test_buck_minimum_fields_and_complete_chain():
    m=buck();report=audit_dependencies(m)
    assert report['status']=='passed'
    assert report['required_fields']==['design.C','operating.fs','ripple.delta_i']
    w=Workflow(schema_version='3.0',id='buck_test',name='Buck test',inputs=m.inputs,
        nodes=[Node(id='ripple',module_id=m.id,module_version=1,inputs={k:Binding(source='input',port=k) for k in m.inputs})],
        outputs={'result':Binding(source='node',node='ripple',port='result')},output_ports=m.outputs)
    minimal={'design':{'C':0.0001},'operating':{'fs':100000},'ripple':{'delta_i':0.6}}
    assert execute(w,{m.id+'@1':m},minimal)['result']['delta_v']==pytest.approx(0.0075)
    full={'design':{'C':0.0001,'L':0.0001},'operating':{'fs':100000,'Vin':24,'Vout':12},'ripple':{'D':0.5,'delta_i':0.6}}
    assert execute(w,{m.id+'@1':m},full)==execute(w,{m.id+'@1':m},minimal)


def test_extra_required_field_detected_without_execution():
    m=buck();m.inputs['design'].fields['L'].required=True
    report=audit_dependencies(m)
    assert report['status']=='passed' and report['unused_required_fields']==['design.L']
    assert report['warnings'][0]['code']=='UNUSED_REQUIRED_INPUT'


def test_undeclared_field_read_detected():
    m=buck();m.source=m.source.replace('8 * fs * C','8 * fs * C * inputs["design"]["factor"]')
    assert audit_dependencies(m)['undeclared_fields']==['design.factor']


def test_optional_read_without_guard_detected():
    m=buck();m.source=m.source.replace('8 * fs * C','8 * fs * C * inputs["design"]["L"]')
    assert audit_dependencies(m)['unsafe_optional_fields']==['design.L']


def test_membership_guard_and_alias_are_resolved():
    m=buck();m.source=m.source.replace('    C =', '    design = inputs["design"]\n    if "L" in design:\n        check = design["L"]\n    C =')
    report=audit_dependencies(m)
    assert report['status']=='passed' and report['guarded_fields']==['design.L']


def test_dynamic_key_is_uncertain_not_approved():
    m=buck();m.source=m.source.replace('    C = inputs["design"]["C"]','    key = "C"\n    C = inputs["design"][key]')
    assert audit_dependencies(m)['status']=='uncertain'


def test_eager_optional_adapter_is_not_mistaken_for_guarded_read():
    m=buck();m.implementation='python_function';m.entrypoint='calc'
    m.source='def calc(C, fs, delta_i, L=1):\n    return {"delta_v": delta_i / (8 * fs * C)}'
    from pe_workflow.models import Adapter
    m.adapter=Adapter.model_validate({'arguments':{'C':{'from':['inputs','design','C']},'fs':{'from':['inputs','operating','fs']},'delta_i':{'from':['inputs','ripple','delta_i']},'L':{'from':['inputs','design','L']}},'returns':{'result':{'from':['return']}}})
    assert audit_dependencies(m)['unsafe_optional_fields']==['design.L']


def test_safe_scalar_function_adapter_resolves_to_external_fields():
    m=buck();m.implementation='python_function';m.entrypoint='calc'
    m.source='def calc(C, fs, delta_i):\n    return {"delta_v": delta_i / (8 * fs * C)}'
    from pe_workflow.models import Adapter
    m.adapter=Adapter.model_validate({'arguments':{'C':{'from':['inputs','design','C']},'fs':{'from':['inputs','operating','fs']},'delta_i':{'from':['inputs','ripple','delta_i']}},'returns':{'result':{'from':['return']}}})
    assert audit_dependencies(m)['status']=='passed'


def test_explicit_optional_default_is_safe_without_inventing_a_value():
    m=buck();m.inputs['design'].fields['L'].default=0.0001
    m.source=m.source.replace('8 * fs * C','8 * fs * C * inputs["design"]["L"]')
    assert audit_dependencies(m)['status']=='passed'


def test_two_step_buck_chain_has_real_dependency_and_expected_response():
    second=buck()
    first=Module(id='buck_current_test',version=1,name='Buck CCM 电感纹波',contract_version='3.0',
        inputs={'design':Port(type='flat_dict',fields={'L':field('inductance','H')}),
                'operating':Port(type='flat_dict',fields={'Vin':field('voltage','V'),'Vout':field('voltage','V'),'fs':field('frequency','Hz')})},
        outputs={'ripple':Port(type='flat_dict',fields={'D':field('duty_cycle','1'),'delta_i':field('current_ripple','A')})},
        implementation='python_subset',source='def compute(inputs):\n    Vin = inputs["operating"]["Vin"]\n    Vout = inputs["operating"]["Vout"]\n    fs = inputs["operating"]["fs"]\n    L = inputs["design"]["L"]\n    D = Vout / Vin\n    return {"ripple": {"D": D, "delta_i": (Vin - Vout) * D / (L * fs)}}',provenance='独立手算教学模型')
    assert audit_dependencies(first)['status']=='passed'
    external=copy.deepcopy(first.inputs);external['design'].fields['C']=field('capacitance','F')
    w=Workflow(schema_version='3.0',id='buck_two_step',name='Buck two-step',inputs=external,
        nodes=[Node(id='current',module_id=first.id,module_version=1,inputs={k:Binding(source='input',port=k) for k in first.inputs}),
               Node(id='voltage',module_id=second.id,module_version=1,inputs={'design':Binding(source='input',port='design'),'operating':Binding(source='input',port='operating'),'ripple':Binding(source='node',node='current',port='ripple')})],
        outputs={'result':Binding(source='node',node='voltage',port='result')},output_ports=second.outputs)
    registry={first.id+'@1':first,second.id+'@1':second}
    values={'design':{'L':0.0001,'C':0.0001},'operating':{'Vin':24,'Vout':12,'fs':100000}}
    assert execute(w,registry,values)['result']['delta_v']==pytest.approx(0.0075)
    values['design']['L']=0.0002
    assert execute(w,registry,values)['result']['delta_v']==pytest.approx(0.00375)


def test_subset_with_function_style_parameters_reports_protocol_not_unused_fields():
    from pe_workflow.engine import WorkflowError
    m=buck();m.source='def compute(design, operating, ripple):\n    return {"result": {"delta_v": ripple["delta_i"] / (8 * operating["fs"] * design["C"])}}'
    with pytest.raises(WorkflowError) as error:audit_dependencies(m)
    assert error.value.diagnostic.code=='MODULE_ENTRYPOINT_MISMATCH'
    assert 'compute(inputs)' in error.value.diagnostic.message


def test_same_dictionary_parameters_work_with_explicit_function_protocol():
    from pe_workflow.models import Adapter
    m=buck();m.implementation='python_function';m.entrypoint='compute'
    m.source='def compute(design, operating, ripple):\n    return {"result": {"delta_v": ripple["delta_i"] / (8 * operating["fs"] * design["C"])}}'
    m.adapter=Adapter.model_validate({'arguments':{k:{'from':['inputs',k]} for k in m.inputs},'returns':{'result':{'from':['return','result']}}})
    assert audit_dependencies(m)['status']=='passed'
