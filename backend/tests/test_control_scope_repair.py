import copy
import pytest
from pe_workflow.models import Binding,DesignPlan
from pe_workflow.control_contracts import scope_issues,boundary_corrections
from pe_workflow.plan_audit import preflight
from pe_workflow.plan_controls import compile_plan
from pe_workflow.engine import execute
from pe_workflow.repair_routing import route_failure
from test_designs import fixture
from test_control_contracts import stopped
from test_planning_protocol import mini


def wrong_rows(count=3):
    p,registry=mini();base=p.steps[0]
    for i in range(1,count):
        step=base.model_copy(deep=True);step.id='extra_'+str(i);p.steps.append(step);p.loops[0].step_ids.append(step.id)
    for step in p.steps:
        step.input_sources['item']=Binding(source='node',node='grid',port='rows')
        step.dependencies=['grid']
    return p,registry


def test_all_scope_errors_precede_dependency_and_connection_errors():
    p,_=wrong_rows();p.steps[0].dependencies=[]
    original=p.model_dump();report=preflight(p)
    assert len(report['issues'])==3
    assert {i['code'] for i in report['issues']}=={'CONTROL_SCOPE_REFERENCE'}
    assert {i['path'] for i in report['issues']}=={f'/steps/{i}/input_sources/item' for i in range(3)}
    assert p.model_dump()==original
    routed=route_failure(ValueError('scope'),{'code':'PLAN_PREFLIGHT','plan_preflight':report})
    assert routed['route']=='needs_confirmation' and not routed['automatic']


def test_reviewed_row_repair_preserves_contracts_and_executes():
    p,r=wrong_rows(1);before=p.model_dump();fixed,edits=boundary_corrections(p)
    assert len(edits)==2 and before==p.model_dump()
    s=fixed['steps'][0];assert s['input_sources']['item']['source']=='input'
    assert s['dependencies']==[]
    for field in ('inputs','outputs','input_checks','formula_latex','module_ref'):
        assert s[field]==before['steps'][0][field]
    q=DesignPlan.model_validate(fixed);assert preflight(q)['valid']
    result=execute(compile_plan(q,'scope_demo',registry=r),r,{'scan':{'currents':[1,2,3]},'device':{'R':2}})
    assert result['scan_loop__loss']['P']==[2,8,18]


@pytest.mark.parametrize('variant',['alias','different_source','incompatible','scalar'])
def test_ambiguous_or_incompatible_reference_not_guessed(variant):
    p,_=wrong_rows(1);s=p.steps[0]
    if variant=='alias':s.input_sources['other']=s.input_sources.pop('item');s.inputs['other']=s.inputs.pop('item')
    if variant=='different_source':s.input_sources['item'].node='other_grid'
    if variant=='incompatible':s.inputs['item'].fields['I'].unit='V'
    if variant=='scalar':s.input_sources['item'].field='I'
    fixed,edits=boundary_corrections(p)
    assert not edits and fixed==p.model_dump()
    assert scope_issues(p)


def test_scope_preview_confirm_is_immutable_and_keeps_budget(fixture):
    c,s,store=fixture;p,registry=wrong_rows(1)
    for m in registry.values():store.add_module(m)
    t=stopped(s,c,p.model_dump());before=copy.deepcopy(s.get(t['id']))
    view=c.get('/api/workflow-designs/'+t['id']+'/control-repair').json()
    assert view['mode']=='scope_repair' and view['after_check']['valid']
    assert s.get(t['id'])==before
    body={'revision':view['revision'],'preview_hash':view['preview_hash'],'allow_unverified':True}
    child=c.post('/api/workflow-designs/'+t['id']+'/control-repair',json=body).json()
    assert child['status']=='awaiting_confirmation' and child['calls']==before['calls'] and child['remaining_calls']==0
    assert child['repeat_count']==before['repeat_count'] and s.get(t['id'])==before
    assert c.post('/api/workflow-designs/'+t['id']+'/control-repair',json=body).json()['id']==child['id']
    assert not store.runs()


def test_top_level_cannot_read_body_and_shared_input_must_be_captured():
    p,_=mini();p.loops[0].captures={}
    extra=p.steps[0].model_copy(deep=True);extra.id='outside';extra.input_sources['item']=Binding(source='node',node='copper',port='loss');p.steps.append(extra)
    issues=scope_issues(p)
    assert any(i['path']=='/steps/0/input_sources/device' for i in issues)
    assert any(i['path']=='/steps/1/input_sources/item' for i in issues)
