import copy
import json
import uuid
import pytest
from pe_workflow.models import FormulaCode
from pe_workflow.storage import Conflict
from pe_workflow.engine import WorkflowError
from pe_workflow.formula_authoring import contract
from test_designs import fixture, create, callback, confirm, plan

GOOD = {'implementation':'python_subset','source':"def compute(inputs):\n    return {'result': {'value': inputs['data']['value'] * 2}}\n"}


def active(fixture, examples=None, extra=False, guarded=False):
    c,s,store=fixture;p=plan(False)
    if extra:
        p['steps'][0]['inputs']['data']['fields']['n']={'type':'number','quantity':'ratio','unit':'1'}
    if guarded:p['steps'][0]['input_checks']=['data["n"] > 0']
    t=callback(c,s,create(c),kind='plan',plan=p);t=confirm(c,t)
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={
        'revision':t['revision'],'step_id':'calculate','formula':'y=2x',
        'declarations':'用户确认输入输出 W，n 为既有声明（若有）','evidence':'用户公式',
        'examples':examples or []}).json()
    task=s.get(t['id']);task.update(status='awaiting_worker',call_id=uuid.uuid4().hex,token='test-secret',calls=1,formula_authoring='formula_code_v1',worker_result_closed=False)
    return c,s,store,s.save(task,'test_dispatch')


def preview(c,s,t,code=None,**changes):
    payload={'call_id':t['call_id'],'token':t['token'],'contract_hash':contract(s,t)[1],'code':copy.deepcopy(GOOD if code is None else code)}
    payload.update(changes)
    return c.post('/api/workflow-designs/'+t['id']+'/formula-precheck',json=payload).json()


def submit(c,t,p,code=None):
    return c.post('/api/workflow-designs/'+t['id']+'/result',json={
        'call_id':t['call_id'],'token':t['token'],'kind':'module_code',
        'implementation':{'contract_hash':p['contract_hash'],'precheck_id':p['precheck_id'],'code':GOOD if code is None else code}}).json()


def test_compact_preview_formal_validation_and_registration_are_separate(fixture):
    examples=[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}}]
    c,s,store,t=active(fixture,examples)
    before=copy.deepcopy(t);p=preview(c,s,t)
    assert p['status']=='passed' and not p['submitted'] and not p['registered'] and not p['stop_required']
    assert s.get(t['id'])==before and len(store.modules())==1
    assert p['verification']['independent_examples'][0]['status']=='passed'
    result=submit(c,t,p)
    assert result['status']=='formula_candidate' and result['calls']==1 and result['remaining_calls']==3,result
    assert result['candidate']['inputs']==t['plan']['steps'][0]['inputs']
    assert result['candidate']['outputs']==t['plan']['steps'][0]['outputs']
    assert result['candidate']['input_checks']==t['plan']['steps'][0].get('input_checks',[])
    assert result['formula_validation']==p['verification']
    assert len(store.modules())==1  # passing never registers automatically
    accepted=c.post('/api/workflow-designs/'+t['id']+'/register-formula',json={'revision':result['revision'],'allow_unverified':True}).json()
    assert accepted['status']=='awaiting_confirmation' and len(store.modules())==2


def test_repair_in_same_call_preserves_quota_and_no_example_claim(fixture):
    c,s,_,t=active(fixture)
    failed=preview(c,s,t,{'source':'def compute(inputs):\n    return (\n'})
    assert failed['status']=='failed' and not failed['stop_required']
    good=preview(c,s,t)
    assert good['status']=='passed' and good['attempt']==2
    assert good['verification']['probe_status']=='not_run_no_independent_examples'
    assert s.get(t['id'])['calls']==1
    assert submit(c,t,good)['status']=='formula_candidate'


