import asyncio
import copy
import time
import pytest
from fastapi.testclient import TestClient
from pe_workflow import api
from pe_workflow.quick_chat import QuickChat, text_events
from pe_workflow.storage import Store, Conflict


def bundle(t):
    port={'type':'flat_dict','fields':{'x':{'type':'number','unit':'W','quantity':'power'}}}
    m={'id':t['workflow_id']+'_scale','version':1,'name':'功率乘二','provenance':'测试公式 y=2x',
        'contract_version':'3.0','implementation':'python_subset','inputs':{'data':port},'outputs':{'result':port},
        'source':"def compute(inputs):\n    return {'result': {'x': inputs['data']['x'] * 2}}\n"}
    w={'schema_version':'3.0','id':t['workflow_id'],'name':'测试工作流','inputs':{'data':port},
        'nodes':[{'id':'scale','module_id':m['id'],'module_version':1,'inputs':{'data':{'source':'input','port':'data'}}}],
        'outputs':{'answer':{'source':'node','node':'scale','port':'result'}},'output_ports':{'answer':port}}
    return {'workflow':w,'modules':[m],'summary':'已实现用户公式，等待运行测试'}


@pytest.fixture
def setup(tmp_path,monkeypatch):
    store=Store(tmp_path/'quick.sqlite');monkeypatch.setattr(api,'store',store)
    monkeypatch.setenv('PE_DSH_ENABLED','0')
    service=api.app.state.quick_chat
    monkeypatch.setattr(service,'enabled',lambda:True)
    calls=[]
    def rpc(method,payload,call_id):
        calls.append((method,payload))
        if method=='session.list':return {'items':[]}
        return {}
    monkeypatch.setattr(service,'rpc',rpc)
    with TestClient(api.app) as c:yield c,service,store,calls


def started(service):
    t=service.create();t.update(turn_id='b'*32,token='test-token',status='working',started=time.time(),checks=0,check_signatures=[])
    return service.save(t)


def credential(t,op,**values):return {'turn_id':t['turn_id'],'token':t['token'],'operation':op,**values}


def test_context_exposes_runtime_contract_and_never_credentials(setup):
    c,s,store,calls=setup;t=started(s)
    for section in ('index','schemas','rules','modules','example','current','diagnostics'):
        r=c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(t,'context',section=section))
        assert r.status_code==200,r.text
        assert 'test-token' not in r.text
    assert not calls


def test_complete_bundle_atomic_save_run_and_versioned_repair(setup):
    c,s,store,calls=setup;t=started(s);b=bundle(t);url='/api/quick-chats/'+t['id']+'/tool'
    r=c.post(url,json=credential(t,'check',bundle=b));assert r.json()['validation']['valid'],r.text
    r=c.post(url,json=credential(t,'submit',bundle=b));assert r.json()['status']=='saved',r.text
    assert store.workflow(t['workflow_id'])['revision']==1
    assert c.get('/api/workspace/workflows').json()['items'][0]['origin']['verification']=='unverified_draft'
    run=c.post('/api/runs',json={'workflow':b['workflow'],'inputs':{'data':{'x':3}}}).json()
    assert run['outputs']=={'answer':{'x':6}},run
    assert c.post(url,json=credential(t,'submit',bundle=b)).status_code==409
    t=s.get(t['id']);t.update(turn_id='c'*32,status='working');t=s.save(t)
    b['modules'][0]['version']=2;b['modules'][0]['source']=b['modules'][0]['source'].replace('* 2','* 3')
    b['workflow']['nodes'][0]['module_version']=2
    r=c.post(url,json=credential(t,'submit',bundle=b));assert r.json()['revision']==2,r.text
    assert store.workflow(t['workflow_id'],1)['workflow']['nodes'][0]['module_version']==1
    assert not calls


