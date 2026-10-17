import copy
import math
import json
import pytest
from pe_workflow import dbsrc
from pe_workflow.models import AssemblyPreviewRequest, Workflow
from pe_workflow.scenarios import compile_plan, context
from pe_workflow.engine import execute, WorkflowError


def sample_request():
    modules,_,original=dbsrc.fixture()
    registry={f'{m.id}@{m.version}':m for m in modules}
    design=dbsrc.action({'action':original['action']})['design']
    electrical=dbsrc.sizing({'design':design,'settings':original['settings']})['electrical']
    inputs={'design':design,'settings':original['settings'],
            'point':{'Vin':original['settings']['Vin'],'Vout':350.,'Pout':1000.,'vi':0.,'pi':0.}}
    for name in ('transformer','inductor','switch','capacitor'):
        inputs[name]=dbsrc.selection({'electrical':electrical},name)['component']
    data={'instruction':'给定设计和器件，计算单个工况全部 EPS 候选的电容损耗，不判断可行性。',
          'facts':{'modulation':'EPS','scope':'single_workpoint','assessment':'loss_only',
                   'upstream_constraints':'not_evaluated','control':'finite_batch'},
          'inputs':inputs,
          'plan':{'control':'finite_batch','summary':'电流候选逐行进入电容损耗计算。','steps':[
              {'id':'currents','role':'candidate_current','module_id':'dbsrc_modulation','module_version':1,
               'inputs':{k:{'source':'input','port':k} for k in registry['dbsrc_modulation@1'].inputs},
               'reason':'使用冻结 EPS 模型计算候选。','evidence':['dbsrc.py:modulation']},
              {'id':'capacitor','role':'capacitor_loss','module_id':'dbsrc_loss_capacitor','module_version':1,
               'inputs':{'candidate':{'source':'node','node':'currents','port':'candidates'},
                         'settings':{'source':'input','port':'settings'},
                         'previous':{'source':'constant','value':{'valid':1.}}},
               'reason':'只计算介质损耗，不代表上游约束通过。','evidence':['dbsrc.py:loss']} ]}}
    return json.loads(json.dumps(data,default=lambda v:v.item())),registry


@pytest.mark.parametrize('voltage,power',[(350.,1000.),(400.,1500.)])
def test_single_workpoint_all_candidates_match_frozen_formula(voltage,power):
    data,modules=sample_request()
    data['inputs']['point'].update(Vout=voltage,Pout=power)
    report=compile_plan(AssemblyPreviewRequest.model_validate(data),modules)
    result=execute(Workflow.model_validate(report['workflow']),modules,data['inputs'])
    # Independent reference: original frozen numerical routines, not generated graph.
    from pe_workflow.science.dbsrc_v1.topo_volt_current_cal.DBSRC_EPS import DBSRC_CurrentModel_EPS
    from pe_workflow.science.dbsrc_v1.topo_volt_current_cal.DBSRC_EPS_max import batch_calculate_key_params
    design=data['inputs']['design'];point=data['inputs']['point']
    model=DBSRC_CurrentModel_EPS(design['inductance'],design['capacitance'],design['frequency'],design['n'])
    reference=batch_calculate_key_params(model,point['Vin'],voltage,model.calculate_D2(point['Vin'],voltage,power))
    assert reference and len(result['loss']['cap1_loss'])==len(reference)
    for i,row in enumerate(reference):
        values=row['key_params']
        expected=values['i_rms_c1']**2*.001/(2*math.pi*values['fs']*values['capacitance1'])
        assert result['loss']['cap1_loss'][i]==pytest.approx(expected,rel=1e-12)
        assert result['loss']['p_cap_total'][i]==pytest.approx(expected,rel=1e-12)
        assert result['candidates']['D1'][i]==row['D1']
        for key,value in values.items():
            assert result['candidates'][key][i]==pytest.approx(float(value),rel=1e-12)
    assert report['validation']['reference']=='not_checked'
    assert report['experience']['review_required']


@pytest.mark.parametrize('mutation,code',[
    (lambda d:d['facts'].pop('modulation'),'SCENE_FACTS_REQUIRED'),
    (lambda d:d['facts'].update(modulation='SPS'),'SCENE_FACTS_REQUIRED'),
    (lambda d:d['plan']['steps'][1].update(module_version=2),'INCOMPATIBLE_PROFILE'),
    (lambda d:d['plan']['steps'][1]['inputs']['candidate'].update(node='other'),'CANDIDATE_PROVENANCE'),
    (lambda d:d['plan']['steps'][1]['inputs']['previous'].update(value={'valid':0.}),'CANDIDATE_PROVENANCE'),
    (lambda d:d['plan']['steps'][0].update(evidence=['invented']),'EVIDENCE_REQUIRED')])
def test_unsafe_plans_rejected(mutation,code):
    data,modules=sample_request();mutation(data)
    with pytest.raises(WorkflowError) as err:compile_plan(AssemblyPreviewRequest.model_validate(data),modules)
    assert err.value.diagnostic.code==code


def test_context_does_not_claim_missing_facts_or_mutate_registry():
    data,modules=sample_request();before=copy.deepcopy(modules)
    packet=context(data['instruction'],{},modules)
    assert packet['status']=='needs_clarification'
    assert all(r['status']=='needs_clarification' for r in packet['rule_report'])
    compile_plan(AssemblyPreviewRequest.model_validate(data),modules)
    assert modules==before


def test_scene_and_preview_http_contract():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pe_workflow.scenarios import install
    from pe_workflow.storage import digest
    data,modules=sample_request()
    app=FastAPI();install(app,lambda:modules)
    with TestClient(app) as client:
        response=client.get('/api/scenes').json()
        assert response['count']==len(response['items'])==1
        task={k:v for k,v in data.items() if k!='plan'}
        packet=client.post('/api/assembly/context',json=task).json()
        recorded=packet.pop('context_hash')
        assert recorded==digest(packet)
        assert packet['status']=='ready' and 'workflow' not in packet
        preview=client.post('/api/assembly/preview',json=data)
        assert preview.status_code==200
        assert preview.json()['validation']['execution']=='not_run'


def test_incompatible_context_is_blocked_not_a_question():
    data,modules=sample_request()
    data['facts']['modulation']='SPS'
    assert context(data['instruction'],data['facts'],modules)['status']=='blocked'


def test_declared_rule_and_file_evidence_can_supplement_role_evidence():
    data,modules=sample_request()
    data['plan']['steps'][0]['evidence'].append('dbsrc.py:current_model')
    data['plan']['steps'][1]['evidence'].append('capacitor_loss_cal.py')
    assert compile_plan(AssemblyPreviewRequest.model_validate(data),modules)['status']=='preview_ready'


def test_extra_loss_dictionary_diagnostic_names_the_actual_problem():
    data,modules=sample_request()
    data['plan']['steps'][1]['inputs']['capacitor']={'source':'input','port':'capacitor'}
    with pytest.raises(WorkflowError) as exc:compile_plan(AssemblyPreviewRequest.model_validate(data),modules)
    assert "['capacitor']" in exc.value.diagnostic.message
    assert 'candidate/settings/previous' in exc.value.diagnostic.message
