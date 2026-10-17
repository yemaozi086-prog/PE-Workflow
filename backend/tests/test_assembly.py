import asyncio
import copy
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from pe_workflow.assembly import Coordinator, install, public
from pe_workflow.models import AssemblyTaskRequest,AssemblyWorkerResult
from pe_workflow.storage import Store,Conflict
from pe_workflow.worker import main
from test_scenarios import sample_request


@pytest.fixture
def setup(tmp_path):
    data,modules=sample_request();store=Store(tmp_path/'assembly.sqlite')
    for m in modules.values():store.seed_module(m)
    c=Coordinator(lambda:store,lambda:modules,lambda payload,timeout:main(payload))
    request={k:v for k,v in data.items() if k!='plan'}
    return c,store,data,modules,request


def awaiting(c,request):
    t=c.create(AssemblyTaskRequest.model_validate(request))
    t.update(status='awaiting_worker',call_id='c'*32,token='secret-token',generation_count=1,batch_calls=1)
    return c.journal().save(t,{'actor':'platform','event':'test_dispatch'})


def submit(c,t,plan):
    return c.result(t,AssemblyWorkerResult(call_id=t['call_id'],token=t['token'],kind='plan',plan=plan,explanation='按声明装配，不判断可行性'))


def test_plan_executes_reference_passes_and_atomic_publication(setup):
    c,store,data,modules,req=setup;t=awaiting(c,req)
    submit(c,t,data['plan']);t=c.journal().get(t['id']);asyncio.run(c.validate(t))
    done=c.journal().get(t['id'])
    assert done['status']=='completed'
    result=done['result'];assert result['validation']['reference']=='passed'
    assert store.workflow(result['workflow']['id'])['revision']==1
    run=store.run(result['run']['id']);assert run['snapshot']['modules']
    replay=main(run['snapshot']);assert replay['outputs']==run['outputs']
    asyncio.run(c.validate(done));assert len(store.workflows())==1
    assert 'secret-token' not in str(public(done))
    assert 'secret-token' not in str(c.journal().events(t['id']))


def test_repeated_diagnosis_stops_without_publication(setup):
    c,store,data,modules,req=setup;t=awaiting(c,req)
    bad=copy.deepcopy(data['plan']);bad['steps'][1]['module_version']=99
    for attempt in range(2):
        submit(c,t,bad);asyncio.run(c.validate(c.journal().get(t['id'])))
        t=c.journal().get(t['id'])
        if attempt==0:
            assert t['status']=='dispatch_ready'
            t.update(status='awaiting_worker',callback_hash=None,batch_calls=2)
            t=c.journal().save(t,{'actor':'platform','event':'test_dispatch'})
    assert t['status']=='paused' and t['repeat_count']==2
    assert not store.workflows()
    assert t['last_report']['diagnostics'][0]['code']=='INCOMPATIBLE_PROFILE'


def test_reference_failure_never_publishes(setup):
    c,store,data,modules,req=setup
    def wrong(payload,timeout):
        result=main(payload)
        if payload.get('mode')!='assembly_reference':result['outputs']['loss']['cap1_loss'][0]+=1
        return result
    c.worker=wrong;t=awaiting(c,req);submit(c,t,data['plan']);asyncio.run(c.validate(c.journal().get(t['id'])))
    assert c.journal().get(t['id'])['last_report']['diagnostics'][0]['code']=='REFERENCE_MISMATCH'
    assert not store.workflows()


def test_duplicate_callback_is_idempotent(setup):
    c,_,data,_,req=setup;t=awaiting(c,req)
    submit(c,t,data['plan'])
    assert submit(c,c.journal().get(t['id']),data['plan'])['duplicate']
    assert len([e for e in c.journal().events(t['id']) if e['event']=='worker_result'])==1


def client_for(setup):
    c,store,data,modules,req=setup;app=FastAPI()
    def problem(code,message,status=422):raise HTTPException(status,detail={'code':code,'message':message})
    coordinator=install(app,lambda:store,lambda:modules,lambda p,t:main(p),problem)
    return TestClient(app),coordinator,req,data


def test_clarification_requires_all_answers_and_preserves_snapshot(setup):
    client,c,req,_=client_for(setup);req['facts']={}
    created=client.post('/api/assembly-tasks',json=req).json();key=created['id']
    assert created['status']=='needs_clarification' and created['generation_count']==0
    assert client.post('/api/assembly-tasks/'+key+'/answer',json={'answers':{'modulation':'EPS'}}).status_code==422
    expected={'modulation':'EPS','scope':'single_workpoint','assessment':'loss_only','upstream_constraints':'not_evaluated','control':'finite_batch'}
    answered=client.post('/api/assembly-tasks/'+key+'/answer',json={'answers':expected}).json()
    assert answered['status']=='dispatch_ready'
    assert answered['context_revisions'][0]['facts']=={}
    assert answered['context']['facts']==expected


def test_stop_rotates_credentials_and_continue_has_three_call_budget(setup):
    client,c,req,_=client_for(setup);t=awaiting(c,req);key=t['id']
    assert client.post('/api/assembly-tasks/'+key+'/stop',json={}).json()['status']=='stopped'
    assert client.get('/api/assembly-tasks/'+key+'/context',params={'call_id':t['call_id']},headers={'Authorization':'Bearer '+t['token']}).status_code==403
    stopped=c.journal().get(key);stopped['status']='paused';c.journal().save(stopped,{'actor':'platform','event':'test_pause'})
    continued=client.post('/api/assembly-tasks/'+key+'/continue',json={'approved':True}).json()
    assert continued['batch_call_limit']==3 and continued['batch_calls']==0


def test_optimistic_task_update_rejects_stale_user_actions(setup):
    c,_,_,_,req=setup;t=c.create(AssemblyTaskRequest.model_validate(req))
    c.journal().save(t,{'actor':'user','event':'first'})
    with pytest.raises(Conflict):c.journal().save(t,{'actor':'user','event':'stale'})


def test_explicit_config_refresh_preserves_old_context_and_three_call_budget(setup):
    client,c,req,_=client_for(setup);t=awaiting(c,req)
    t['status']='paused';t['configuration']['prompt_hash']='old-hash'
    t=c.journal().save(t,{'actor':'platform','event':'test_pause'})
    old_context=copy.deepcopy(t['context'])
    updated=client.post('/api/assembly-tasks/'+t['id']+'/continue',json={'approved':True,'refresh_configuration':True}).json()
    assert updated['batch_call_limit']==3 and updated['context_revisions'][-1]==old_context
    assert updated['configuration']['prompt_hash']!='old-hash'
    assert 'binding_format' in updated['context']
    assert c.journal().events(t['id'])[-1]['configuration_change']['before']['prompt_hash']=='old-hash'


def test_user_can_reduce_initial_call_budget_without_expanding_permissions(setup):
    c,_,_,_,req=setup
    req['max_calls']=1
    t=awaiting(c,req)
    assert t['batch_call_limit']==1 and t['configuration']['initial_call_limit']==1
    c.result(t,AssemblyWorkerResult(call_id=t['call_id'],token=t['token'],kind='plan',plan={}))
    asyncio.run(c.validate(c.journal().get(t['id'])))
    assert c.journal().get(t['id'])['status']=='paused'