def test_unsafe_module_does_not_install_or_save_graph(setup):
    c,s,store,_=setup;t=started(s);b=bundle(t);b['modules'][0]['source']="def compute(inputs):\n    return open('secret')\n"
    r=c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(t,'submit',bundle=b))
    assert r.json()['status']=='not_saved',r.text
    assert not store.modules() and not store.workflow(t['workflow_id'])
    assert s.get(t['id'])['pending_bundle']==b


def test_invalid_binding_saved_as_explicit_invalid_draft(setup):
    c,s,store,_=setup;t=started(s);b=bundle(t);b['workflow']['nodes'][0]['inputs']={}
    r=c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(t,'submit',bundle=b)).json()
    assert r['status']=='saved' and not r['validation']['valid']
    assert store.workflow(t['workflow_id'])


def test_namespace_and_stale_graph_leave_no_partial_modules(setup):
    c,s,store,_=setup;t=started(s);b=bundle(t)
    wrong=copy.deepcopy(b);wrong['modules'][0]['id']='unrelated'
    assert c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(t,'submit',bundle=wrong)).json()['status']=='not_saved'
    from pe_workflow.models import Workflow
    store.save(Workflow.model_validate(b['workflow']),{},0)
    r=c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(t,'submit',bundle=b)).json()
    assert r['status']=='not_saved' and not store.modules()
    assert store.workflow(t['workflow_id'])['revision']==1


def test_active_editor_draft_cannot_be_overwritten(setup):
    c,s,store,_=setup;t=started(s);b=bundle(t)
    from pe_workflow.models import Workflow
    w=Workflow.model_validate(b['workflow']);store.save(w,{},0)
    t['artifact_revision']=1;t=s.save(t)
    c.put('/api/workspace/workflows/'+w.id+'/draft',json={})  # bad requests must not create a draft
    with store.connect() as con:con.execute('INSERT INTO workflow_drafts VALUES(?,?,?,?,?)',(w.id,1,1,'{}','now'))
    r=c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(t,'submit',bundle=b)).json()
    assert r['status']=='not_saved' and not store.modules()


def test_same_error_twice_stops_prechecks_without_model_calls(setup):
    c,s,store,calls=setup;t=started(s);b=bundle(t);b['workflow']['nodes'][0]['inputs']={}
    url='/api/quick-chats/'+t['id']+'/tool'
    for _ in range(2):assert c.post(url,json=credential(t,'check',bundle=b)).json()['status']=='checked'
    assert c.post(url,json=credential(t,'check',bundle=b)).json()['status']=='check_limit'
    assert not calls and not store.workflows()


def test_send_once_and_stop_fences_late_delivery(setup):
    c,s,store,calls=setup;t=c.post('/api/quick-chats',json={}).json()
    r=c.post('/api/quick-chats/'+t['id']+'/messages',json={'revision':t['revision'],'text':'计算功率'});assert r.status_code==200
    time.sleep(.1);raw=s.get(t['id']);assert len([m for m,p in calls if m=='session.prompt'])==1
    assert c.post('/api/quick-chats/'+t['id']+'/messages',json={'revision':raw['revision'],'text':'duplicate'}).status_code==409
    assert 'token' not in c.get('/api/quick-chats/'+t['id']).json()
    raw=s.get(t['id']);c.post('/api/quick-chats/'+t['id']+'/stop',json={'revision':raw['revision']})
    assert c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(raw,'submit',bundle=bundle(raw))).status_code==409


def test_text_projection_omits_reasoning_and_old_turn():
    history={'events':[{'event':{'type':'assistant/message','seq':1,'time':1000,'data':{'message':{'content':[{'type':'text','text':'old'}]}}}},
        {'event':{'type':'assistant/message','seq':2,'time':3000,'data':{'message':{'content':[{'type':'reasoning','text':'private'},{'type':'text','text':'用户可见答案'}]}}}}]}
    assert text_events(history,2)==[{'id':'dsh-2','role':'assistant','text':'用户可见答案'}]


