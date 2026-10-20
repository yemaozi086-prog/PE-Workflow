import copy
import pytest
from test_designs import fixture
from test_tool_completion import source,queue
from pe_workflow.models import CompletionRecovery,DeclarationRecovery
from pe_workflow.storage import Conflict


def missing(c,s):
    t=source(c,s,count=1);raw=s.get(t['id'])
    port=raw['plan']['steps'][0]['outputs']['result']['fields']['value']
    port.update(quantity='',description='示例输出功率')
    t=s.save(raw,'fixture_missing');q,_=queue(c,t);s.completion.tick()
    return q,s.get(q['items'][0]['task_id'])


def test_description_draft_is_read_only_and_confirmed_revision_is_idempotent(fixture):
    c,s,_=fixture;q,t=missing(c,s);original=copy.deepcopy(t)
    r=s.completion.recovery;view=r.task_preview(t['id'])
    assert s.get(t['id'])==original
    assert view['fields'][0]['proposed']=='示例输出功率' and view['calls']==0
    req=DeclarationRecovery(revision=t['revision'],preview_hash=view['preview_hash'],confirm=True,
                            quantities={'outputs.result.value':'示例输出功率'})
    child=r.declarations(t['id'],req)
    assert child['status']=='completion_pending' and child['calls']==0
    assert s.get(t['id'])==original and child['parent_id']==t['id']
    assert child['plan']['steps'][0]['formula_latex']==original['plan']['steps'][0]['formula_latex']
    assert r.declarations(t['id'],req)['id']==child['id']
    assert s.completion.get(q['id'])['items'][0]['task_id']==child['id']
    s.completion.tick();assert s.get(child['id'])['status']=='dispatch_ready'


def test_partial_semantics_remain_blocked_and_no_source_is_not_guessed(fixture):
    c,s,_=fixture;q,t=missing(c,s)
    t['plan']['steps'][0]['inputs']['data']['fields']['value'].update(quantity='',description='')
    t=s.save(t,'fixture_second_missing');r=s.completion.recovery;v=r.task_preview(t['id'])
    assert any(f['source'] is None and not f['proposed'] for f in v['fields'])
    child=r.declarations(t['id'],DeclarationRecovery(revision=t['revision'],preview_hash=v['preview_hash'],confirm=True,quantities={'outputs.result.value':'输出功率'}))
    assert child['status']=='blocked' and child['calls']==0 and child['completion_missing_info']


def test_stale_draft_and_unlisted_fields_rejected(fixture):
    c,s,_=fixture;q,t=missing(c,s);r=s.completion.recovery;v=r.task_preview(t['id'])
    with pytest.raises(Exception):r.declarations(t['id'],DeclarationRecovery(revision=t['revision'],preview_hash=v['preview_hash'],confirm=True,quantities={'formula':'changed'}))
    s.save(t,'fixture_update')
    with pytest.raises(Conflict):r.declarations(t['id'],DeclarationRecovery(revision=t['revision'],preview_hash=v['preview_hash'],confirm=True,quantities={'outputs.result.value':'输出功率'}))


def repair_task(s,q,repeat=1,calls=1):
    t=s.get(q['items'][0]['task_id']);t.update(status='needs_clarification',calls=calls,repeat_count=repeat,
       last_report={'code':'EXPRESSION_OUTPUTS','path':'/module/expressions','message':'输出声明和表达式名称必须完全相同'},
       failure_routing={'route':'uncertain'},questions={'failure_evidence':'old question'})
    return s.save(t,'fixture_failure')


def test_queue_recovery_reclassifies_preserves_limits_history_and_idempotence(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t,items=[{'step_id':'calculate','max_calls':4}]);t=repair_task(s,q)
    r=s.completion.recovery;v=r.queue_preview(q['id'])
    assert v['items'][0]['action']=='candidate_repair' and s.get(t['id'])['status']=='needs_clarification'
    req=CompletionRecovery(revision=v['revision'],preview_hash=v['preview_hash'],confirm=True)
    result=r.recover_queue(q['id'],req);current=s.get(t['id'])
    assert current['status']=='dispatch_ready' and current['calls']==1 and current['call_limit']==4
    assert current['repeat_count']==1 and current['last_report']==t['last_report']
    assert current['plan']==t['plan'] and current['events'][:-1]==t['events']
    assert r.recover_queue(q['id'],req)['revision']==result['revision']


@pytest.mark.parametrize('repeat,calls',[(2,1),(1,4)])
def test_stops_not_reset_by_recovery(fixture,repeat,calls):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t,items=[{'step_id':'calculate','max_calls':4}]);t=repair_task(s,q,repeat,calls)
    r=s.completion.recovery;v=r.queue_preview(q['id']);assert v['items'][0]['action']=='stopped'
    r.recover_queue(q['id'],CompletionRecovery(revision=v['revision'],preview_hash=v['preview_hash'],confirm=True))
    assert s.get(t['id'])==t


