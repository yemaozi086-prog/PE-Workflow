import copy
import pytest
from pe_workflow.dictionary_demo import fixture, upload_candidate
from pe_workflow.engine import execute, validate_workflow, check_module, WorkflowError
from pe_workflow.catalog import compile_catalog
from pe_workflow.models import NamingRule
from pe_workflow.naming import check_rule, resolve_rule

def setup():
    modules,wf,values=copy.deepcopy(fixture())
    return modules,wf,values,{f'{m.id}@{m.version}':m for m in modules}

def test_dual_dictionary_projection_and_units():
    modules,wf,values,registry=setup();trace=[]
    report=validate_workflow(wf,registry,values)
    assert report['valid'] and report['connection_plans']
    result=execute(wf,registry,values,trace)
    assert result['loss']['p_cap_total']==pytest.approx(1.1368210220849668)
    assert trace[-1]['inputs']=={'electrical':{'fs':70000,'i_rms_c1':10},'component':{'C_total':pytest.approx(2e-7),'tan_delta':.001}}
    assert 'field_mappings' in trace[-1]

def test_unconsumed_producer_fields_are_still_validated():
    _,wf,values,registry=setup();values['electrical_request']['voltage']='bad'
    with pytest.raises(WorkflowError):execute(wf,registry,values)

def test_missing_mapping_never_guesses():
    _,wf,values,registry=setup();wf.nodes[-1].inputs['electrical'].field_map={}
    assert not validate_workflow(wf,registry,values)['valid']

def test_same_name_wrong_semantics_is_rejected():
    modules,wf,values,registry=setup()
    modules[-1].inputs['electrical'].fields['fs'].device='unrelated_device'
    assert not validate_workflow(wf,registry,values)['valid']

def test_bare_public_input_is_rejected():
    modules,_,_,_=setup();modules[-1].inputs['electrical']=modules[-1].inputs['electrical'].fields['fs']
    with pytest.raises(WorkflowError):check_module(modules[-1])

@pytest.mark.parametrize('frequency,current,capacitance',[(70,10000,200),(100,1500,300),(25,25000,100)])
def test_uploaded_nested_function_matches_original(frequency,current,capacitance):
    _,wf,values,registry=setup();values['electrical_request'].update(f_sw=frequency,current_rms=current);values['component_request']['C_total']=capacitance
    original=execute(wf,registry,values)
    candidate=upload_candidate();check_module(candidate);registry['cr_loss_flat@2']=candidate;wf.nodes[-1].module_version=2
    adapted=execute(wf,registry,values)
    assert adapted['loss']==pytest.approx(original['loss'])

def test_catalog_preserves_raw_rows_and_configuration():
    from pe_workflow.dictionary_demo import ROOT
    c=compile_catalog((ROOT/'resources/parameter_dictionary-v1.csv').read_text(encoding='utf-8-sig'))
    assert len(c['rows'])==310
    assert all('raw' in r for r in c['rows'])
    assert any(r['status']=='needs_review' for r in c['rows'])

def test_rules_require_positive_negative_and_preserve_instance():
    rule=NamingRule(id='current_alias',version=1,scope='module',scope_id='example',source_pattern='Irms{index}',target_pattern='i_rms_c{index}',evidence='user supplied naming convention',declaration={'type':'number','quantity':'current','unit':'A','instance_id':'C{index}'},examples=[{'source':'Irms2','target':'i_rms_c2'},{'source':'Ipeak2','target':None}])
    check_rule(rule)
    assert resolve_rule(rule,'Irms2')['declaration']['instance_id']=='C2'
    rule.examples.pop()
    with pytest.raises(ValueError):check_rule(rule)

def test_registration_packet_and_dictionary_catalog_endpoints(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from pe_workflow import api
    from pe_workflow.storage import Store
    from pe_workflow.catalog import seed
    store=Store(tmp_path/'dictionary.sqlite');seed(store);monkeypatch.setattr(api,'store',store)
    with TestClient(api.app) as client:
        packet=client.post('/api/harness/prepare-registration',json={'module_id':'uploaded','source':'def square(x):\n    return x*x\n','instruction':'Adapt provided function without changing formula'}).json()
        assert packet['status']=='prepared' and packet['connected'] is False
        assert packet['context']['protocol_version']=='4.0'
        assert len(packet['context']['parameter_catalogs'][0]['rows'])==310
        assert packet['context']['supplied_anchors']==[]
        assert store.context(packet['context_id'])['task_kind']=='register_flat_dictionary_module'
        assert client.get('/api/harness/dictionary-contract').json()['module_contract_version']=='3.0'
