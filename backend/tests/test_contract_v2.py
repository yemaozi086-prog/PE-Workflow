import copy
import pytest
from fastapi.testclient import TestClient
from pe_workflow import api
from pe_workflow.engine import execute,check_module,validate_workflow,WorkflowError,fill_inputs
from pe_workflow.expressions import analyze_source
from pe_workflow.models import Module,Port
from pe_workflow.storage import Store
from pe_workflow.waveform import fixture


@pytest.fixture
def client(tmp_path,monkeypatch):
    store=Store(tmp_path/'v2.sqlite')
    for m in api.builtins()+[fixture()[0]]:store.seed_module(m)
    monkeypatch.setattr(api,'store',store)
    with TestClient(api.app) as c:yield c


def test_nested_adapter_units_and_scipy_integral():
    m,wf,inputs=fixture();check_module(m)
    result=execute(wf,{f'{m.id}@1':m},inputs)
    assert result['energy']==pytest.approx(.02)


def test_array_shared_shape_and_coordinate_order():
    m,wf,inputs=fixture();registry={f'{m.id}@1':m}
    inputs['i']=[0,1]
    assert not validate_workflow(wf,registry,inputs)['valid']
    inputs['i']=[0,1,2];inputs['t']=[0,.002,.001]
    assert validate_workflow(wf,registry,inputs)['diagnostics'][0]['code']=='COORDINATE_ORDER'


def test_different_coordinate_source_requires_transform():
    m,wf,inputs=fixture();wf.inputs['other_t']=wf.inputs['t'].model_copy(deep=True)
    wf.nodes[0].inputs['t'].port='other_t';inputs['other_t']=[0,.001,.002]
    report=validate_workflow(wf,{f'{m.id}@1':m},inputs)
    assert report['diagnostics'][0]['code']=='COORDINATE_BINDING'


def test_array_index_cannot_hide_as_adapter():
    m,_,_=fixture();data=m.model_dump();data['adapter']['arguments']['samples']['assemble']['clock_ms']['from_path'].append(0)
    with pytest.raises(WorkflowError):check_module(Module.model_validate(data))


def test_runtime_domain_rejection():
    m,wf,inputs=fixture();data=m.model_dump();data['domains']={'voltage':{'maximum':5}}
    with pytest.raises(WorkflowError) as e:execute(wf,{f'{m.id}@1':Module.model_validate(data)},inputs)
    assert e.value.diagnostic.code=='DOMAIN_VIOLATION'


@pytest.mark.parametrize('value',[[1,'x'],[float('inf'),2],[[1],[2,3]],[True,False]])
def test_bad_arrays_rejected(value):
    p=Port(type='array',value_type={'shape':['N']},axes=[{'name':'sample'}])
    with pytest.raises(WorkflowError):fill_inputs({'x':value},{'x':p},'/inputs')


def test_matrix_solve():
    p=lambda shape,axes:Port(type='array',value_type={'shape':shape},axes=[{'name':n} for n in axes])
    m=Module(id='solve_demo',version=1,name='solve',inputs={'a':p([2,2],['row','column']),'b':p([2],['row'])},outputs={'x':p([2],['column'])},implementation='expressions',expressions={'x':'scipy.linalg.solve(a,b)'},provenance='hand checked')
    from pe_workflow.expressions import execute_module
    check_module(m)
    assert execute_module(m,{'a':[[2,0],[0,4]],'b':[6,8]})['x']==[3,2]


def test_transpose_requires_explicit_evidence():
    m,_,_=fixture();data=m.model_dump();data['source']=data['source'].replace('seconds = samples["clock_ms"] / 1000','seconds = np.transpose(samples["clock_ms"]) / 1000')
    with pytest.raises(WorkflowError) as e:check_module(Module.model_validate(data))
    assert e.value.diagnostic.code=='UNDECLARED_TRANSFORM'


@pytest.mark.parametrize('source',['import os\ndef f(x):\n return x','def f(x):\n return open(x)','def f(x):\n return x.__class__','def f(x):\n while True:\n  x = x + 1\n return x','import numpy as np\ndef f(x):\n return np.load(x)'])
def test_source_restrictions(source):assert not analyze_source(source)['supported']

def test_reserved_assignment_diagnostic_names_line_and_symbol():
    report = analyze_source('def compute(inputs):\n    pi = 3.14\n    return pi\n')
    assert not report['supported']
    message = str(report)
    assert "pi" in message and "第 2 行" in message and "冲突" in message


def candidate():
    m,_,inputs=fixture();data=m.model_dump();data['id']='uploaded_energy'
    return {'module':data,'anchors':[{'inputs':inputs,'expected':{'energy':.02},'tolerance':1e-9}]}


def test_registration_samples_gate_and_replay(client):
    c=candidate()
    assert client.post('/api/modules',json=c['module']).status_code==422
    bad=copy.deepcopy(c);bad['anchors'][0]['expected']['energy']=20
    assert client.post('/api/harness/submit-candidate',json=bad).json()['status']=='rejected'
    assert not client.get('/api/modules/search?q=uploaded_energy').json()
    assert client.post('/api/harness/submit-candidate',json=c).json()['status']=='registered'
    _,wf,inputs=fixture();wf.nodes[0].module_id='uploaded_energy'
    run=client.post('/api/runs',json={'workflow':wf.model_dump(),'inputs':inputs}).json()
    assert run['status']=='succeeded'
    assert client.post('/api/runs/'+run['id']+'/replay').json()['outputs']==run['outputs']


def test_context_and_replacement_plan(client):
    _,wf,inputs=fixture()
    req={'workflow':wf.model_dump(),'inputs':inputs,'node_id':'integrate','instruction':'保留能量积分接口，更换代码'}
    ctx=client.post('/api/harness/prepare-change',json=req).json()
    assert ctx['status']=='prepared' and ctx['context']['rules']
    c=candidate();c['context_id']=ctx['context_id']
    result=client.post('/api/harness/submit-candidate',json=c).json()
    assert result['status']=='registered'
    assert result['replacement_plans'][0]['status']=='compatible'
    assert client.post('/api/harness/submit-candidate',json=c).status_code==409


def test_versioned_definitions_immutable(client):
    d={'id':'current.rms','version':1,'name':'有效值电流','port':{'type':'number','quantity':'current','characteristic':'rms','unit':'A'},'provenance':'用户明确指定'}
    assert client.post('/api/parameter-definitions',json=d).status_code==200
    assert client.post('/api/parameter-definitions',json=d).status_code==409
    d['version']=2;d['port']['scope']='primary'
    assert client.post('/api/parameter-definitions',json=d).status_code==200
    assert len(client.get('/api/parameter-definitions').json())==2
    c=candidate();c['module']['inputs']['voltage']['definition_ref']={'id':'current.rms','version':1}
    response=client.post('/api/harness/submit-candidate',json=c)
    assert response.status_code==422 and response.json()['diagnostics'][0]['code']=='DEFINITION_CONFLICT'
