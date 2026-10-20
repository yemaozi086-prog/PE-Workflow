import copy,time,uuid,pytest
from pe_workflow.storage import Conflict
from test_designs import fixture,create,plan
from pe_workflow.design_lifecycle import terminal_event,pause_without_result

def active(c,s):
    t=s.get(create(c)['id']);t.update(status='awaiting_worker',call_id=uuid.uuid4().hex,token='secret',calls=1,call_limit=4,dispatched_at=time.time()-60)
    return s.save(t,'isolated_dispatch')

def test_resume_keeps_budget_and_fences_late_callback(fixture):
    c,s,store=fixture;t=active(c,s);original=copy.deepcopy(t)
    t=pause_without_result(s,t,'AGENT_ENDED_WITHOUT_RESULT','ended')
    assert t['status']=='paused' and t['worker_result_closed']
    assert s.public(t)['recovery_actions'][0]['id']=='resume'
    r=c.post('/api/workflow-designs/'+t['id']+'/resume',json={'revision':t['revision']})
    assert r.status_code==200,r.text
    t=s.get(t['id']);assert t['calls']==1 and t['call_limit']==4 and t['status']=='dispatch_ready'
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/resume',json={'revision':t['revision']})
    t.update(call_id=uuid.uuid4().hex,token='new-secret',status='awaiting_worker');s.save(t,'next_call')
    late=c.post('/api/workflow-designs/'+t['id']+'/result',json={'call_id':original['call_id'],'token':'secret','kind':'plan','plan':plan()})
    assert late.status_code==200 and late.json()['applied'] is False
    assert s.get(t['id'])['status']=='awaiting_worker' and not store.runs()

def test_authenticated_failure_is_idempotent(fixture):
    c,s,_=fixture;t=active(c,s);url='/api/workflow-designs/'+t['id']+'/transport-failure'
    body={'call_id':t['call_id'],'token':'wrong','code':'TOOL_ARGUMENT_OBJECT'}
    with pytest.raises(Conflict):c.post(url,json=body)
    body['token']='secret';assert c.post(url,json=body).status_code==200
    saved=s.get(t['id']);assert saved['status']=='paused' and saved['calls']==1
    c.post(url,json=body);assert s.get(t['id'])['revision']==saved['revision']

def test_terminal_requires_fresh_end_event():
    assert terminal_event({'events':[]},100) is None
    assert terminal_event({'events':[{'type':'turn/end','time':99}]},100) is None
    assert terminal_event({'events':[{'type':'turn/end','time':101},{'type':'turn/start','time':102}]},100) is None
    assert terminal_event({'events':[{'event':{'type':'turn/end','time':101,'seq':3}}]},100)['seq']==3
    assert terminal_event({'events':[{'type':'turn/end','time':'2026-10-20T01:00:00Z'}]},100)

def test_missing_session_does_not_close_call(fixture,monkeypatch):
    from pe_workflow import automation
    c,s,_=fixture;t=active(c,s);t['session_id']='isolated';s.save(t,'session')
    monkeypatch.setattr(automation,'rpc',lambda *a:{'items':[]})
    s.progress.refresh(t['id']);assert s.get(t['id'])['status']=='awaiting_worker'

def test_stable_idle_with_fresh_end_closes_call(fixture,monkeypatch):
    from pe_workflow import automation
    c,s,_=fixture;t=active(c,s);t['session_id']='isolated';t=s.save(t,'session')
    s.progress.merge(t,{'running_reported':False,'observed_seq':8,'idle_since':time.time()-20,'checked_epoch':0})
    def rpc(method,*a):
        return {'items':[{'sessionId':'isolated','running':False,'projections':{'asOfSeq':8}}]} if method=='session.list' else {'events':[{'type':'turn/end','time':time.time()-10,'seq':8}]}
    monkeypatch.setattr(automation,'rpc',rpc)
    s.progress.refresh(t['id']);assert s.get(t['id'])['status']=='paused'

def test_initial_array_schema_and_catalog(fixture):
    from pe_workflow.planning_protocol import blueprint_schema,delta_schema
    schema=blueprint_schema();port=schema['$defs']['CompactPort']
    assert all(k in port['properties'] for k in ('dtype','shape','axis_names'))
    assert delta_schema(['/port_defs/x'])['properties']['edits']['items']['properties']['path']['enum']==['/port_defs/x']
    c,s,_=fixture;context=s.context(active(c,s))
    assert context['controlled_syntax']['numeric_catalog']['functions']
    assert all('keywords' in f and 'explicit_transform' in f for f in context['controlled_syntax']['numeric_catalog']['functions'])

def test_shared_frontend_control_fixtures_match_runtime():
    import json
    from pathlib import Path
    from pe_workflow.models import Workflow,Node
    from pe_workflow.engine import interface,compile_workflow
    cases=json.loads((Path(__file__).resolve().parents[2]/'frontend/scripts/fixtures/control-contracts.json').read_text(encoding='utf-8'))
    for case in cases:
        w=Workflow.model_validate(case['workflow']);n=Node.model_validate(case['node'])
        _,outputs=interface(n,{}) if n.kind!='aggregate' else (None,compile_workflow(w,{})[1][n.id][1])
        assert {k:v.model_dump() for k,v in outputs.items()}==case['expected'],case['name']
