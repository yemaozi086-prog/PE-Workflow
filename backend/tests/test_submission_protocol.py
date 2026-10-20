import copy
import pytest
from pe_workflow.submission_protocol import normalize_submission,EnvelopeError,execution_state
from test_designs import fixture,callback,create
from test_binding_repair_receipts import register_mini,blueprint_from


def test_exact_wrapper_losslessly_normalizes_and_keeps_raw(fixture):
    c,s,store=fixture;plan,_=register_mini(store);blueprint=blueprint_from(plan)
    original=copy.deepcopy(blueprint)
    t=callback(c,s,create(c),kind='plan',plan={'blueprint':blueprint})
    assert t['status']=='awaiting_confirmation',t
    assert t['calls']==1 and len(t['envelope_repair_audit'])==1
    assert blueprint==original
    receipt=c.get('/api/workflow-designs/'+t['id']+'/candidate-receipts').json()['items'][0]
    assert receipt['candidate']['plan']=={'blueprint':original}
    assert t['plan']['steps']==plan['steps']


def test_envelope_rejects_ambiguous_or_missing_without_rewriting():
    for raw in ({'kind':'plan_blueprint'}, {'kind':'plan_blueprint','blueprint':{},'plan':{'blueprint':{}}}, {'kind':'module','plan':{}}):
        old=copy.deepcopy(raw)
        with pytest.raises(EnvelopeError):normalize_submission(raw,True)
        assert raw==old


