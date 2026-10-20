import copy,json
import pytest
from pe_workflow.models import DesignPlan,BindingAssemblyConfirm
from pe_workflow.finite_control import grid_interface,control_spec
from pe_workflow.control_contracts import describe,port_corrections,reference_issues
from pe_workflow.plan_controls import compile_plan
from pe_workflow.plan_audit import preflight
from pe_workflow.engine import execute
from pe_workflow.storage import Conflict
from test_designs import fixture,create
from test_planning_protocol import mini,builder,bag,array,scalar,inp,node


def stopped(s,c,raw):
    t=create(c);t=s.get(t['id']);t.update(plan=raw,status='needs_clarification',phase='plan',repair_mode='plan_patch',patch_base_plan=copy.deepcopy(raw),calls=10,call_limit=10,repeat_count=2,questions={'q':'platform port?'})
    return s.save(t,'test_legacy_control_failure')


def test_runtime_grid_contract_and_exact_diagnostics():
    p,_=mini();p.loops[0].rows.port='wrong_alias';original=p.model_dump()
    desc=describe(p);g=next(n for n in desc['nodes'] if n['id']=='grid')
    _,formal=grid_interface({'scan':p.external_inputs['scan']},{'I':'scan.currents'})
    assert g['outputs']=={k:v.model_dump() for k,v in formal.items()}
    assert control_spec()['grid']['output_port']=='rows'
    issues=reference_issues(p)
    assert issues[0]['path']=='/loops/0/rows/port' and issues[0]['available_ports']==['rows']
    fixed,edits,_=port_corrections(p)
    assert len(edits)==1 and fixed['loops'][0]['rows']['port']=='rows'
    assert p.model_dump()==original and preflight(DesignPlan.model_validate(fixed))['valid']
    assert describe(fixed)['nodes'][1]['outputs']['loss']['fields']['P']['value_type']['shape']==['batch_rows']


def test_confirm_derives_preserves_budget_parent_and_is_idempotent(fixture):
    c,s,store=fixture;p,registry=mini();p.loops[0].rows.port='bad'
    for m in registry.values():store.add_module(m)
    t=stopped(s,c,p.model_dump());before=copy.deepcopy(s.get(t['id']));modules=store.modules()
    view=c.get('/api/workflow-designs/'+t['id']+'/control-repair').json()
    assert view['available'] and view['remaining']==0 and view['after_check']['valid']
    assert s.get(t['id'])==before
    body={'revision':view['revision'],'preview_hash':view['preview_hash'],'allow_unverified':True}
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/control-repair',json={**body,'preview_hash':'stale'})
    child=c.post('/api/workflow-designs/'+t['id']+'/control-repair',json=body).json()
    assert child['id']!=t['id'] and child['parent_id']==t['id']
    assert child['calls']==10 and child['remaining_calls']==0 and child['repeat_count']==2
    assert child['status']=='awaiting_confirmation' and not child.get('repair_mode')
    assert child['plan']['steps']==before['plan']['steps']
    assert child['plan']['external_inputs']==before['plan']['external_inputs']
    assert c.post('/api/workflow-designs/'+t['id']+'/control-repair',json=body).json()['id']==child['id']
    assert s.get(t['id'])==before and store.modules()==modules and not store.runs()


def test_unknown_or_ambiguous_source_and_active_task_do_not_repair(fixture):
    c,s,_=fixture;p,_=mini();raw=p.model_dump();raw['loops'][0]['rows']['node']='unknown'
    fixed,edits,_=port_corrections(raw);assert not edits and fixed==raw
    raw=p.model_dump();raw['steps'][0]['id']='grid'
    with pytest.raises(ValueError):port_corrections(raw)
    raw=p.model_dump();raw['loops'][0]['rows']['port']='bad';t=stopped(s,c,raw)
    task=s.get(t['id']);task['status']='awaiting_worker';s.save(task,'test_running')
    assert not c.get('/api/workflow-designs/'+t['id']+'/control-repair').json()['available']


