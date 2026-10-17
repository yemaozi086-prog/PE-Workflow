import copy
import pytest
from fastapi.testclient import TestClient
from pe_workflow import api
from pe_workflow.storage import Store


@pytest.fixture
def client(tmp_path,monkeypatch):
    store=Store(tmp_path/'changes.sqlite')
    for m in api.builtins():store.seed_module(m)
    monkeypatch.setattr(api,'store',store)
    with TestClient(api.app) as c:yield c


def request_data(client):
    d=client.get('/api/examples/thermal').json()
    d.pop('revision')
    d.update(expected_revision=0,node_path=['sweep','body','model','cases','temperature','loss'],instruction='alpha=0.002 V/℃，新增输入和常量连接，保持开关损耗公式不变',alpha_demo=True,alpha=.002)
    return d


def prepare(client,data=None):
    r=client.post('/api/change-tasks',json=data or request_data(client))
    assert r.status_code==200,r.text
    task=r.json();assert task['status']=='awaiting_candidate',task
    return task


def test_selected_nested_node_atomic_apply_and_replay(client):
    data=request_data(client)
    temperature=data['workflow']['nodes'][0]['body']['nodes'][0]['cases']['temperature']
    sibling=copy.deepcopy(temperature['nodes'][0]);sibling['id']='untouched';temperature['nodes'].append(sibling)
    task=prepare(client,data)
    assert len(task['context']['target_samples'])==3
    assert task['label']=='平台自带手算示例，待课题组核对'
    result=client.post('/api/change-tasks/'+task['id']+'/apply',json=task['demo_candidate'])
    assert result.status_code==200,result.text
    applied=result.json();assert applied['status']=='applied'
    graph=applied['workflow']['nodes'][0]['body']['nodes'][0]['cases']
    selected=graph['temperature']['nodes'][0]
    assert selected['module_version']==3 and selected['inputs']['alpha']['value']==.002
    assert graph['temperature']['nodes'][1]['module_version']==2
    assert graph['baseline']['nodes'][0]['module_version']==1
    assert applied['run']['outputs']['mean_loss']==pytest.approx(107.2)
    assert applied['run']['outputs']['worst_loss']==pytest.approx(107.6)
    assert applied['revision']==1
    assert client.get('/api/workflows/thermal_demo').json()['workflow']==applied['workflow']
    replay=client.post('/api/runs/'+applied['run']['id']+'/replay').json()
    assert replay['outputs']==applied['run']['outputs']
    assert client.post('/api/change-tasks/'+task['id']+'/apply',json=task['demo_candidate']).json()['revision']==1
    assert len(client.get('/api/workflows/thermal_demo/history').json())==1


def test_missing_alpha_is_question_not_default(client):
    data=request_data(client);data.pop('alpha')
    result=client.post('/api/change-tasks',json=data).json()
    assert result['status']=='needs_clarification' and 'alpha' in result['questions']
    assert client.get('/api/change-tasks').json()==[]
    assert len(client.get('/api/modules').json())==3


def test_anchor_failure_publishes_nothing(client):
    task=prepare(client);candidate=task['demo_candidate'];candidate['module']['expressions']['p_cond']='0'
    r=client.post('/api/change-tasks/'+task['id']+'/apply',json=candidate).json()
    assert r['status']=='rejected' and r['stage']=='anchors'
    assert client.get('/api/workflows').json()==[]
    assert client.get('/api/runs').json()==[]
    assert len(client.get('/api/modules').json())==3
    assert not r['regression']['anchors'][0]['passed']


def test_wrong_wiring_cannot_be_authorized_by_agent(client):
    task=prepare(client);candidate=task['demo_candidate'];candidate['added_bindings']['alpha']['value']=.003
    r=client.post('/api/change-tasks/'+task['id']+'/apply',json=candidate)
    assert r.status_code==422 and r.json()['diagnostics'][0]['code']=='UNAUTHORIZED_BINDING'
    assert client.get('/api/workflows').json()==[]


def test_missing_wiring_blocks_candidate(client):
    task=prepare(client);candidate=task['demo_candidate'];candidate['added_bindings']={}
    assert client.post('/api/change-tasks/'+task['id']+'/apply',json=candidate).status_code==422


def test_complete_graph_failure_does_not_publish_module(client):
    task=prepare(client);candidate=task['demo_candidate']
    # Anchor temperatures 25/125 still pass, but the actual 75-degree workpoint must fail.
    expression=candidate['module']['expressions']['p_cond']
    candidate['module']['expressions']['p_cond']=f'1 / (Tj - 75) if Tj == 75 else ({expression})'
    r=client.post('/api/change-tasks/'+task['id']+'/apply',json=candidate).json()
    assert r['status']=='rejected' and r['stage']=='workflow_execution'
    assert len(client.get('/api/modules').json())==3
    assert client.get('/api/workflows').json()==[]


def test_stale_graph_and_registry_rejected(client):
    task=prepare(client);data=request_data(client)
    client.post('/api/workflows',json={k:v for k,v in data.items() if k in ('workflow','inputs','expected_revision')})
    r=client.post('/api/change-tasks/'+task['id']+'/apply',json=task['demo_candidate'])
    assert r.status_code==409 and r.json()['diagnostics'][0]['code']=='REVISION_CONFLICT'
    assert len(client.get('/api/modules').json())==3


def test_clarification_once_and_new_context(client):
    task=prepare(client);url='/api/change-tasks/'+task['id']
    assert client.post(url+'/clarify',json={'questions':{'meaning':'请核对口径'}}).json()['status']=='needs_clarification'
    assert client.post(url+'/apply',json=task['demo_candidate']).status_code==422
    answered=client.post(url+'/answer',json={'answers':{'meaning':'保持原单管导通损耗口径'}}).json()
    assert answered['context_id']!=task['context_id']
    assert client.post(url+'/clarify',json={'questions':{'more':'再问一次'}}).status_code==409
    candidate=task['demo_candidate']
    assert client.post(url+'/apply',json=candidate).status_code==409
    candidate['context_id']=answered['context_id']
    assert client.post(url+'/apply',json=candidate).json()['status']=='applied'


def test_revision_race_rolls_back_everything(client,monkeypatch):
    task=prepare(client)
    # Simulate a concurrent graph save immediately before the atomic commit.
    commit=api.store.commit_change
    def conflict_commit(module,workflow,inputs,record,run):
        api.store.save(workflow,inputs,0)
        return commit(module,workflow,inputs,record,run)
    monkeypatch.setattr(api.store,'commit_change',conflict_commit)
    assert client.post('/api/change-tasks/'+task['id']+'/apply',json=task['demo_candidate']).status_code==409
    assert len(client.get('/api/modules').json())==3
    assert client.get('/api/runs').json()==[]


def test_contract_has_machine_readable_apply_schema(client):
    data=client.get('/api/harness/change-task-contract').json()
    assert data['protocol_version']=='3.0' and not data['connected']
    assert 'module' in data['schemas']['apply']['properties']
