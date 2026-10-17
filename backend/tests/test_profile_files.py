import json
import pytest
from pe_workflow.dbsrc import fixture
from pe_workflow.profile_files import load_file,CONFIG_PATH,unique_object
from pe_workflow.input_profiles import fill_missing,contract
from pe_workflow.engine import WorkflowError

def registry():
    modules,_,_=fixture()
    return {f'{m.id}@{m.version}':m for m in modules}

def test_file_complete_and_preserves_user_input():
    p=load_file(registry);ports,_=contract(registry)
    assert len(p['parameter_declarations'])==59
    result,applied,kept=fill_missing({'point':{'Vout':400}},p['inputs'],ports)
    assert result['point']['Vout']==400 and len(applied)==58 and kept==['point.Vout']
    assert p['catalog_source']['row_count']==310

@pytest.mark.parametrize('change',['unit','missing','contract'])
def test_bad_file_rejected(tmp_path,change):
    p=json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    if change=='unit':p['parameter_declarations']['point.Vin']['runtime_unit']='mV'
    if change=='missing':p['parameter_declarations'].pop('point.Vin')
    if change=='contract':p['contract_hash']='0'*64
    path=tmp_path/'bad.json';path.write_text(json.dumps(p),encoding='utf-8')
    with pytest.raises(WorkflowError):load_file(registry,path)

def test_duplicate_keys_rejected():
    with pytest.raises(ValueError):json.loads('{"a":1,"a":2}',object_pairs_hook=unique_object)

def test_file_profile_available_and_applied_through_api(tmp_path):
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from pe_workflow.storage import Store
    from pe_workflow.input_profiles import install
    app=FastAPI();store=Store(tmp_path/'file.sqlite')
    def problem(code,message,status=422):raise HTTPException(status,detail=message)
    install(app,lambda:store,registry,problem)
    with TestClient(app) as c:
        item=c.get('/api/assembly/input-profiles').json()['items'][0]
        assert item['compatible'] and item['source_file'].endswith('.json')
        r=c.post('/api/assembly/input-profiles/'+item['id']+'/apply',json={'inputs':{'point':{'Vout':400}}})
        assert r.status_code==200
        data=r.json();assert len(data['applied'])==58 and data['inputs']['point']['Vout']==400
        assert '.json' in data['provenance']['design.frequency']