def test_stale_child_revision_and_unknown_delivery_block_recovery(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t);t=repair_task(s,q)
    r=s.completion.recovery;v=r.queue_preview(q['id']);s.save(t,'fixture_concurrent_change')
    with pytest.raises(Conflict):r.recover_queue(q['id'],CompletionRecovery(revision=v['revision'],preview_hash=v['preview_hash'],confirm=True))
    t=s.get(t['id']);t['status']='delivery_unknown';s.save(t,'fixture_unknown')
    v=r.queue_preview(q['id'])
    with pytest.raises(Conflict):r.recover_queue(q['id'],CompletionRecovery(revision=v['revision'],preview_hash=v['preview_hash'],confirm=True))


def test_declaration_revision_cannot_clear_repeat_stop(fixture):
    c,s,_=fixture;q,t=missing(c,s);t.update(repeat_count=2,calls=1,call_limit=4);t=s.save(t,'fixture_stop')
    r=s.completion.recovery;v=r.task_preview(t['id'])
    child=r.declarations(t['id'],DeclarationRecovery(revision=t['revision'],preview_hash=v['preview_hash'],confirm=True,quantities={'outputs.result.value':'输出功率'}))
    assert child['status']=='paused' and child['repeat_count']==2 and child['calls']==1


def test_large_declaration_recovery_uses_complete_paged_reference(fixture):
    import json
    from pe_workflow.tool_completion import formula_declarations, preparation_request
    from pe_workflow.models import DesignPlan
    from pe_workflow.design_context_pages import read
    c,s,_=fixture;q,t=missing(c,s)
    t['plan']['steps'][0]['outputs']['result']['fields']['value']['description']='large verified declaration '*700
    t=s.save(t,'large_declaration_fixture')
    r=s.completion.recovery;v=r.task_preview(t['id'])
    child=r.declarations(t['id'],DeclarationRecovery(revision=t['revision'],preview_hash=v['preview_hash'],confirm=True,quantities={'outputs.result.value':'output power'}))
    saved=s.get(child['id']);before=copy.deepcopy(saved['plan'])
    request=preparation_request(saved)
    ref=json.loads(request.declarations)['declaration_source']
    assert ref['section']=='step' and ref['step_id']=='calculate'
    assert len(request.declarations)<=10000
    chunks=[];offset=0
    while True:
        part=read(s,saved,'step',offset,ref['step_id']);chunks.append(part['text'])
        if part['next_offset'] is None:break
        offset=part['next_offset']
    assert json.loads(''.join(chunks))['outputs']['result']['fields']['value']['description']==before['steps'][0]['outputs']['result']['fields']['value']['description']
    assert formula_declarations(DesignPlan.model_validate(saved['plan']).steps[0])==request.declarations
    s.completion.tick();after=s.get(child['id'])
    assert after['status']=='dispatch_ready' and after['calls']==0 and after['call_limit']==saved['call_limit']
    assert after['plan']==before and after['completion_draft']['examples']==saved['completion_draft']['examples']


def test_internal_request_error_is_platform_fault_without_model_calls(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t)
    task=s.get(q['items'][0]['task_id']);task['completion_draft']['evidence']='x'*5000
    s.save(task,'invalid_internal_request');s.completion.tick();failed=s.get(task['id'])
    assert failed['calls']==0 and failed['status']=='paused'
    assert failed['last_report']['code']=='COMPLETION_PREPARATION_FAILED'
    assert failed['failure_routing']['route']=='platform_fault'
    assert not failed['failure_routing']['automatic']
    assert s.completion.recovery.task_preview(task['id'])['action']=='keep'


def test_legacy_preparation_recovery_preserves_queue_and_candidates(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t)
    task=s.get(q['items'][0]['task_id']);task.update(status='paused',repeat_count=1,last_report={
        'code':'DESIGN_VALIDATION','message':"1 validation error for FormulaRequest\ndeclarations\n  String should have at most 10000 characters [type=string_too_long]",'path':'/'})
    task=s.save(task,'legacy_preparation_fixture');original=copy.deepcopy(task)
    other=s.get(q['items'][1]['task_id']);other.update(status='formula_candidate',candidate={'sentinel':'keep'},calls=1)
    other=s.save(other,'passed_fixture')
    r=s.completion.recovery;v=r.queue_preview(q['id'])
    assert v['items'][0]['action']=='preparation_retry'
    assert v['items'][0]['routing']['route']=='platform_fault'
    req=CompletionRecovery(revision=v['revision'],preview_hash=v['preview_hash'],confirm=True)
    restored=r.recover_queue(q['id'],req);s.completion.tick();current=s.get(task['id'])
    assert current['status']=='dispatch_ready' and current['calls']==0 and current['call_limit']==original['call_limit']
    assert current['plan']==original['plan'] and s.get(other['id'])==other
    assert r.recover_queue(q['id'],req)['revision']==restored['revision']
    # A schema failure after a model call must never be silently reinterpreted.
    current.update(status='paused',calls=1,last_report=original['last_report']);s.save(current,'agent_failure_fixture')
    assert r.task_preview(task['id'])['action']!='preparation_retry'
