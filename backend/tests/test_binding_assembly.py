import copy,json
import pytest
from test_designs import fixture,callback,create,plan,tool
from pe_workflow.models import Binding,BindingAssemblyConfirm
from pe_workflow.storage import Conflict
from pe_workflow.design_context_pages import page,compact,index,index_data,PAGE_SIZE


def explicit(c,s):
    p=plan();p['external_inputs']=copy.deepcopy(p['steps'][0]['inputs'])
    p['steps'][0]['input_sources']={'data':Binding(source='input',port='data').model_dump()}
    p['steps'][0]['input_checks']=['data["value"] >= 0']
    return callback(c,s,create(c),kind='plan',plan=p)


def test_exact_assembly_preview_confirm_without_model_and_idempotence(fixture):
    c,s,store=fixture;t=explicit(c,s);before=copy.deepcopy(s.get(t['id']))
    v=c.get('/api/workflow-designs/'+t['id']+'/binding-preview').json()
    assert v['valid'] and s.get(t['id'])==before
    assert v['workflow']['nodes'][0]['inputs']==t['plan']['steps'][0]['input_sources']
    assert v['workflow']['nodes'][0]['input_checks']==['data["value"] >= 0']
    assert v['workflow']['inputs']==t['plan']['external_inputs']
    req={'revision':v['revision'],'preview_hash':v['preview_hash'],'allow_unverified':True}
    child=c.post('/api/workflow-designs/'+t['id']+'/assemble',json=req).json()
    assert child['status']=='ready_to_save' and child['validation']['execution']=='not_run'
    assert child['assembly_origin']=='platform_explicit_bindings'
    assert child['stage_budgets']==before['stage_budgets'] and child['total_calls']==before.get('total_calls',before.get('calls',0))
    assert s.get(t['id'])['superseded_by']==child['id'] and store.workflows()==[]
    assert c.post('/api/workflow-designs/'+t['id']+'/assemble',json=req).json()['id']==child['id']
    saved=c.post('/api/workflow-designs/'+child['id']+'/publish',json={'revision':child['revision']}).json()
    assert saved['status']=='saved'


def test_confirm_explicit_plan_does_not_dispatch_model(fixture):
    c,s,_=fixture;t=explicit(c,s)
    result=c.post('/api/workflow-designs/'+t['id']+'/confirm',json={'revision':t['revision'],'allow_unverified':True}).json()
    assert result['status']=='awaiting_confirmation' and result['assembly_pending'] and result['phase']=='bind'
    assert result['calls']==t['calls']


def test_no_source_inference_even_when_only_one_possible_source(fixture):
    c,s,_=fixture;t=explicit(c,s);raw=s.get(t['id']);raw['plan']['steps'][0]['input_sources']={};s.save(raw,'fixture_missing_source')
    v=s.binding_assembly.preview(t['id'])
    assert not v['valid'] and any(x['code']=='BINDING_SOURCE_REQUIRED' for x in v['issues'])


def test_reject_stale_preview_and_contract_changes(fixture):
    c,s,_=fixture;t=explicit(c,s);v=s.binding_assembly.preview(t['id']);raw=s.get(t['id']);raw['plan']['steps'][0]['inputs']['data']['fields']['value']['unit']='A';s.save(raw,'fixture_changed')
    with pytest.raises(Conflict):s.binding_assembly.confirm(t['id'],BindingAssemblyConfirm(revision=t['revision'],preview_hash=v['preview_hash'],allow_unverified=True))
    assert not s.binding_assembly.preview(t['id'])['valid']


def test_timeout_switch_retains_budget_and_records_late_result(fixture):
    c,s,_=fixture;t=explicit(c,s);raw=s.get(t['id']);raw.update(phase='bind',status='delivery_unknown',calls=1,call_limit=4,call_id='a'*32,token='old-secret')
    t=s.save(raw,'fixture_timeout');v=s.binding_assembly.preview(t['id'])
    child=s.binding_assembly.confirm(t['id'],BindingAssemblyConfirm(revision=t['revision'],preview_hash=v['preview_hash'],allow_unverified=True))
    assert child['calls']==1 and child['call_limit']==4 and child['stage_budgets']['bind']['used']==1
    late={'call_id':'a'*32,'token':'old-secret','kind':'questions','questions':{'why':'old answer'}}
    for _ in range(2):
        r=c.post('/api/workflow-designs/'+t['id']+'/result',json=late).json()
        assert r['status']=='recorded_late_result' and r['applied'] is False
    assert s.get(child['id'])['status']=='ready_to_save'
    with s.connect() as db:
        rows=db.execute('SELECT document FROM design_late_results').fetchall()
        assert len(rows)==1 and 'old-secret' not in rows[0][0]


