"""Prepared real-model experiment: reference parity and deliberately wrong adapter."""
import copy
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pe_workflow import api
from pe_workflow.engine import execute,WorkflowError
from pe_workflow.models import Module,Workflow
from pe_workflow.storage import Store

PACK=Path(__file__).resolve().parents[2]/'examples/real-capacitor'
def read(name):return json.loads((PACK/name).read_text(encoding='utf-8'))


@pytest.mark.parametrize('row',read('evaluator/comparison.json')['rows'],ids=lambda row:row['name'])
def test_old_function_parity(row):
    baseline=Module.model_validate(read('baseline-registration.json')['module'])
    candidate=Module.model_validate(read('evaluator/reference-candidate.json')['module'])
    wf=Workflow.model_validate(read('workflow.json')['workflow'])
    registry={f'{m.id}@{m.version}':m for m in (baseline,candidate)}
    original=execute(wf,registry,row['inputs'])
    wf.nodes[0].module_version=2
    adapted=execute(wf,registry,row['inputs'])
    assert original==pytest.approx(row['expected_W'],rel=1e-10,abs=1e-10)
    assert adapted==pytest.approx(row['expected_W'],rel=1e-10,abs=1e-10)


@pytest.mark.parametrize('port,value',[('fs',0),('C',0),('i_rms',-1),('tan_delta',-1)])
def test_invalid_inputs_rejected_instead_of_legacy_zero(port,value):
    base=Module.model_validate(read('baseline-registration.json')['module'])
    fixture=read('workflow.json');wf=Workflow.model_validate(fixture['workflow']);fixture['inputs'][port]=value
    with pytest.raises(WorkflowError) as exc:execute(wf,{f'{base.id}@1':base},fixture['inputs'])
    assert exc.value.diagnostic.code=='DOMAIN_VIOLATION'


def test_reference_snapshot_is_unchanged():
    import hashlib
    manifest=read('provenance.json')
    assert hashlib.sha256((PACK/'reference/capacitor_loss_cal.py').read_bytes()).hexdigest()==manifest['sha256']
    assert manifest['call_lines']


def test_real_upload_task_accepts_correct_adapter_and_rejects_wrong_units(tmp_path,monkeypatch):
    store=Store(tmp_path/'capacitor.sqlite');monkeypatch.setattr(api,'store',store)
    with TestClient(api.app) as client:
        assert client.post('/api/harness/submit-candidate',json=read('baseline-registration.json')).json()['status']=='registered'
        task=client.post('/api/change-tasks',json=read('task-request.json')).json()
        assert task['status']=='awaiting_candidate' and task['demo_candidate'] is None
        candidate=read('evaluator/reference-candidate.json');candidate['context_id']=task['context_id']
        wrong=copy.deepcopy(candidate)
        wrong['module']['adapter']['arguments']['operating']['assemble']['current_ma'].pop('conversion')
        failed=client.post('/api/change-tasks/'+task['id']+'/apply',json=wrong).json()
        assert failed['status']=='rejected' and failed['stage']=='anchors'
        assert len(store.modules())==1 and not store.workflows()
        applied=client.post('/api/change-tasks/'+task['id']+'/apply',json=candidate).json()
        assert applied['status']=='applied'
        assert applied['run']['outputs']['p_cap_total']==pytest.approx(1.1368210220849668)
        assert applied['workflow']['nodes'][0]['module_version']==2
        assert 'calculate_uploaded_loss' in store.modules()[-1]['source']
