import copy
from types import SimpleNamespace
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from pe_workflow.models import Port
from pe_workflow.engine import WorkflowError
from pe_workflow.storage import Store
from pe_workflow.input_profiles import install

@pytest.fixture
def profiles(tmp_path,monkeypatch):
    monkeypatch.setattr("pe_workflow.profile_files.CONFIG_PATH",tmp_path/"absent.json")
    scalar=Port(type='number',quantity='voltage',unit='V')
    bag=Port(type='flat_dict',kind='flat_dict',fields={'Vin':scalar,'note':Port(type='string')})
    registry={'dbsrc_modulation@1':SimpleNamespace(inputs={'point':bag}),
              'dbsrc_loss_capacitor@1':SimpleNamespace(inputs={'settings':Port(type='flat_dict',kind='flat_dict',fields={'limit':Port(type='number')})})}
    store=Store(tmp_path/'profiles.sqlite');app=FastAPI()
    def problem(code,message,status=422):raise HTTPException(status,detail={'code':code,'message':message})
    install(app,lambda:store,lambda:registry,problem)
    with TestClient(app) as client:yield client,registry,store

def save(client,**extra):
    data={'name':'器件方案 A','notes':'用户资料，适用范围待核对','inputs':{'point':{'Vin':390,'note':''}},'facts':{'modulation':'EPS'}};data.update(extra)
    r=client.post('/api/assembly/input-profiles',json=data);assert r.status_code==200;return r.json()

def test_partial_profile_is_explicit_persistent_snapshot(profiles):
    c,_,store=profiles;assert c.get('/api/assembly/input-profiles').json()['count']==0
    p=save(c);items=c.get('/api/assembly/input-profiles').json()['items']
    assert len(items)==1 and items[0]['compatible'] and p['scope']=='local_workspace'
    assert not store.workflows() and not store.runs()
    second=save(c,inputs={'point':{'Vin':400}});assert second['id']!=p['id']
    r=c.post('/api/assembly/input-profiles/'+p['id']+'/apply',json={}).json();assert r['inputs']['point']['Vin']==390

def test_apply_preserves_values_and_sources_including_zero_and_empty_string(profiles):
    c,_,_=profiles;p=save(c)
    payload={'inputs':{'point':{'Vin':0,'note':''}},'facts':{'modulation':'OTHER'},'provenance':{'point.Vin':'用户明确设置'}}
    original=copy.deepcopy(payload)
    r=c.post('/api/assembly/input-profiles/'+p['id']+'/apply',json=payload).json()
    assert payload==original and r['inputs']==payload['inputs'] and r['facts']==payload['facts']
    assert r['provenance']==payload['provenance'] and not r['applied']

def test_apply_fills_missing_and_records_leaf_provenance(profiles):
    c,_,_=profiles;p=save(c)
    r=c.post('/api/assembly/input-profiles/'+p['id']+'/apply',json={'inputs':{'point':{'Vin':''}}}).json()
    assert r['inputs']['point']=={'Vin':390,'note':''}
    assert r['facts']=={'modulation':'EPS'} and r['automatic_application'] is False
    assert p['id'][:8] in r['provenance']['point.Vin']

def test_contract_change_blocks_old_profile(profiles):
    c,registry,_=profiles;p=save(c)
    registry['dbsrc_modulation@1'].inputs['point'].fields['Vin'].unit='mV'
    assert c.get('/api/assembly/input-profiles').json()['items'][0]['compatible'] is False
    with pytest.raises(WorkflowError,match='声明或场景版本已改变'):
        c.post('/api/assembly/input-profiles/'+p['id']+'/apply',json={})

@pytest.mark.parametrize('inputs',[{'point':{'Vin':'390'}},{'point':{'other':1}},{'other':{}},{'point':{'Vin':''}}])
def test_invalid_and_empty_profiles_are_not_zero_filled(profiles,inputs):
    c,_,_=profiles
    with pytest.raises(WorkflowError):save(c,inputs=inputs)
    assert c.get('/api/assembly/input-profiles').json()['count']==0

def test_bad_conditions_and_blank_names_are_rejected(profiles):
    c,_,_=profiles
    with pytest.raises(WorkflowError):save(c,facts={'modulation':'SPS'})
    r=c.post('/api/assembly/input-profiles',json={'name':' ','notes':'x','inputs':{}});assert r.status_code==422