def test_repeat_twice_stops_and_fences_late_results(fixture):
    c,s,_,t=active(fixture);bad={'source':'def compute(inputs):\n    return (\n'}
    assert not preview(c,s,t,bad)['stop_required']
    second=preview(c,s,t,bad)
    assert second['stop_required']
    stopped=s.get(t['id'])
    assert stopped['status']=='paused' and stopped['calls']==1 and stopped['worker_result_closed']
    assert stopped['failure_routing']['stop_reason']=='repeated_error'
    with pytest.raises(Conflict):preview(c,s,t)
    result=submit(c,t,{'contract_hash':contract(s,t)[1],'precheck_id':'late'})
    assert result['status']=='recorded_late_result' and s.get(t['id'])['status']=='paused'


def test_candidate_cannot_change_confirmed_contract_or_reuse_changed_code(fixture):
    c,s,_,t=active(fixture)
    invalid=preview(c,s,t,{**GOOD,'inputs':{}})
    assert invalid['status']=='failed' and invalid['code']=='RESULT_ENVELOPE'
    passed=preview(c,s,t)
    changed={**GOOD,'source':GOOD['source'].replace('* 2','* 3')}
    with pytest.raises(WorkflowError) as e:
        s.authoring.submission(t,{'contract_hash':passed['contract_hash'],'precheck_id':passed['precheck_id'],'code':changed})
    assert e.value.diagnostic.code=='PRECHECK_REQUIRED'
    altered=copy.deepcopy(t);altered['plan']['steps'][0]['inputs']['data']['fields']['value']['unit']='V'
    with pytest.raises(WorkflowError):
        s.authoring.submission(altered,{'contract_hash':passed['contract_hash'],'precheck_id':passed['precheck_id'],'code':GOOD})


def test_independent_answer_conflict_stops_without_leaking_or_editing_answer(fixture):
    examples=[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':123456789}}}]
    c,s,_,t=active(fixture,examples)
    p=preview(c,s,t)
    assert p['code']=='EXAMPLE_RESULT_MISMATCH' and p['stop_required']
    assert '123456789' not in json.dumps(p)
    live=s.get(t['id']);assert live['formula_request']['examples']==examples
    assert live['verification_feedback']['expected']==examples[0]['outputs']
    assert live['calls']==1


def test_unused_field_requires_review_and_is_never_silently_removed(fixture):
    c,s,_,t=active(fixture,extra=True);before=copy.deepcopy(t['plan'])
    p=preview(c,s,t)
    assert p['status']=='passed' and not p['stop_required']
    assert p['verification']['dependency_audit']['unused_required_fields']==['data.n']
    assert p['verification']['dependency_audit']['warnings']
    assert s.get(t['id'])['plan']==before
    result=submit(c,t,p)
    assert 'n' in result['candidate']['inputs']['data']['fields']


def test_check_only_field_and_frozen_checks_are_preserved(fixture):
    c,s,_,t=active(fixture,extra=True,guarded=True)
    p=preview(c,s,t)
    assert p['status']=='passed',p
    assert p['verification']['dependency_audit']['check_fields']==['data.n']
    result=submit(c,t,p)
    assert result['candidate']['input_checks']==['data["n"] > 0']


def test_authentication_budget_latest_receipt_and_cross_call_guards(fixture):
    c,s,_,t=active(fixture)
    with pytest.raises(Conflict):preview(c,s,t,token='wrong')
    assert not s.authoring.rows(t)
    first=preview(c,s,t);second=preview(c,s,t);third=preview(c,s,t)
    assert third['remaining_prechecks']==0 and s.get(t['id'])['calls']==1
    with pytest.raises(Conflict):preview(c,s,t)
    with pytest.raises(WorkflowError):s.authoring.submission(t,{'contract_hash':first['contract_hash'],'precheck_id':first['precheck_id'],'code':GOOD})
    different=copy.deepcopy(t);different['call_id']=uuid.uuid4().hex
    with pytest.raises(WorkflowError):s.authoring.submission(different,{'contract_hash':third['contract_hash'],'precheck_id':third['precheck_id'],'code':GOOD})
    assert submit(c,t,third)['status']=='formula_candidate'


def test_full_module_cannot_bypass_compact_precheck(fixture):
    c,s,_,t=active(fixture)
    from pe_workflow.formula_authoring import assemble
    m=assemble(s,t,FormulaCode.model_validate(GOOD))
    result=c.post('/api/workflow-designs/'+t['id']+'/result',json={'call_id':t['call_id'],'token':t['token'],'kind':'module','module':m.model_dump()}).json()
    assert result['last_report']['code']=='PRECHECK_REQUIRED'
    assert not result.get('candidate')


def test_pause_during_check_does_not_overwrite_current_state(fixture):
    c,s,_,t=active(fixture,[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}}])
    original=s.worker
    def worker(payload,timeout):
        live=s.get(t['id']);live['status']='paused';s.save(live,'user_stopped')
        return original(payload,timeout)
    s.worker=worker
    result=preview(c,s,t)
    assert result['status']=='stale' and result['stop_required']
    assert s.get(t['id'])['status']=='paused' and not s.get(t['id']).get('candidate')


