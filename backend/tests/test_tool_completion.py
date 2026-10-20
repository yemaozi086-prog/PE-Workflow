import copy
import pytest
from test_designs import fixture,callback,plan,tool,create
from pe_workflow.models import Binding,ToolCompletionRegister
from pe_workflow.storage import Conflict
from pe_workflow.engine import WorkflowError


def source(c,s,count=2):
    p=plan(False);p['external_inputs']=copy.deepcopy(p['steps'][0]['inputs'])
    step=p['steps'][0];step['input_sources']={'data':Binding(source='input',port='data').model_dump()}
    for i in range(1,count):
        x=copy.deepcopy(step);x['id']='calculate'+str(i);x['title']='计算'+str(i);p['steps'].append(x)
    return callback(c,s,create(c),kind='plan',plan=p)


def queue(c,t,**changes):
    body={'revision':t['revision'],'confirm_plan':True,'concurrency':1,'items':[{'step_id':x['id'],'max_calls':1} for x in t['plan']['steps']]}
    body.update(changes)
    response=c.post('/api/workflow-designs/'+t['id']+'/tool-completion',json=body)
    assert response.status_code==200,response.text
    return response.json(),body


def candidate(c,s,q,index):
    s.completion.tick();task=s.get(q['items'][index]['task_id'])
    m=tool().model_dump();m['id']='completion_'+str(index)
    result=callback(c,s,task,kind='module',module=m)
    assert result['status']=='formula_candidate',result
    return result


def test_context_is_automatic_and_not_independent_validation(fixture):
    c,s,_=fixture;t=source(c,s)
    ctx=c.get('/api/workflow-designs/'+t['id']+'/tool-completion').json()
    assert len(ctx['steps'])==2
    adopted=ctx['steps'][0]
    assert adopted['formula']=='y=2x' and adopted['declarations']['inputs']==t['plan']['steps'][0]['inputs']
    assert adopted['verification']['status']=='not_provided' and not adopted['missing']
    assert adopted['source']['revision']==t['revision']


def test_sequential_generation_does_not_register_and_freezes_source(fixture):
    c,s,store=fixture;t=source(c,s);before=copy.deepcopy(s.get(t['id']))
    q,body=queue(c,t)
    same=c.post('/api/workflow-designs/'+t['id']+'/tool-completion',json=body).json()
    assert q['id']==same['id']
    s.completion.tick()
    assert s.get(q['items'][0]['task_id'])['status']=='dispatch_ready'
    assert s.get(q['items'][1]['task_id'])['status']=='completion_pending'
    s.completion.tick()
    assert s.get(q['items'][1]['task_id'])['status']=='completion_pending'
    candidate(c,s,q,0)
    assert len(store.modules())==1 and s.get(t['id'])==before
    s.completion.tick()
    assert s.get(q['items'][1]['task_id'])['status']=='dispatch_ready'


def test_partial_registration_one_derived_plan_and_idempotent_receipt(fixture):
    c,s,store=fixture;t=source(c,s);q,_=queue(c,t)
    candidate(c,s,q,0)
    second=s.get(q['items'][1]['task_id']);second.update(status='blocked',calls=1,questions={'evidence':'missing'})
    s.save(second,'test_blocked');s.completion.tick()
    q=s.completion.public(s.completion.get(q['id']))
    req={'revision':q['revision'],'task_ids':[q['items'][0]['task_id']],'allow_unverified':True}
    result=c.post('/api/tool-completion-queues/'+q['id']+'/register',json=req).json()
    child=result['design'];assert child['parent_id']==t['id']
    assert child['plan']['steps'][0]['module_ref']=='completion_0@1'
    assert child['plan']['steps'][1]['module_ref'] is None
    assert child['stage_budgets']['formula:calculate1']['used']==1
    assert result['queue']['status']=='review' and result['queue']['items'][1]['status']=='blocked'
    again=c.post('/api/tool-completion-queues/'+q['id']+'/register',json=req).json()
    assert again['design']['id']==child['id'] and len(store.modules())==2
    assert s.get(t['id'])['plan']['steps'][0]['module_ref'] is None


def test_registration_rolls_back_all_versions_on_collision(fixture):
    c,s,store=fixture;t=source(c,s);q,_=queue(c,t)
    candidate(c,s,q,0);candidate(c,s,q,1);s.completion.tick()
    occupied=tool().model_copy(deep=True);occupied.id='completion_1';occupied.description='different immutable content'
    store.add_module(occupied)
    q=s.completion.get(q['id'])
    with pytest.raises(Conflict):
        s.completion.register(q['id'],ToolCompletionRegister(revision=q['revision'],task_ids=[i['task_id'] for i in q['items']],allow_unverified=True))
    assert not any(m['id']=='completion_0' for m in store.modules())
    assert s.completion.get(q['id']).get('result_design_id') is None