def test_active_call_cannot_be_silently_switched(fixture):
    c,s,_=fixture;t=explicit(c,s);raw=s.get(t['id']);raw['status']='awaiting_worker';t=s.save(raw,'fixture_active');v=s.binding_assembly.preview(t['id'])
    with pytest.raises(Conflict):s.binding_assembly.confirm(t['id'],BindingAssemblyConfirm(revision=t['revision'],preview_hash=v['preview_hash'],allow_unverified=True))


def test_paged_context_reassembles_and_never_contains_expected_answers(fixture):
    c,s,_=fixture;t=explicit(c,s);raw=s.get(t['id']);raw.update(call_id='b'*32,token='secret',formula_request={'examples':[{'inputs':{'a':1},'outputs':{'secret_expected':12345}}]})
    t=s.save(raw,'fixture_context')
    from pe_workflow.design_context_pages import read
    part=read(s,t,'schema');text=part['text']
    while part['next_offset'] is not None:
        part=read(s,t,'schema',part['next_offset']);assert len(part['text'])<=PAGE_SIZE;text+=part['text']
    assert json.loads(text)==s.context(t)['schemas']  # Schema must survive paging without dropping empty definitions
    assert 'secret_expected' not in json.dumps(read(s,t,'formula'))
    idx=index(s,t);assert 'double@1' in idx['module_refs']


def test_oversized_index_pages_share_one_hash_and_content(fixture):
    c,s,_=fixture;t=explicit(c,s);raw=s.get(t['id']);raw['frozen_modules']=[tool().model_copy(update={'id':'tool_'+str(i),'description':'x'*160}).model_dump() for i in range(150)]
    from pe_workflow.design_context_pages import read
    first=index(s,raw)['index_page'];part=first;text=part['text']
    while part['next_offset'] is not None:
        part=read(s,raw,'index',part['next_offset']);assert part['content_hash']==first['content_hash'];text+=part['text']
    assert json.loads(text)==compact(index_data(s,raw))


def test_unknown_module_returns_exact_stop_and_progress_has_no_prompts(fixture,monkeypatch):
    c,s,_=fixture;t=explicit(c,s);raw=s.get(t['id']);raw.update(call_id='c'*32,token='secret',session_id='test-session');t=s.save(raw,'fixture_session')
    url='/api/workflow-designs/'+t['id']+'/context?call_id='+('c'*32)
    result=c.get(url+'&module_ref=guessed@1&section=index',headers={'Authorization':'Bearer secret'}).json()
    assert result['code']=='MODULE_NOT_FOUND' and result['stop_required']
    current=s.get(t['id']);assert current['status']=='paused' and current['worker_result_closed']
    stopped=c.get(url+'&module_ref=double@1&section=index',headers={'Authorization':'Bearer secret'}).json()
    assert stopped['code']=='CALL_CLOSED'
    from pe_workflow import automation
    monkeypatch.setattr(automation,'rpc',lambda *args:{'items':[{'sessionId':'test-session','running':True,'projections':{'asOfSeq':10}}]})
    progress=s.progress.refresh(t['id']);assert progress['running_reported'] is True and progress['last_tool']=='design_module'
    assert 'token' not in progress and 'request' not in progress
    monkeypatch.setattr(automation,'rpc',lambda *args:(_ for _ in ()).throw(RuntimeError('private detail')))
    s.progress.merge(t,{'checked_epoch':0});progress=s.progress.refresh(t['id'])
    assert progress['observation']=='query_failed' and 'private detail' not in progress['query_error']


def test_stop_records_late_result_but_timeout_does_not_cancel(fixture):
    c,s,_=fixture;t=explicit(c,s);raw=s.get(t['id']);raw.update(status='delivery_unknown',call_id='d'*32,token='late-secret')
    t=s.save(raw,'fixture_unknown')
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/continue',json={'revision':t['revision']})
    stopped=c.post('/api/workflow-designs/'+t['id']+'/stop',json={'revision':t['revision']}).json()
    assert stopped['worker_result_closed'] and stopped['calls']==t['calls']
    result=c.post('/api/workflow-designs/'+t['id']+'/result',json={'call_id':'d'*32,'token':'late-secret','kind':'questions','questions':{'x':'late'}}).json()
    assert result['status']=='recorded_late_result'
    assert s.get(t['id'])['status']=='paused'
