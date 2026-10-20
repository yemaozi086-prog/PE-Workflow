import copy
import pytest
from pe_workflow.models import DesignPlan, Module, Workflow, Node, Binding, Port
from pe_workflow.plan_audit import preflight
from pe_workflow.engine import execute, WorkflowError, validate_workflow


def bag(name,quantity='current',statistic=''):
    return Port(type='flat_dict',fields={name:Port(type='number',quantity=quantity,unit='A' if quantity=='current' else 'W',statistic=statistic)})


def chain():
    return DesignPlan(title='test',summary='test',external_inputs={'source':bag('i','current','rms')},steps=[
        dict(id='first',title='first',purpose='first',inputs={'electrical':bag('i','current','rms')},outputs={'loss':bag('p','power_loss')},input_sources={'electrical':Binding(source='input',port='source')}),
        dict(id='second',title='second',purpose='second',dependencies=['first'],inputs={'loss':bag('p','power_loss')},outputs={'result':bag('p','power_loss')},input_sources={'loss':Binding(source='node',node='first',port='loss')})])


def test_exact_semantics_checked_before_binding():
    p=chain();assert preflight(p)['valid']
    p.steps[0].inputs['electrical'].fields['i'].statistic='average'
    assert any(i['code']=='SEMANTIC_CONFLICT' for i in preflight(p)['issues'])

def test_source_output_diagnostic_lists_real_dictionary_names():
    p=chain();p.steps[1].input_sources['loss'].port='first'
    r=preflight(p)
    assert r['issues'][0]['code']=='PLAN_SOURCE_PORT_NOT_FOUND'
    assert r['repairs'][0]['allowed_outputs']==['loss']
    p=chain();p.steps[1].inputs['loss'].fields['p'].quantity='power'
    assert any(i['code']=='SEMANTIC_CONFLICT' for i in preflight(p)['issues'])


def test_legacy_multisource_not_guessed_and_no_mutation():
    p=chain();p.external_inputs={}
    for step in p.steps:step.input_sources={}
    before=p.model_dump();r=preflight(p)
    assert not r['valid'] and r['issues'][0]['code']=='PLAN_SOURCES_REQUIRED'
    assert p.model_dump()==before


def test_extra_fields_and_independent_bags_allowed():
    p=chain();p.external_inputs['device']=bag('r','power')
    p.steps[0].inputs['device']=bag('r','power')
    p.steps[0].input_sources['device']=Binding(source='input',port='device')
    p.external_inputs['source'].fields['unused']=Port(type='number',quantity='current',unit='A')
    assert preflight(p)['valid']

def test_partial_one_bag_contract_explains_derivation_without_guessing():
    p=chain();p.external_inputs['devices']=Port(type='flat_dict',fields={
        'R_hs':Port(type='number',quantity='resistance',unit='ohm'),
        'R_ls':Port(type='number',quantity='resistance',unit='ohm')})
    p.steps[1].module_ref='copper_loss@1';p.steps[1].inputs={'electrical':bag('i_rms','current','rms')}
    p.steps[1].inputs['electrical'].fields['resistance']=Port(type='number',quantity='resistance',unit='ohm')
    p.steps[0].outputs={'rms':bag('IHS','current','rms')}
    p.steps[1].input_sources={'electrical':Binding(source='node',node='first',port='rms',field_map={'i_rms':'IHS'})}
    before=p.model_dump();r=preflight(p);repair=r['repairs'][0]
    assert not r['valid'] and repair['strategy']=='derive_independent_inputs'
    assert repair['module_ref']=='copper_loss@1'
    assert {c['field'] for c in repair['missing_fields'][0]['candidates']}=={'R_hs','R_ls'}
    assert repair['not_applied'] and p.model_dump()==before
    # A declared split solves the structure; the platform does not choose R_hs.
    step=p.steps[1];step.module_ref=None;step.derives_from='copper_loss@1'
    step.inputs['device']=Port(type='flat_dict',fields={'resistance':step.inputs['electrical'].fields.pop('resistance')})
    step.input_sources['device']=Binding(source='input',port='devices',field_map={'resistance':'R_hs'})
    assert preflight(p)['valid']

@pytest.mark.parametrize('condition',["missing['i'] > 0", "electrical['missing'] > 0", "electrical['i'] >"])
def test_invalid_guards_report_instead_of_crashing(condition):
    p=chain();p.steps[0].input_checks=[condition]
    r=preflight(p)
    assert not r['valid'] and r['issues'][0]['code']=='INPUT_CHECK_INVALID'

def test_guard_repair_uses_declared_binding_instead_of_guessing():
    p=chain();step=p.steps[0]
    p.external_inputs={'resistances':Port(type='flat_dict',fields={'R_hs':Port(type='number',quantity='resistance',unit='ohm')})}
    step.inputs={'device':Port(type='flat_dict',fields={'R':Port(type='number',quantity='resistance',unit='ohm')})}
    step.input_sources={'device':Binding(source='input',port='resistances',field_map={'R':'R_hs'})}
    step.input_checks=["resistances['R_hs'] >= 0"]
    r=preflight(p);repair=next(r for r in r['repairs'] if r['code']=='INPUT_CHECK_SCOPE')
    assert repair['references'][0]['candidate_local_paths'][0]['port']=='device'
    assert repair['references'][0]['candidate_local_paths'][0]['field']=='R'
    assert step.input_checks==["resistances['R_hs'] >= 0"]
    fixed=p.model_copy(deep=True);fixed.steps[0].input_checks=["device['R'] >= 0"]
    assert preflight(fixed,p)['valid']
    fixed.steps[0].input_checks=[]
    assert any(i['code']=='PLAN_GUARD_REGRESSION' for i in preflight(fixed,p)['issues'])


def test_guard_stops_before_division_and_is_compiled():
    data=Port(type='flat_dict',fields={'denominator':Port(type='number',quantity='ratio',unit='')})
    out=Port(type='flat_dict',fields={'value':Port(type='number',quantity='ratio',unit='')})
    m=Module(id='divide',version=1,name='divide',contract_version='3.0',inputs={'data':data},outputs={'result':out},expressions={'result':"{'value': 1/data['denominator']}"},provenance='unit test')
    wf=Workflow(id='test',name='test',schema_version='3.0',inputs=m.inputs,nodes=[Node(id='calculate',module_id='divide',module_version=1,inputs={'data':Binding(source='input',port='data')},input_checks=["data['denominator'] > 0"])],outputs={'result':Binding(source='node',node='calculate',port='result')},output_ports=m.outputs)
    trace=[]
    with pytest.raises(WorkflowError) as e:execute(wf,{'divide@1':m},{'data':{'denominator':0}},trace)
    assert e.value.diagnostic.code=='INPUT_PRECONDITION'
    assert trace[0]['status']=='failed' and not trace[0].get('outputs')
    assert execute(wf,{'divide@1':m},{'data':{'denominator':2}})['result']['value']==.5
    wf.nodes[0].input_checks=["1/data['denominator'] > 0"]
    assert not validate_workflow(wf,{'divide@1':m})['valid']