def test_missing_information_pauses_only_affected_generation(fixture):
    c,s,_=fixture;t=source(c,s);old=s.get(t['id']);old['plan']['steps'][0]['formula_latex']=''
    t=s.save(old,'test_missing_formula');q,_=queue(c,t)
    s.completion.tick();a=s.get(q['items'][0]['task_id'])
    assert a['status']=='blocked' and a['calls']==0 and a['questions']
    s.completion.tick();assert s.get(q['items'][1]['task_id'])['status']=='dispatch_ready'


def test_no_budget_reset_by_duplicate_queue(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t)
    with pytest.raises(Conflict):queue(c,t,items=[{'step_id':'calculate','max_calls':4}])


def test_existing_formula_cannot_be_rewritten_and_samples_prechecked(fixture):
    c,s,_=fixture;t=source(c,s)
    with pytest.raises(WorkflowError):queue(c,t,items=[{'step_id':'calculate','formula':'y=3x'}])
    with pytest.raises(WorkflowError):queue(c,t,items=[{'step_id':'calculate','examples':[{'inputs':{},'outputs':{}}]}])


def test_pause_preserves_remaining_budget_and_pending_jobs(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t)
    paused=c.post('/api/tool-completion-queues/'+q['id']+'/pause',json={'revision':q['revision']}).json()
    s.completion.tick()
    assert paused['status']=='paused' and not paused['running']
    assert all(s.get(i['task_id'])['status']=='completion_pending' for i in q['items'])
    resumed=c.post('/api/tool-completion-queues/'+q['id']+'/resume',json={'revision':paused['revision']}).json()
    assert resumed['approved_call_limit']==2 and resumed['total_calls']==0
    s.completion.tick();assert s.get(q['items'][0]['task_id'])['status']=='dispatch_ready'


def test_declared_supplements_fill_missing_only_in_frozen_copy(fixture):
    c,s,_=fixture;t=source(c,s,1);old=s.get(t['id']);old['plan']['steps'][0]['formula_latex']='';old['plan']['steps'][0]['evidence']=[]
    t=s.save(old,'test_missing');q,_=queue(c,t,items=[{'step_id':'calculate','formula':'y=2x','evidence':'user supplied formula','max_calls':1}])
    s.completion.tick();job=s.get(q['items'][0]['task_id'])
    assert job['status']=='dispatch_ready' and job['plan']['steps'][0]['formula_latex']=='y=2x'
    assert s.get(t['id'])['plan']['steps'][0]['formula_latex']==''
    assert job['formula_request']['examples']==[]


def test_derived_plan_recovers_the_same_partial_queue(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t)
    candidate(c,s,q,0)
    second=s.get(q['items'][1]['task_id']);second.update(status='blocked');s.save(second,'test_blocked');s.completion.tick()
    q=s.completion.get(q['id'])
    result=s.completion.register(q['id'],ToolCompletionRegister(revision=q['revision'],task_ids=[q['items'][0]['task_id']],allow_unverified=True))
    context=s.completion.context(result['design']['id'])
    assert context['queue']['id']==q['id']
    assert len(context['steps'])==1


def test_unknown_delivery_does_not_start_next_job(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t)
    first=s.get(q['items'][0]['task_id']);first['status']='delivery_unknown';s.save(first,'test_delivery_unknown')
    s.completion.tick();assert s.get(q['items'][1]['task_id'])['status']=='completion_pending'


def test_same_family_candidates_reserve_distinct_versions_and_lineage(fixture):
    c,s,store=fixture;t=source(c,s);old=s.get(t['id'])
    for step in old['plan']['steps']:step['derives_from']='double@1'
    t=s.save(old,'test_family');q,_=queue(c,t)
    targets=[s.get(i['task_id'])['completion_registration_target'] for i in q['items']]
    assert [r['version'] for r in targets]==[2,3]
    for index,item in enumerate(q['items']):
        s.completion.tick();job=s.get(item['task_id'])
        assert job['registration_target']==targets[index]
        m=tool().model_dump();m['version']=targets[index]['version'];m['description']='role '+str(index)
        job=callback(c,s,job,kind='module',module=m)
        assert job['status']=='formula_candidate',job
    s.completion.tick();q=s.completion.get(q['id'])
    result=s.completion.register(q['id'],ToolCompletionRegister(revision=q['revision'],task_ids=[i['task_id'] for i in q['items']],allow_unverified=True))
    assert [x['module_ref'] for x in result['design']['plan']['steps']]==['double@2','double@3']
    with store.connect() as con:
        relations=[__import__('json').loads(r[0]) for r in con.execute('SELECT document FROM module_relations ORDER BY version')]
    assert len(relations)==2 and all(r['parent_version']==1 for r in relations)