def test_control_context_is_authoritative_paged_and_not_a_user_question(fixture):
    c,s,_=fixture;p,_=mini();t=stopped(s,c,p.model_dump())
    from pe_workflow.design_context_pages import read,index_data
    payload=json.loads(read(s,t,'controls')['text'])
    assert payload['specification']['grid']['output_port']=='rows'
    assert payload['nodes'][0]['outputs']['rows']['fields']['I']['axes'][0]['name']=='batch_rows'
    assert 'controls' in index_data(s,t)['sections']


def test_different_two_axis_power_scan_preserves_cartesian_order():
    raw,registry,add=builder('电阻负载两轴功率扫描')
    raw['external_inputs']={'scan':bag({'voltages':array('V','voltage','voltage_count'),'resistances':array('ohm','resistance','resistance_count')})}
    raw['grids']=[{'id':'operating_grid','title':'电压与电阻组合','input_sources':{'scan':inp('scan')},'axes':{'V':'scan.voltages','R':'scan.resistances'},'axis_order':['V','R']}]
    add('power',{'item':bag({'V':scalar('V','voltage'),'R':scalar('ohm','resistance')})},{'item':inp('item')},
        {'loss':bag({'P':scalar('W','power')})},{'loss':"{'P':item['V']**2/item['R']}"},['item["R"] > 0'])
    raw['loops']=[{'id':'power_scan','title':'逐组合计算','step_ids':['power'],'rows':node('operating_grid','old_alias'),'captures':{},'outputs':{'loss':node('power','loss')}}]
    fixed,edits,_=port_corrections(raw)
    p=DesignPlan.model_validate(fixed);assert preflight(p)['valid']
    w=compile_plan(p,'power_scan_demo',registry=registry)
    outputs=execute(w,registry,{'scan':{'voltages':[2,4],'resistances':[1,2,4]}},[])
    assert outputs['power_scan__loss']['P']==[4,2,1,16,8,4]
    assert outputs['operating_grid__rows']['V']==[2,2,2,4,4,4]
    assert outputs['operating_grid__rows']['R']==[1,2,4,1,2,4]
    assert edits[0]['path']=='/loops/0/rows/port'


def test_text_support_preserves_declared_reason_without_deleting(fixture):
    c,s,_=fixture;p,_=mini();p.loops[0].rows.port='wrong'
    from pe_workflow.models import Port
    p.steps[0].module_ref=None
    p.steps[0].outputs['loss'].fields['reason']=Port(type='string',quantity='failure_reason',unit='1')
    t=stopped(s,c,p.model_dump());view=c.get('/api/workflow-designs/'+t['id']+'/control-repair').json()
    assert view['available'] and view['after_check']['valid']
    body={'revision':view['revision'],'preview_hash':view['preview_hash'],'allow_unverified':True}
    child=c.post('/api/workflow-designs/'+t['id']+'/control-repair',json=body).json()
    assert child['status']=='awaiting_confirmation' and child['plan']['steps'][0]['outputs']['loss']['fields']['reason']['type']=='string'
    assert child['calls']==10 and child['remaining_calls']==0


def test_runtime_recheck_keeps_plan_and_budget_and_does_not_repeat(fixture):
    c,s,store=fixture;p,registry=mini()
    for m in registry.values():store.add_module(m)
    t=stopped(s,c,p.model_dump());original=copy.deepcopy(s.get(t['id']))
    view=c.get('/api/workflow-designs/'+t['id']+'/control-repair').json()
    assert view['mode']=='runtime_recheck' and view['available'] and view['edits']==[]
    child=c.post('/api/workflow-designs/'+t['id']+'/control-repair',json={'revision':view['revision'],'preview_hash':view['preview_hash'],'allow_unverified':True}).json()
    assert child['status']=='awaiting_confirmation' and child['plan']==original['plan']
    assert child['calls']==10 and child['remaining_calls']==0 and s.get(t['id'])==original
    task=s.get(child['id']);task['status']='paused';s.save(task,'test_manual_pause')
    assert not c.get('/api/workflow-designs/'+child['id']+'/control-repair').json()['available']
