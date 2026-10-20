import copy
import pytest
from pe_workflow.models import DesignPlan,Port
from pe_workflow.plan_audit import preflight
from pe_workflow.planning_protocol import compile_blueprint,ProtocolError,repair_manifest,local_patch
from test_planning_protocol import mini,scalar,bag


def blueprint(plan):
    raw=plan.model_dump();raw['port_defs']={}
    def refs(ports):
        result={}
        for name,port in ports.items():
            key='p'+str(len(raw['port_defs']))
            # Compact declaration only, including explicit array metadata.
            def compact(p):
                v={k:p[k] for k in ('type','unit','quantity','required')}
                if p['fields']:v['fields']={k:compact(f) for k,f in p['fields'].items()}
                if p['type']=='array':v.update(dtype=p['value_type']['dtype'],shape=p['value_type']['shape'],axis_names=[a['name'] for a in p['axes']])
                return v
            raw['port_defs'][key]=compact(port);result[name]={'ref':key}
        return result
    raw['external_inputs']=refs(raw['external_inputs'])
    for s in raw['steps']:
        for side in ('inputs','outputs'):s[side]=refs(s[side])
    return raw


def with_summary():
    p,_=mini();raw=p.model_dump()
    raw['steps'].append({'id':'summary','title':'汇总','purpose':'处理所有点','module_ref':None,'inputs':{'loss':bag({'P':scalar('W','power'),'missing':scalar()})},'outputs':{'summary':bag({'total':scalar('W','power')})},'input_sources':{'loss':{'source':'node','node':'scan_loop','port':'loss'}},'dependencies':['scan_loop']})
    return DesignPlan.model_validate(raw)


def test_collects_all_fields_without_mutating_plan():
    p=with_summary();before=p.model_dump();r=preflight(p)
    assert {i['code'] for i in r['issues']}=={'PORT_TYPE','MISSING_DICT_FIELD'}
    assert len(r['issues'])==2
    assert p.model_dump()==before


def test_source_contract_is_explicit_and_preserves_batch_shape():
    p=with_summary();b=blueprint(p)
    b['steps'][-1]['inputs']={'loss':{'from_source':True,'fields':['P']}}
    before=copy.deepcopy(b);compiled=compile_blueprint(b)
    f=compiled.steps[-1].inputs['loss'].fields['P']
    assert f.type=='array' and f.value_type.shape==['batch_rows'] and f.axes[0].name=='batch_rows'
    assert preflight(compiled)['valid'] and b==before


def test_source_contract_cannot_guess_field_or_override_registered_tool():
    b=blueprint(with_summary());b['steps'][-1]['inputs']={'loss':{'from_source':True,'fields':['invented']}}
    with pytest.raises(ProtocolError,match='缺少字段'):compile_blueprint(b)
    b['steps'][-1]['inputs']={'loss':{'from_source':True,'fields':['P']}}
    b['steps'][-1]['module_ref']='existing@1'
    with pytest.raises(ProtocolError,match='冻结接口'):compile_blueprint(b)


def test_source_contract_in_member_resolves_item_and_capture():
    p,_=mini();b=blueprint(p);b['steps'][0]['module_ref']=None
    b['steps'][0]['inputs']={'item':{'from_source':True,'fields':['I']},'device':{'from_source':True,'fields':['R']}}
    compiled=compile_blueprint(b)
    assert compiled.steps[0].inputs['item'].fields['I'].type=='number'
    assert preflight(compiled)['valid']


def test_missing_node_on_exact_local_scope_is_program_repair_not_user_question():
    p,_=mini();b=blueprint(p);b['steps'][0]['input_sources']['item']={'source':'node','port':'item'}
    with pytest.raises(ProtocolError) as error:compile_blueprint(b)
    m=repair_manifest({'kind':'plan_blueprint','blueprint':b},error.value)
    assert not m['questions'] and m['path_modes']['/steps/0/input_sources/item']=='scope_binding'
    patched=local_patch(m,{'base_hash':m['base_hash'],'edits':[{'path':'/steps/0/input_sources/item','value':{'source':'input','port':'item'}}]})
    assert preflight(compile_blueprint(patched))['valid']
    with pytest.raises(ValueError):local_patch(m,{'base_hash':m['base_hash'],'edits':[{'path':'/steps/0/input_sources/item','value':{'source':'input','port':'device'}}]})
    b['steps'][0]['input_sources']['item']['port']='unknown'
    with pytest.raises(ProtocolError) as error:compile_blueprint(b)
    assert repair_manifest({'kind':'plan_blueprint','blueprint':b},error.value)['questions']


def test_control_format_repairs_keep_selected_producer_and_field():
    p,_=mini();b=blueprint(p)
    b['loops'][0]['outputs']['loss']['field']='P'
    b['steps'][0]['input_sources']['item']['port']='invented_row_name'
    with pytest.raises(ProtocolError) as error:compile_blueprint(b)
    m=repair_manifest({'kind':'plan_blueprint','blueprint':b},error.value)
    assert not m['questions'] and set(m['path_modes'].values())=={'whole_output','item_port'}
    edits=[{'path':'/loops/0/outputs/loss','value':{'source':'node','node':'copper','port':'loss'}},
           {'path':'/steps/0/input_sources/item','value':{'source':'input','port':'item'}}]
    fixed=compile_blueprint(local_patch(m,{'base_hash':m['base_hash'],'edits':edits}))
    assert preflight(fixed)['valid'] and 'P' in fixed.steps[0].outputs['loss'].fields
    edits[0]['value']['node']='some_other_node'
    with pytest.raises(ValueError):local_patch(m,{'base_hash':m['base_hash'],'edits':edits})


