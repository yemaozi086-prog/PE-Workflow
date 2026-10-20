import copy
import pytest
from test_designs import fixture,plan
from test_design_lifecycle import active
from pe_workflow.storage import Conflict

def pending(c,s,payload):
    t=active(c,s);receipt=s.receipts.capture(t,payload)
    return t,receipt

def test_interrupted_receipt_rechecks_without_model_or_budget(fixture):
    c,s,store=fixture;raw={'kind':'plan','plan':plan()};before=copy.deepcopy(raw)
    t,r=pending(c,s,raw);t=s.receipts.reconcile(t)
    assert t['status']=='paused' and t['last_report']['code']=='CANDIDATE_PROCESSING_INTERRUPTED'
    assert s.public(t)['recovery_actions'][0]['id']=='recheck-received-candidate'
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/continue',json={'revision':t['revision']})
    result=c.post('/api/workflow-designs/'+t['id']+'/recheck-received-candidate',json={'revision':t['revision']})
    assert result.status_code==200,result.text
    assert result.json()['candidate_processing']['status']!='validating'
    after=s.get(t['id']);assert after['status']=='awaiting_confirmation'
    assert after['calls']==1 and after['call_limit']==4 and not store.runs()
    assert s.receipts.list(t['id'])['items'][0]['candidate']==before
    assert s.receipts.list(t['id'])['items'][0]['processed']
    assert len(store.modules())==1

def test_live_validation_not_interrupted_or_replayed(fixture):
    c,s,_=fixture;t,r=pending(c,s,{'kind':'plan','plan':plan()})
    with s.receipts.processing(t['id'],t['call_id']):
        assert s.receipts.state(t)['status']=='validating'
        assert s.receipts.reconcile(t)['revision']==t['revision']
        with pytest.raises(Conflict):
            c.post('/api/workflow-designs/'+t['id']+'/result',json={'call_id':t['call_id'],'token':t['token'],'kind':'plan','plan':plan()})
    assert s.receipts.state(t)['status']=='interrupted'

def test_bad_saved_envelope_recheck_does_not_schedule_model(fixture):
    c,s,_=fixture;t,r=pending(c,s,{'kind':'module','module':'not a module object'})
    t=s.receipts.reconcile(t)
    result=c.post('/api/workflow-designs/'+t['id']+'/recheck-received-candidate',json={'revision':t['revision']})
    assert result.status_code==200,result.text
    after=s.get(t['id']);assert after['status']=='paused' and after['calls']==1 and after['call_limit']==4
    assert after['last_report']['code']=='RESULT_ENVELOPE'
    assert after['failure_routing']['stop_reason']=='revalidation_only'

def test_restart_loses_handler_but_preserves_receipt(fixture):
    from pe_workflow.candidate_receipts import Receipts
    c,s,_=fixture;t,r=pending(c,s,{'kind':'plan','plan':plan()})
    # A new service instance/process has no live request; the durable receipt remains.
    s.receipts=Receipts(s)
    t=s.receipts.reconcile(t)
    assert t['status']=='paused' and s.receipts.pending(t)[0]['payload_hash']==r['payload_hash']

def test_multiple_saved_receipts_require_exact_selection(fixture):
    c,s,_=fixture;t,r=pending(c,s,{'kind':'plan','plan':plan()})
    s.receipts.capture(t,{'kind':'blocked','explanation':'other original candidate'})
    t=s.receipts.reconcile(t)
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/recheck-received-candidate',json={'revision':t['revision']})
    result=c.post('/api/workflow-designs/'+t['id']+'/recheck-received-candidate',json={'revision':t['revision'],'receipt_hash':r['payload_hash']})
    assert result.status_code==200 and result.json()['status']=='awaiting_confirmation'

def test_stop_before_interruption_does_not_enable_replay(fixture):
    c,s,_=fixture;t,r=pending(c,s,{'kind':'plan','plan':plan()})
    t=c.post('/api/workflow-designs/'+t['id']+'/stop',json={'revision':t['revision']}).json()
    assert s.receipts.reconcile(s.get(t['id']))['revision']==t['revision']
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/recheck-received-candidate',json={'revision':t['revision']})