def test_missing_blueprint_is_paused_with_precise_diagnostic(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan_blueprint')
    assert t['status']=='paused' and t['calls']==1,t
    assert t['last_report']['code']=='RESULT_ENVELOPE'
    assert t['last_report']['issues'][0]['path']=='/blueprint'
    assert t['execution_state']['route']=='needs_confirmation'
    assert t['execution_state']['allowed_paths']==[]


def test_incomplete_blueprint_preserves_previous_and_does_not_regenerate(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan_blueprint',blueprint={'external_inputs':{}})
    assert t['status']=='paused',t
    assert t['previous_candidate']['blueprint']=={'external_inputs':{}}
    assert {'/title','/summary','/steps'} <= {i['path'] for i in t['execution_state']['issues']}


def test_execution_status_never_claims_thinking_and_preserves_budget():
    t={'status':'awaiting_worker','phase':'plan','calls':4,'call_limit':4}
    state=execution_state(t)
    assert state['remaining']==0 and state['label']=='请求已送达，等待结果'
    assert '不证明' in state['activity_notice']


def test_complete_blueprint_in_wrong_field_is_losslessly_recovered(fixture):
    c,s,store=fixture;plan,_=register_mini(store);blueprint=blueprint_from(plan)
    t=callback(c,s,create(c),kind='plan_blueprint',plan=blueprint)
    assert t['status']=='awaiting_confirmation',t
    assert t['envelope_repair_audit'][0]['basis']=='explicit_blueprint_kind_complete_compact_payload'
    assert t['calls']==1


def test_replay_is_read_only_and_worker_receives_repair_ticket(fixture):
    c,s,store=fixture;plan,_=register_mini(store);blueprint=blueprint_from(plan)
    t=callback(c,s,create(c),kind='plan_blueprint',plan=blueprint)
    before=copy.deepcopy(s.get(t['id']))
    replay=c.get('/api/workflow-designs/'+t['id']+'/candidate-replay').json()
    assert replay['items'][0]['status']=='structure_passed',replay
    assert replay['items'][0]['applied'] is False
    assert s.get(t['id'])==before
    assert t['worker_feedback']['accepted'] is True
    context=s.context(before)
    assert context['repair_ticket']['used']==1


def test_control_dependency_error_authorizes_only_consistency_patch(fixture):
    from pe_workflow.planning_protocol import local_patch
    c,s,store=fixture;plan,_=register_mini(store);blueprint=blueprint_from(plan)
    blueprint['steps'][0]['dependencies']=['grid']
    t=callback(c,s,create(c),kind='plan_blueprint',blueprint=blueprint)
    assert t['status']=='dispatch_ready',t
    manifest=t['format_repair']
    assert manifest['allowed_paths']==['/steps/0/dependencies']
    assert manifest['path_modes']['/steps/0/dependencies']=='dependencies'
    with pytest.raises(ValueError):local_patch(manifest,{'base_hash':manifest['base_hash'],'edits':[{'path':'/steps/0/dependencies','value':['scan_loop']}]})
    t=callback(c,s,t,kind='plan_delta',delta={'base_hash':manifest['base_hash'],'edits':[{'path':'/steps/0/dependencies','value':[]}]})
    assert t['status']=='awaiting_confirmation',t
    assert t['plan']['steps'][0]['formula_latex']==plan['steps'][0]['formula_latex']


def test_stopped_call_duration_does_not_keep_growing(fixture):
    from datetime import datetime,timezone
    c,s,_=fixture;t=create(c);internal=s.get(t['id'])
    internal.update(status='paused',dispatched_at=1000,events=[{'at':datetime.fromtimestamp(1010,timezone.utc).isoformat(),'event':'validation_failed'},{'at':datetime.fromtimestamp(9000,timezone.utc).isoformat(),'event':'user_reviewed'}])
    s.save(internal,'test_stopped')
    data=c.get('/api/workflow-designs/'+t['id']+'/progress').json()
    assert data['elapsed_seconds']==10 and data['active'] is False


def test_old_paused_candidate_recovery_exposes_append_without_spending(fixture):
    c,s,store=fixture;plan,_=register_mini(store);blueprint=blueprint_from(plan)
    blueprint['steps'][0]['dependencies']=['grid']
    t=callback(c,s,create(c),kind='plan_blueprint',plan=blueprint)
    old=s.get(t['id']);old.pop('plan',None);old.pop('format_repair',None)
    old.update(status='paused',calls=4,call_limit=4,failure_routing={'route':'needs_confirmation','automatic':False})
    s.save(old,'test_old_protocol_failure')
    preview=c.get('/api/workflow-designs/'+t['id']+'/candidate-replay').json()['items'][0]
    assert preview['recovery_available']
    body={'revision':preview['revision'],'receipt_hash':preview['receipt_hash'],'preview_hash':preview['preview_hash'],'confirm':True}
    from pe_workflow.storage import Conflict
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/recover-candidate',json={**body,'preview_hash':'stale'})
    recovered=c.post('/api/workflow-designs/'+t['id']+'/recover-candidate',json=body).json()
    assert recovered['status']=='paused' and recovered['calls']==4 and recovered['remaining_calls']==0,recovered
    assert recovered['failure_routing']['automatic'] is True
    assert recovered['format_repair']['allowed_paths']==['/steps/0/dependencies']
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/recover-candidate',json=body)
    assert recovered['candidate_recovery_history'][0]['used']==4
    # Do not actually approve more model calls in this test.


def test_continuation_button_obeys_backend_blockers():
    from pe_workflow.submission_protocol import continuation_action
    base={'status':'paused','calls':4,'call_limit':4,'failure_routing':{'route':'candidate_repair'},'format_repair':{'allowed_paths':['/steps/12/dependencies']}}
    a=continuation_action(base)
    assert a['allowed'] and a['new_limit']==7 and a['additional_limit']==3
    assert a['scope']==['/steps/12/dependencies']
    for route in ('platform_fault','uncertain','needs_information','needs_confirmation'):
        assert not continuation_action({**base,'failure_routing':{'route':route}})['allowed']
    for extra in ({'questions':{'q':'missing'}},{'archived':True},{'superseded_by':'new'},{'status':'awaiting_worker'}):
        assert not continuation_action({**base,**extra})['allowed']
