import copy,json
from pathlib import Path
import pytest
from pe_workflow.design_context_pages import page
from pe_workflow.planning_protocol import blueprint_schema,delta_schema,compile_blueprint,repair_manifest,local_patch,ProtocolError
from pe_workflow.models import Module
from test_designs import fixture

def wire(value):
    chunks=[];offset=0
    while True:
        part=page(value,'schema',offset);chunks.append(part['text'])
        if part['next_offset'] is None:break
        offset=part['next_offset']
    return json.loads(''.join(chunks))

def test_schema_transport_is_lossless_including_empty_definitions():
    value={'delta':delta_schema(['/port_defs/a']),'blueprint':blueprint_schema()}
    assert wire(value)==value
    assert 'value' in wire(value)['delta']['properties']['edits']['items']['properties']

def test_loop_output_schema_prohibits_scalar_extraction_upfront():
    props=blueprint_schema()['$defs']['PlanLoop']['properties']['outputs']['additionalProperties']
    assert props['properties']['field']=={'type':'null'}
    assert props['required']==['source','node','port']

def test_actual_dsh_failure_is_local_format_repair_not_user_information(fixture):
    from pe_workflow.plan_controls import compile_plan
    from pe_workflow.engine import execute
    from pe_workflow.plan_audit import preflight
    c,s,store=fixture
    sample=json.loads((Path(__file__).parent/'fixtures/real_scan_scalar_output.json').read_text(encoding='utf-8'))
    registry={f'{m["id"]}@{m["version"]}':Module.model_validate(m) for m in sample['modules']}
    for m in registry.values():store.add_module(m)
    candidate=sample['candidate'];before=copy.deepcopy(candidate)
    with pytest.raises(ProtocolError) as caught:compile_blueprint(candidate['blueprint'])
    manifest=repair_manifest(candidate,caught.value,registry)
    path='/loops/0/outputs/loss_p_w'
    assert manifest['questions']==[] and manifest['path_modes'][path]=='whole_output'
    value=copy.deepcopy(candidate['blueprint']['loops'][0]['outputs']['loss_p_w']);value.pop('field')
    patch={'base_hash':manifest['base_hash'],'edits':[{'path':path,'value':value}]}
    repaired=local_patch(manifest,patch)
    compiled=compile_blueprint(repaired);s.audit_plan(compiled)
    assert preflight(compiled)['valid']
    workflow=compile_plan(compiled,'regression_only',registry=registry)
    output=execute(workflow,registry,{'scan':{'currents':[0,1,2,3]},'device':{'R':0.5}})
    assert [v['p_w'] for v in output.values() if isinstance(v,dict) and 'p_w' in v]==[[0,0.5,2,4.5]]
    assert candidate==before
    bad=copy.deepcopy(patch);bad['edits'][0]['value']['node']='other_physical_source'
    with pytest.raises(ValueError):local_patch(manifest,bad)

def test_actual_receipt_passes_platform_pipeline_without_another_model_call(fixture):
    from test_designs import create,callback
    c,s,store=fixture
    sample=json.loads((Path(__file__).parent/'fixtures/real_scan_scalar_output.json').read_text(encoding='utf-8'))
    for raw in sample['modules']:store.add_module(Module.model_validate(raw))
    original=copy.deepcopy(sample['candidate'])
    result=callback(c,s,create(c),**sample['candidate'])
    assert result['status']=='awaiting_confirmation' and result['calls']==1
    assert result['binding_repair_audit'][-1]['basis']=='frozen_batch_whole_dictionary'
    assert result['plan']['loops'][0]['outputs']['loss_p_w']['field'] is None
    assert s.receipts.list(result['id'])['items'][0]['candidate']==original
    assert not store.runs()

@pytest.mark.parametrize('change',['unknown_field','unknown_node','field_map'])
def test_batch_normalization_never_guesses_unknown_sources_or_fields(change):
    from pe_workflow.planning_protocol import normalize_binding_ports
    sample=json.loads((Path(__file__).parent/'fixtures/real_scan_scalar_output.json').read_text(encoding='utf-8'))
    registry={f'{m["id"]}@{m["version"]}':Module.model_validate(m) for m in sample['modules']}
    base=sample['candidate']['blueprint'];b=base['loops'][0]['outputs']['loss_p_w']
    if change=='unknown_field':b['field']='unknown'
    elif change=='unknown_node':b['node']='different_source'
    else:b['field_map']={'x':'p_w'}
    before=copy.deepcopy(b);normalized,audit=normalize_binding_ports(base,registry)
    assert normalized['loops'][0]['outputs']['loss_p_w']==before
    assert not any(a['basis']=='frozen_batch_whole_dictionary' for a in audit)
