import copy
import pytest
from fastapi.testclient import TestClient
from pe_workflow import api
from pe_workflow.models import Module,Workflow
from pe_workflow.storage import Store


@pytest.fixture
def client(tmp_path,monkeypatch):
    store=Store(tmp_path/'versions.sqlite');monkeypatch.setattr(api,'store',store)
    port={'type':'flat_dict','fields':{'x':{'type':'number','quantity':'scalar','unit':'1'}}}
    out={'type':'flat_dict','fields':{'y':{'type':'number','quantity':'scalar','unit':'1'}}}
    module=Module(id='formula',version=1,name='公式',contract_version='3.0',inputs={'data':port},outputs={'result':out},expressions={'result':"{'y': data['x']*2}"},provenance='test')
    store.seed_module(module)
    with TestClient(api.app) as c:yield c,module,port,out


def task(client,wrong=False):
    c,base,port,out=client
    wf=Workflow(schema_version='3.0',id='version_case',name='版本验证',inputs={'data':port},nodes=[{'id':'calc','module_id':base.id,'module_version':1,'inputs':{'data':{'source':'input','port':'data'}}}],outputs={'result':{'source':'node','node':'calc','port':'result'}},output_ports={'result':out})
    binding=lambda value:{'offset':{'source':'constant','value':{'x':value}}}
    request={'workflow':wf.model_dump(),'inputs':{'data':{'x':3}},'expected_revision':0,'node_path':['calc'],'instruction':'派生公式 x*3+offset','added_bindings':binding(1),
             'anchors':[{'inputs':{'data':{'x':x},'offset':{'x':1}},'expected':{'result':{'y':3*x+1}}} for x in (2,3)],
             'evaluation_cases':[{'name':'工况1','added_bindings':binding(1),'expected':{'result':{'y':10}}},{'name':'工况2','added_bindings':binding(2),'expected':{'result':{'y':999 if wrong else 11}}}],
             'acceptance_label':'测试手算依据'}
    prepared=c.post('/api/change-tasks',json=request).json()
    assert prepared['status']=='awaiting_candidate',prepared
    module=base.model_copy(deep=True);module.version=2;module.inputs['offset']=copy.deepcopy(module.inputs['data']);module.expressions={'result':"{'y': data['x']*3+offset['x']}"}
    result=c.post('/api/change-tasks/'+prepared['id']+'/apply',json={'context_id':prepared['context_id'],'module':module.model_dump(),'added_bindings':binding(1)})
    assert result.status_code==200,result.text
    return result.json()


def test_all_cases_publish_once_with_proven_lineage(client):
    c,*_=client;result=task(client)
    assert result['status']=='applied' and len(result['case_results'])==2
    assert len(c.get('/api/modules').json())==2
    assert len(c.get('/api/runs').json())==3
    family=c.get('/api/module-families').json()['items'][0]
    assert family['versions'][1]['lineage']['parent_version']==1
    assert family['versions'][1]['lineage']['acceptance_label']=='测试手算依据'
    assert family['versions'][0]['uses']==[]
    assert len(family['versions'][1]['uses'])==1


def test_one_bad_full_case_publishes_nothing(client):
    c,*_=client;result=task(client,wrong=True)
    assert result['status']=='rejected' and result['diagnostics'][0]['code']=='CASE_MISMATCH'
    assert len(c.get('/api/modules').json())==1
    assert c.get('/api/workflows').json()==[] and c.get('/api/runs').json()==[]


def test_modify_policy_requires_model_mode(client):
    from pe_workflow.models import AutomationRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AutomationRequest.model_validate({'source_policy':'modify','mode':'interface'})


def test_added_input_units_cannot_be_reinterpreted():
    from pe_workflow.changes import physical_declaration
    from pe_workflow.models import Port
    c=Port(type='number',quantity='temperature',unit='degC').model_dump()
    cosmetic={**c,'description':'capacitance material temperature'}
    assert physical_declaration(c)==physical_declaration(cosmetic)
    assert physical_declaration(c)!=physical_declaration({**c,'unit':'K'})


def test_thermal_recipe_exposes_assumptions_and_three_full_cases():
    from pe_workflow.thermal_task import recipe
    from pe_workflow.models import Anchor,EvaluationCase
    r=recipe()
    assert r['source_policy']=='modify' and r['mode']=='model'
    assert '待课题组核对' in r['acceptance_label']
    assert len(r['evaluation_cases'])==3
    assert r['added_input_ports']['thermal']['fields']['temperature']['unit']=='degC'
    for case in r['evaluation_cases']:EvaluationCase.model_validate(case)
    for anchor in r['anchors']:Anchor.model_validate(anchor)
    assert [c['added_bindings']['thermal']['value']['temperature'] for c in r['evaluation_cases']]==[25,75,125]