def test_runtime_type_error_is_locally_repairable_but_worker_fault_is_not(fixture):
    examples=[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}}]
    c,s,_,t=active(fixture,examples)
    bad={'source':"def compute(inputs):\n    return {'result': {'value': (inputs['data']['value'] > 0) * 2.0}}\n"}
    p=preview(c,s,t,bad)
    assert p['code']=='CONTROLLED_RUNTIME_ERROR' and not p['stop_required'],p
    assert preview(c,s,t)['status']=='passed'
    s.worker=lambda payload,timeout:{'status':'failed','diagnostics':[{'code':'WORKER_START','message':'test worker unavailable'}]}
    failed=preview(c,s,t)
    assert failed['code']=='FORMULA_WORKER_FAULT' and failed['stop_required']
    assert failed['routing']['route']=='platform_fault'


def test_compact_context_is_small_and_uses_actual_step_and_schema(fixture):
    c,s,_,t=active(fixture)
    context=c.get('/api/workflow-designs/'+t['id']+'/context',params={'call_id':t['call_id'],'section':'index'},headers={'Authorization':'Bearer '+t['token']}).json()
    assert context['required_result_kind']=='module_code'
    assert len(json.dumps(context))<6000
    full=s.context(t)
    assert set(full['schemas'])=={'implementation'}
    assert 'inputs' not in FormulaCode.model_fields
    assert full['formula_authoring']['step_id']=='calculate'


def test_saved_compact_wrapper_recovers_without_generation(fixture):
    c,s,store,t=active(fixture)
    p=preview(c,s,t)
    task=s.get(t['id'])
    task.update(status='paused',worker_result_closed=True,previous_candidate={'kind':'module_code','plan':{'implementation':{'contract_hash':p['contract_hash'],'precheck_id':p['precheck_id'],'code':GOOD}}})
    task=s.save(task,'saved_envelope_failure')
    result=c.post('/api/workflow-designs/'+task['id']+'/revalidate-candidate',json={'revision':task['revision']}).json()
    assert result['status']=='formula_candidate',result
    assert result['calls']==1 and len(store.modules())==1
    assert result['candidate']['source']==GOOD['source']
    assert s.get(task['id'])['compact_recovery_audit']['normalization']


def test_compact_recovery_rejects_changed_code(fixture):
    c,s,_,t=active(fixture);p=preview(c,s,t)
    task=s.get(t['id']);task.update(status='paused',previous_candidate={'kind':'module_code','implementation':{'contract_hash':p['contract_hash'],'precheck_id':p['precheck_id'],'code':{**GOOD,'source':GOOD['source']+'# changed'}}})
    task=s.save(task,'saved_changed_candidate')
    with pytest.raises(WorkflowError):
        c.post('/api/workflow-designs/'+task['id']+'/revalidate-candidate',json={'revision':task['revision']})