def test_array_length_guards_are_bounded_and_do_not_enable_computation():
    from pe_workflow.plan_audit import validate_checks,run_checks
    from test_planning_protocol import array
    ports={'a':Port.model_validate(bag({'x':array('A','current','batch_rows')}))}
    validate_checks(['len(a["x"]) > 0'],ports)
    run_checks(['len(a["x"]) > 0'],{'a':{'x':[1]}})
    with pytest.raises(ValueError):run_checks(['len(a["x"]) > 0'],{'a':{'x':[]}})
    for expr in ['max(a["x"]) > 0','len(a["x"])+1 > 0','len(a["x"][0]) > 0','a["x"] > 0']:
        with pytest.raises(ValueError):validate_checks([expr],ports)


def test_bare_input_contract_has_exact_source_copy_repair_scope():
    p=with_summary();b=blueprint(p)
    from test_planning_protocol import array
    b['port_defs']['bad_scalar_column']={'type':'array','unit':'W','quantity':'power','dtype':'float64','shape':['batch_rows'],'axis_names':['batch_rows']}
    b['steps'][-1]['inputs']={'loss':{'ref':'bad_scalar_column'}}
    with pytest.raises(ProtocolError) as error:compile_blueprint(b)
    m=repair_manifest({'kind':'plan_blueprint','blueprint':b},error.value)
    assert m['path_modes']['/steps/1/inputs/loss']=='input_contract'
    fixed=local_patch(m,{'base_hash':m['base_hash'],'edits':[{'path':'/steps/1/inputs/loss','value':{'from_source':True,'fields':['P']}}]})
    assert preflight(compile_blueprint(fixed))['valid']
    with pytest.raises(ValueError):local_patch(m,{'base_hash':m['base_hash'],'edits':[{'path':'/steps/1/inputs/loss','value':{'ref':'some_new_contract'}}]})


def test_source_copy_reports_all_missing_case_sensitive_fields():
    b=blueprint(with_summary());b['steps'][-1]['inputs']={'loss':{'from_source':True,'fields':['p','other']}}
    with pytest.raises(ProtocolError) as error:compile_blueprint(b)
    assert len(error.value.issues)==2
    assert all(i['available_fields']==['P'] for i in error.value.issues)
    m=repair_manifest({'kind':'plan_blueprint','blueprint':b},error.value)
    assert m['allowed_paths']==['/steps/1/inputs/loss'] and not m['questions']


def test_all_raw_row_guards_run_before_any_member_calculation():
    from pe_workflow.plan_controls import compile_plan
    from pe_workflow.engine import execute,WorkflowError
    p,registry=mini();p.steps[0].input_checks.append('item["I"] >= 0')
    w=compile_plan(p,'upfront_guards',registry=registry);trace=[]
    with pytest.raises(WorkflowError) as error:execute(w,registry,{'scan':{'currents':[1,-1]},'device':{'R':1}},trace)
    assert error.value.diagnostic.code=='INPUT_PRECONDITION'
    assert '/preflight/1/copper/' in error.value.diagnostic.path
    assert not any(r['node_id']=='copper' for r in trace)
    trace=[];out=execute(w,registry,{'scan':{'currents':[1,2]},'device':{'R':1}},trace)
    assert out['scan_loop__loss']['P']==[1,4]


def test_registered_batch_member_interfaces_resolve_before_source_copy():
    p,registry=mini();b=blueprint(p)
    b['steps'][0]['inputs']={};b['steps'][0]['outputs']={}
    b['steps'].append({'id':'summary','title':'summary','purpose':'all points',
        'inputs':{'loss':{'from_source':True,'fields':['P']}},
        'outputs':{},'input_sources':{'loss':{'source':'node','node':'scan_loop','port':'loss'}},
        'dependencies':['scan_loop']})
    before=copy.deepcopy(b);compiled=compile_blueprint(b,registry)
    assert b==before
    assert compiled.steps[0].outputs==p.steps[0].outputs
    field=compiled.steps[-1].inputs['loss'].fields['P']
    assert field.type=='array' and field.value_type.shape==['batch_rows']
    assert registry[next(iter(registry))].outputs==p.steps[0].outputs


def test_unresolved_upstream_is_not_a_fake_empty_binding_question():
    p,_=mini();b=blueprint(p);b['steps'][0]['outputs']={}
    b['steps'].append({'id':'summary','title':'summary','purpose':'all points',
        'inputs':{'loss':{'from_source':True}},'outputs':{},
        'input_sources':{'loss':{'source':'node','node':'scan_loop','port':'loss'}}})
    with pytest.raises(ProtocolError) as error:compile_blueprint(b)
    issue=error.value.issues[0]
    assert issue['code']=='UPSTREAM_CONTROL_UNRESOLVED'
    assert issue['source_binding']['node']=='scan_loop'
    manifest=repair_manifest({'kind':'plan_blueprint','blueprint':b},error.value)
    assert manifest is None or not manifest['questions']