def test_reconcile_ended_session_allows_followup_without_auto_retry(setup,monkeypatch):
    c,s,store,calls=setup;t=started(s);t['session_id']='test-session';t=s.save(t)
    def rpc(method,payload,cid):
        calls.append((method,payload))
        if method=='session.list':return {'items':[{'sessionId':'test-session','running':False}]}
        return {'events':[{'event':{'type':'turn/end','time':(t['started']+1)*1000}},{'event':{'type':'assistant/message','seq':7,'time':(t['started']+1)*1000,'data':{'message':{'content':[{'type':'text','text':'请明确热阻范围'}]}}}}]}
    monkeypatch.setattr(s,'rpc',rpc)
    r=c.get('/api/quick-chats/'+t['id']).json()
    assert r['status']=='idle' and r['messages'][-1]['text']=='请明确热阻范围'
    assert not any(m=='session.prompt' for m,_ in calls)


def test_staged_small_artifacts_use_same_validator_and_atomic_delivery(setup):
    c,s,store,calls=setup;t=started(s);b=bundle(t);url='/api/quick-chats/'+t['id']+'/tool'
    assert c.post(url,json=credential(t,'draft',action='put_module',module=b['modules'][0])).json()['status']=='staged'
    assert not store.modules() and not store.workflows()
    assert c.post(url,json=credential(t,'draft',action='put_workflow',workflow=b['workflow'],summary=b['summary'])).json()['status']=='staged'
    assert c.post(url,json=credential(t,'draft',action='check')).json()['validation']['valid']
    assert c.post(url,json=credential(t,'draft',action='submit')).json()['status']=='saved'
    assert not s.get(t['id'])['staged_modules']
    assert not calls


def test_grid_batch_and_subgraph_complete_delivery(setup):
    from test_batch_text import text_scan
    c,s,store,_=setup;t=started(s);_,registry,w=text_scan()
    names={m.id:t['workflow_id']+'_'+m.id for m in registry.values()}
    modules=[m.model_copy(update={'id':names[m.id]}).model_dump() for m in registry.values()]
    doc=w.model_dump();doc['id']=t['workflow_id']
    def rename(graph):
        for n in graph['nodes']:
            if n.get('module_id') in names:n['module_id']=names[n['module_id']]
            if n.get('body'):rename(n['body'])
    rename(doc)
    r=c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(t,'submit',bundle={'workflow':doc,'modules':modules,'summary':'126 点有限循环与子图，保留中文原因'})).json()
    assert r['status']=='saved' and r['validation']['valid'],r
    run=c.post('/api/runs',json={'workflow':doc,'inputs':{'scan':{'currents':list(range(126))}}}).json()
    assert run['status']=='succeeded',run
    assert len(run['outputs']['scan_loop__assessment']['reason'])==126


def test_feedback_keeps_frozen_input_and_versions_for_same_workflow(setup):
    c,s,store,calls=setup;t=started(s);b=bundle(t)
    c.post('/api/quick-chats/'+t['id']+'/tool',json=credential(t,'submit',bundle=b))
    run=c.post('/api/runs',json={'workflow':b['workflow'],'inputs':{'data':{'x':4}}}).json()
    t=s.get(t['id']);t.update(status='idle');t=s.save(t)
    r=c.post('/api/quick-chats/'+t['id']+'/messages',json={'revision':t['revision'],'text':'请检查运行结果','run_id':run['id']})
    assert r.status_code==200,r.text
    raw=s.get(t['id']);assert raw['feedback']['inputs']=={'data':{'x':4}}
    assert raw['feedback']['snapshot']['modules'][0]['version']==1
    assert 'feedback' not in s.public(raw)
    brief=s.context(raw,'diagnostics')['feedback']
    assert brief['trace_count']==1 and 'snapshot' not in brief and 'trace' not in brief
    assert s.context(raw,'run')['inputs']=={'data':{'x':4}}
    assert len(s.context(raw,'run_trace'))==1
