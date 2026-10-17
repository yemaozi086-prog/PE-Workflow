import copy
import json
from pathlib import Path
import numpy as np
import pytest
from pe_workflow.dbsrc import fixture, runtime_hash
from pe_workflow.engine import check_module,execute,validate_workflow,WorkflowError
from pe_workflow.models import Binding,Node,Port,Workflow


@pytest.fixture(scope='module')
def migrated():
    modules,wf,inputs=fixture()
    registry={f'{m.id}@{m.version}':m for m in modules}
    trace=[]
    result=execute(wf,registry,inputs,trace)['result']
    return modules,wf,inputs,registry,result,trace


def test_complete_reward_efficiency_and_detailed_matrix_match_original(migrated):
    modules,wf,inputs,registry,result,trace=migrated
    original=json.loads((Path(__file__).resolve().parents[2]/'docs/dbsrc-reference.json').read_text(encoding='utf-8'))['cases']['fresh_default']
    assert validate_workflow(wf,registry,inputs)['valid']
    assert result['valid']==1
    assert result['reward']==pytest.approx(original['reward'],abs=1e-10)
    for key in ('efficiency','volume','cost'):
        assert result[key]==pytest.approx(original['metrics'][key],rel=1e-9,abs=1e-10)
    np.testing.assert_allclose(result['efficiency_matrix'],original['efficiency_matrix'],rtol=1e-9,atol=1e-10)
    np.testing.assert_allclose(result['detail_matrix'],original['detailed_params_matrix'],rtol=1e-9,atol=1e-10)
    assert any('/modulations/' in e['path'] and e['node_id']=='loss_capacitor' for e in trace)
    assert any(e['node_id']=='check_transformer' for e in trace)
    mapping_sources={e['path'] for e in trace if 'field_mappings' in e}
    assert any('field_mappings_ref' in e for e in trace)
    assert all(e['field_mappings_ref'] in mapping_sources for e in trace if 'field_mappings_ref' in e)
    assert len(json.dumps(trace))<45_000_000
    assert all(p.type=='flat_dict' for m in modules for p in (*m.inputs.values(),*m.outputs.values()))


def test_infeasible_then_fresh_repeat_has_no_history(migrated):
    _,wf,inputs,registry,baseline,_=migrated
    strict=copy.deepcopy(inputs);strict['settings']['p_s_limit']=0
    rejected=execute(wf,registry,strict)['result']
    assert rejected['reward']==-1 and rejected['valid']==0
    assert np.count_nonzero(rejected['efficiency_matrix'])==0
    again=execute(wf,registry,inputs)['result']
    assert again==baseline


def test_cannot_forge_trusted_entry_or_replay_different_code():
    modules,_,_=fixture()
    base=modules[0]
    for update in ({'builtin_id':'os.system'},{'builtin_hash':'0'*64},{'source':'arbitrary'},{'version':2}):
        with pytest.raises(WorkflowError):check_module(base.model_copy(update=update))
    assert base.builtin_hash==runtime_hash()


def test_one_scalar_function_replacement_preserves_complete_chain(migrated):
    modules,wf,inputs,registry,baseline,_=migrated
    original=next(m for m in modules if m.builtin_id=='loss_capacitor')
    source=(Path(__file__).resolve().parents[2]/'examples/dbsrc/alternate_capacitor.py').read_text(encoding='utf-8')
    def field(name,from_unit=None,to_unit=None):
        value={'from':['inputs','candidate',name]}
        if from_unit:value['conversion']={'from_unit':from_unit,'to_unit':to_unit}
        return value
    result={'from':['return','loss_mw'],'conversion':{'from_unit':'mW','to_unit':'W'}}
    from pe_workflow.models import Adapter
    adapter=Adapter.model_validate({'arguments':{
        'drive':{'assemble':{'frequency_khz':field('fs','Hz','kHz'),'rms_ma':field('i_rms_c1','A','mA'),'candidate_ok':field('valid')}},
        'tank':{'assemble':{'capacitance_nf':field('capacitance1','F','nF')}},
        'upstream_ok':{'from':['inputs','previous','valid']}},
        'returns':{'loss':{'assemble':{'valid':{'from':['return','ok']},'cap1_loss':result,'p_cap_total':result}}}})
    candidate=original.model_copy(update={'version':2,'implementation':'python_function','builtin_id':None,'builtin_hash':None,'source':source,'entrypoint':'capacitor_loss_alternate','adapter':adapter})
    check_module(candidate)
    changed=wf.model_copy(deep=True)
    body=next(n for n in changed.nodes if n.kind=='batch').body
    scalar_body=next(n for n in body.nodes if n.kind=='batch').body
    target=next(n for n in scalar_body.nodes if n.id=='loss_capacitor');target.module_version=2
    actual=execute(changed,{**registry,f'{candidate.id}@2':candidate},inputs)['result']
    assert actual['reward']==pytest.approx(baseline['reward'],abs=1e-10)
    np.testing.assert_allclose(actual['detail_matrix'],baseline['detail_matrix'],rtol=1e-9,atol=1e-10)
    assert next(n for n in scalar_body.nodes if n.id=='check_capacitor').module_version==1


def test_batch_rejects_different_column_lengths(migrated):
    _,wf,_,registry,_,_=migrated
    outer=next(n for n in wf.nodes if n.kind=='batch')
    node=Node(id='batch',kind='batch',body=outer.body,inputs=copy.deepcopy(outer.inputs))
    # Use a direct input interface so shape failure happens before body execution.
    from pe_workflow.engine import interface
    ins,outs=interface(node,registry)
    node.inputs={k:Binding(source='input',port=k) for k in ins}
    probe=Workflow(schema_version='3.0',id='probe',name='shape',inputs=ins,nodes=[node],outputs={k:Binding(source='node',node='batch',port=k) for k in outs},output_ports=outs)
    columns={k:[1.,2.] for k in ins['rows'].fields};columns['Vin']=[1.]
    with pytest.raises(WorkflowError):execute(probe,registry,{'rows':columns})
