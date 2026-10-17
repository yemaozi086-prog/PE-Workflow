import asyncio
import copy
import time
import uuid
import pytest
from fastapi.testclient import TestClient
from pe_workflow import api
from pe_workflow import automation
from pe_workflow.automation import Journal, failure_signature
from pe_workflow.dictionary_demo import fixture,upload_candidate
from pe_workflow.storage import Store,Conflict

@pytest.fixture
def client(tmp_path,monkeypatch):
    store=Store(tmp_path/'auto.sqlite')
    from pe_workflow.catalog import seed
    seed(store)
    for m in fixture()[0]:store.seed_module(m)
    monkeypatch.setattr(api,'store',store)
    async def idle(self):await asyncio.Event().wait()
    monkeypatch.setattr(automation.Coordinator,'loop',idle)
    with TestClient(api.app) as c:yield c

def create(client,mode='interface'):
    _,wf,inputs=fixture();candidate=upload_candidate()
    r=client.post('/api/automation-tasks',json={'workflow':wf.model_dump(),'inputs':inputs,'node_path':['loss'],'expected_revision':0,'instruction':'Adapt uploaded function; retain all physical meanings','mode':mode,'worker_protocol':'module_v1','source':candidate.source,'facts':'Explicit frequency kHz, current mA, capacitance nF and dissipation mW; RMS single group.'})
    assert r.status_code==200,r.text
    return r.json()['id']

def get(key):return Journal(api.store).get(key)
def step(key):asyncio.run(api.app.state.automation.step(get(key)))
def pending(key):
    t=get(key);t.update(status='awaiting_worker',call_id=uuid.uuid4().hex,token='test-secret',session_id='test-session',dispatch_at=time.time());return Journal(api.store).save(t,{'event':'test_dispatch'})
def submit(client,key,**changes):
    t=get(key);data={'call_id':t['call_id'],'token':t['token'],'kind':'candidate','module':upload_candidate().model_dump(),**changes}
    return client.post('/api/automation-tasks/'+key+'/result',json=data)

def test_equivalent_task_anchors_from_original_and_atomic_apply(client):
    key=create(client);step(key);t=get(key)
    assert t['status']=='dispatch_ready' and len(t['anchors'])>=2
    assert not api.store.workflows() and len(api.store.modules())==3
    pending(key);assert submit(client,key).status_code==200;step(key)
    t=get(key);assert t['status']=='applied',t
    assert t['result']['run']['outputs']['loss']['p_cap_total']==pytest.approx(1.1368210220849668)
    assert len(api.store.modules())==4 and len(api.store.workflows())==1
    # Repeat recovery/application uses existing committed change-task, no duplicate module/run.
    asyncio.run(api.app.state.automation.validate(t))
    assert len(api.store.modules())==4 and len(api.store.workflows())==1

def test_model_changes_wait_for_independent_evidence(client):
    key=create(client,'model');step(key)
    assert get(key)['status']=='needs_evidence'
    assert client.post('/api/automation-tasks/'+key+'/continue',json={'approved':True}).status_code==409
    assert len(api.store.modules())==3

def test_clarification_answer_and_unresolved_block(client):
    key=create(client);step(key);pending(key)
    r=submit(client,key,kind='questions',module=None,questions={'unit':'Which capacitance unit?'})
    assert r.status_code==200 and get(key)['status']=='needs_clarification'
    assert client.post('/api/automation-tasks/'+key+'/answer',json={'answers':{}}).status_code==422
    assert client.post('/api/automation-tasks/'+key+'/answer',json={'answers':{'unit':'nF'}}).status_code==200
    pending(key);submit(client,key,kind='questions',module=None,questions={'meaning':'Still missing group meaning'})
    assert get(key)['status']=='blocked'

def test_credentials_duplicate_and_late_results(client):
    key=create(client);step(key);pending(key)
    assert submit(client,key,token='wrong').status_code==403
    assert submit(client,key).status_code==200
    assert submit(client,key).json()['duplicate'] is True
    assert submit(client,key,explanation='different result').status_code==409
    t=get(key);t.update(call_id='b'*32,token='new-secret');Journal(api.store).save(t,{'event':'new_attempt'})
    assert submit(client,key,call_id=uuid.uuid4().hex,token='test-secret').status_code==403
    public=client.get('/api/automation-tasks/'+key).json()
    assert 'token' not in public and 'request' not in public

def test_two_identical_failures_pause_and_user_can_extend(client):
    key=create(client);step(key)
    candidate=upload_candidate().model_dump();candidate['adapter']['arguments']['operating']['assemble']['current_ma'].pop('conversion')
    for _ in range(2):
        pending(key);assert submit(client,key,module=candidate).status_code==200;step(key)
    t=get(key);assert t['status']=='paused' and t['repeat_count']==2
    assert len(api.store.modules())==3 and not api.store.workflows()
    assert client.post('/api/automation-tasks/'+key+'/continue',json={}).status_code==422
    assert client.post('/api/automation-tasks/'+key+'/continue',json={'approved':True}).status_code==200
    t=get(key);assert t['status']=='dispatch_ready' and t['repeat_count']==0 and t['batch']==2

def test_late_current_callback_after_timeout_is_accepted_without_redispatch(client):
    key=create(client);step(key);t=pending(key)
    t.update(status='delivery_unknown',generation_count=1)
    Journal(api.store).save(t,{'event':'test_timeout'})
    assert submit(client,key,token='wrong').status_code==403
    assert submit(client,key).status_code==200
    current=get(key)
    assert current['status']=='validating'
    assert current['generation_count']==1 and current['call_id']==t['call_id']
    assert submit(client,key).json()['duplicate'] is True
    assert submit(client,key,explanation='conflicting late result').status_code==409


def test_source_cannot_silently_change(client):
    key=create(client);step(key);pending(key)
    candidate=upload_candidate().model_dump();candidate['source']+='\n# silently modified'
    submit(client,key,module=candidate);step(key)
    assert get(key)['last_report']['diagnostics'][0]['code']=='UPLOADED_SOURCE_CHANGED'


def test_worker_packet_gives_adapter_shape_without_reference_report_outputs(client):
    key=create(client);step(key);t=get(key)
    t['last_report']={'status':'rejected','stage':'schema','diagnostics':[],
                      'baseline_outputs':{'secret_reference':123},'candidate_outputs':{'value':456}}
    packet=api.app.state.automation.worker_packet(t)
    assert set(packet['last_report'])=={'status','stage','diagnostics'}
    from pe_workflow.models import Adapter
    example=packet['adapter_format']['whole_dictionary_example']
    assert Adapter.model_validate(example).arguments['function_parameter'].from_path==['inputs','external_input_port']
    from pe_workflow.models import Module,Port
    from pe_workflow.adapters import check_adapter,map_arguments,map_returns
    bag=Port(type='flat_dict',fields={'value':Port(type='number',unit='1',quantity='dimensionless')})
    module=Module(id='adapter_example',version=1,name='example',implementation='python_function',
                  entrypoint='f',source='def f(function_parameter):\n return {"returned_dictionary_key": function_parameter}',
                  inputs={'external_input_port':bag},outputs={'external_output_port':bag},adapter=example,provenance='test')
    check_adapter(module,['function_parameter'],{})
    arguments=map_arguments(module,{'external_input_port':{'value':2}})
    assert map_returns(module,{'returned_dictionary_key':arguments['function_parameter']})=={'external_output_port':{'value':2}}
    nested=packet['adapter_format']['nested_and_units_example']
    module.inputs={'electrical':Port(type='flat_dict',fields={'fs':Port(type='number',unit='Hz',quantity='frequency')})}
    module.outputs={'loss':Port(type='flat_dict',fields={'power':Port(type='number',unit='W',quantity='power')})}
    module.adapter=Adapter.model_validate(nested)
    check_adapter(module,['nested_parameter'],{})
    assert map_arguments(module,{'electrical':{'fs':100000}})=={'nested_parameter':{'frequency_khz':100}}
    assert map_returns(module,({'power_mw':2000},))=={'loss':{'power':2}}


def test_original_contract_differences_report_unit_and_quantity_together():
    base=upload_candidate().model_dump()
    candidate=copy.deepcopy(base)
    leaf=next(iter(candidate['inputs'].values()))
    if leaf['type']=='flat_dict':leaf=next(iter(leaf['fields'].values()))
    leaf['unit']=''
    leaf['quantity']='changed_quantity'
    errors=automation.original_contract_errors(base,candidate)
    assert any(e['path'].endswith('/quantity') for e in errors)
    assert any(e['path'].endswith('/unit') for e in errors)
    assert automation.original_contract_errors(base,base)==[]


def patch_task(client):
    key=create(client);step(key);t=get(key)
    t['request']['worker_protocol']='implementation_patch_v1'
    Journal(api.store).save(t,{'event':'test_patch_protocol'})
    candidate=upload_candidate()
    patch={k:candidate.model_dump(by_alias=True)[k] for k in ('source','entrypoint','adapter','provenance')}
    patch['added_inputs']=[]
    return key,patch


def test_patch_materializes_frozen_contract_and_applies(client):
    key,patch=patch_task(client);before=copy.deepcopy(get(key)['packet']['base_module'])
    packet=api.app.state.automation.worker_packet(get(key))
    assert packet['worker_protocol']=='implementation_patch_v1'
    assert 'workflow' not in packet and 'inputs' not in packet and 'previous_candidate' not in packet
    pending(key);assert submit(client,key,module=None,patch=patch).status_code==200;step(key)
    task=get(key);assert task['status']=='applied',task.get('last_report')
    assert task['candidate']['inputs']==before['inputs']
    assert task['candidate']['outputs']==before['outputs']
    assert task['candidate']['id']==before['id'] and task['candidate']['version']==2
    assert task['candidate_patch']==patch


@pytest.mark.parametrize('extra',[{'inputs':{}},{'outputs':{}},{'version':99},{'id':'foreign'},{'added_inputs':['not_authorized']}])
def test_patch_rejects_scope_expansion_without_publication(client,extra):
    key,patch=patch_task(client);patch.update(extra);pending(key)
    submit(client,key,module=None,patch=patch);step(key)
    task=get(key);assert task['status']=='dispatch_ready'
    assert task['last_report']['diagnostics'][0]['code']=='PATCH_CONTRACT'
    assert len(api.store.modules())==3 and not api.store.workflows()


def test_patch_cannot_bypass_reference_validation(client):
    key,patch=patch_task(client)
    task=get(key);task['request'].update(mode='model',source_policy='modify')
    Journal(api.store).save(task,{'event':'test_wrong_formula'})
    patch['source']='def calculate_uploaded_loss(operating, component):\n return ({"capacitor": {"dissipation_mw": 0.0}},)'
    pending(key);submit(client,key,module=None,patch=patch);step(key)
    assert get(key)['status']=='dispatch_ready'
    assert get(key)['last_report']['stage']=='anchors'
    assert len(api.store.modules())==3 and not api.store.workflows()


def test_patch_copies_only_exact_authorized_new_ports(client):
    key,patch=patch_task(client);packet=get(key)['packet']
    from pe_workflow.models import Port
    declaration=Port(type='flat_dict',fields={'alpha':Port(type='number',unit='1',quantity='ratio')}).model_dump()
    packet['authorized_added_ports']={'thermal':declaration};patch['added_inputs']=['thermal']
    candidate=automation.materialize_patch(packet,patch)
    assert candidate['inputs']['thermal']==declaration
    assert candidate['inputs']['component']==packet['base_module']['inputs']['component']


def test_patch_in_legacy_envelope_gets_explicit_protocol_diagnostic(client):
    key,patch=patch_task(client);pending(key)
    submit(client,key,module=patch);step(key)
    report=get(key)['last_report']
    assert report['diagnostics'][0]['code']=='PATCH_CONTRACT'
    assert '顶层 patch' in report['diagnostics'][0]['message']
    assert len(api.store.modules())==3 and not api.store.workflows()


def test_explicit_formula_policy_accepts_new_source_and_keeps_old_version(client):
    key=create(client);step(key);t=get(key)
    t['request'].update(mode='model',source_policy='modify')
    Journal(api.store).save(t,{'event':'test_explicit_formula_authorization'})
    pending(key)
    packet=api.app.state.automation.worker_packet(get(key))
    assert packet['source_policy']=='modify' and packet['task_kind']=='selected_formula_version_derivation'
    candidate=upload_candidate().model_dump();candidate['source']+='\n# derived implementation test'
    submit(client,key,module=candidate);step(key)
    assert get(key)['status']=='applied',get(key).get('last_report')
    assert any(m['id']==candidate['id'] and m['version']==1 for m in api.store.modules())


def test_formula_preflight_batches_contract_and_syntax_gaps(client):
    key=create(client);step(key);t=get(key)
    from pe_workflow.models import Port
    t['request'].update(mode='model',source_policy='modify')
    t['packet']['authorized_added_ports']={'thermal':Port(type='flat_dict',fields={'alpha':Port(type='number',quantity='temperature_coefficient',unit='1/degC',unit_definition={'dimension':'temperature_coefficient','scale':1})}).model_dump()}
    Journal(api.store).save(t,{'event':'test_explicit_formula_authorization'})
    pending(key)
    candidate=upload_candidate().model_dump();candidate.pop('provenance');candidate['source']='def wrong(x, **kwargs):\n    return x\n'
    submit(client,key,module=candidate);step(key)
    codes={d['code'] for d in get(key)['last_report']['diagnostics']}
    assert {'SCHEMA_ERROR','CONTROLLED_SOURCE_SYNTAX','ADDED_INPUT_CONTRACT'}<=codes
    assert len(api.store.modules())==3 and not api.store.workflows()

def test_revision_conflict_preserves_candidate(client):
    key=create(client);step(key)
    _,wf,values=fixture();api.store.save(wf,values,0)
    pending(key);submit(client,key);step(key)
    assert get(key)['status']=='version_changed'
    assert len(api.store.modules())==3
    r=client.post('/api/automation-tasks/'+key+'/continue',json={'approved':True,'refresh_version':True})
    assert r.status_code==200 and get(key)['request']['expected_revision']==1

def test_delivery_unknown_requires_explicit_retry(client):
    key=create(client);step(key);t=pending(key);t.update(status='delivery_unknown');Journal(api.store).save(t,{'event':'unknown'})
    assert client.post('/api/automation-tasks/'+key+'/continue',json={'approved':True}).status_code==422
    assert client.post('/api/automation-tasks/'+key+'/continue',json={'approved':True,'retry_uncertain':True}).status_code==200

def test_journal_compare_and_swap_and_persistence(client):
    key=create(client);a=get(key);b=copy.deepcopy(a)
    Journal(api.store).save(a,{'event':'first'})
    with pytest.raises(Conflict):Journal(api.store).save(b,{'event':'stale'})
    assert Journal(Store(api.store.path)).get(key)['revision']==2

def test_different_fields_not_same_failure():
    assert failure_signature({'stage':'contract','diagnostics':[{'code':'UNIT_CONFLICT','path':'/field/a'}]})!=failure_signature({'stage':'contract','diagnostics':[{'code':'UNIT_CONFLICT','path':'/field/b'}]})

def test_interrupted_dispatch_reconciles_without_resending(client,monkeypatch):
    key=create(client);step(key);t=pending(key);t.update(status='dispatching');t=Journal(api.store).save(t,{'event':'interrupted'})
    calls=[]
    def read_history(method,payload,call_id):calls.append(method);return {'events':[{'rpcId':t['call_id']}]}
    monkeypatch.setattr(automation,'rpc',read_history)
    asyncio.run(api.app.state.automation.recover(t))
    assert calls==['session.history'] and get(key)['status']=='awaiting_worker'

def test_schema_error_is_journaled_and_repaired(client):
    key=create(client);step(key);pending(key)
    assert submit(client,key,module={'source':'bad incomplete module'}).status_code==200
    step(key);t=get(key)
    assert t['status']=='dispatch_ready' and t['last_report']['stage']=='schema'
    assert any(e['event']=='candidate_checked' for e in Journal(api.store).events(key))

def test_runner_lease_excludes_second_owner(client):
    j=Journal(api.store)
    assert j.lease('one') and not j.lease('two')
    assert j.lease('one',release=True) and j.lease('two')
    j.lease('two',release=True)

def test_three_repair_budget_with_different_failures(client,monkeypatch):
    key=create(client);step(key)
    async def reject(*args):
        t=get(key);return {'status':'rejected','stage':'contract','diagnostics':[{'code':'TEST_ERROR','path':'/field/'+str(t['attempt'])}]}
    monkeypatch.setattr(api.app.state.automation,'apply',reject)
    for _ in range(4):pending(key);submit(client,key);step(key)
    t=get(key);assert t['status']=='paused' and t['repairs_used']==3
    assert len(api.store.modules())==3

def test_worker_context_filters_unrelated_catalog_rows(client):
    key=create(client);step(key);pending(key)
    t=get(key);r=client.get('/api/automation-tasks/'+key+'/context',params={'call_id':t['call_id']},headers={'Authorization':'Bearer '+t['token']})
    assert r.status_code==200
    packet=r.json();assert 'registry' not in packet and packet['uploaded_source']==upload_candidate().source
    assert 0<len(packet['parameter_catalogs'][0]['rows'])<310

def test_stale_draft_detected_before_any_calculation(client,monkeypatch):
    _,wf,values=fixture();api.store.save(wf,values,0)
    key=create(client)
    def forbidden(*args):raise AssertionError('Stale draft must not start a calculation')
    monkeypatch.setattr(api.app.state.automation,'worker',forbidden)
    step(key);t=get(key)
    assert t['status']=='version_changed'
    assert t['last_report']['expected_revision']==0 and t['last_report']['current_revision']==1
    assert t['last_report']['target_changed'] is False
    assert Journal(api.store).events(key)[-1]['report']['diagnostics'][0]['code']=='REVISION_CONFLICT'

def test_new_target_version_requires_explicit_confirmation(client):
    api.store.seed_module(upload_candidate())
    _,wf,values=copy.deepcopy(fixture());wf.nodes[-1].module_version=2;api.store.save(wf,values,0)
    key=create(client);step(key)
    assert get(key)['last_report']['target_changed'] is True
    assert client.post('/api/automation-tasks/'+key+'/continue',json={'approved':True,'refresh_version':True}).status_code==409
    r=client.post('/api/automation-tasks/'+key+'/continue',json={'approved':True,'refresh_version':True,'accept_target_change':True})
    assert r.status_code==200
    assert get(key)['request']['workflow']['nodes'][-1]['module_version']==2

def test_http_errors_keep_structured_diagnosis(client):
    from fastapi import HTTPException
    key=create(client)
    api.app.state.automation.handle_error(get(key),HTTPException(422,detail={'code':'ACCEPTANCE_REQUIRED','message':'Missing evidence','path':'/anchors'}))
    t=get(key);assert t['last_report']['diagnostics'][0]['code']=='ACCEPTANCE_REQUIRED'
    assert Journal(api.store).events(key)[-1]['report']['diagnostics'][0]['path']=='/anchors'

def test_identical_source_does_not_call_dsh_or_publish(client,monkeypatch):
    api.store.seed_module(upload_candidate())
    _,wf,values=copy.deepcopy(fixture());wf.nodes[-1].module_version=2;api.store.save(wf,values,0)
    key=create(client);t=get(key);t['request'].update(workflow=wf.model_dump(),expected_revision=1,facts='');Journal(api.store).save(t,{'event':'latest_version'})
    def forbidden(*args):raise AssertionError('No new generation or calculation for identical implementation')
    monkeypatch.setattr(api.app.state.automation,'worker',forbidden)
    monkeypatch.setattr(automation,'rpc',forbidden)
    step(key);assert get(key)['status']=='already_current'
    assert len(api.store.modules())==4 and api.store.workflow(wf.id)['revision']==1

def test_live_progress_has_no_model_text_or_credentials(client,monkeypatch):
    key=create(client);step(key);pending(key)
    monkeypatch.setattr(automation,'rpc',lambda *args:{'events':[{'event':{'type':'tool/call','data':{'name':'flux_worker_context','arguments':'SECRET'}}},{'event':{'type':'assistant/chunk','data':{'private':'SECRET'}}}]})
    asyncio.run(api.app.state.automation.monitor(get(key)))
    t=get(key);assert t['progress']['online'] and t['progress']['observed_chunks']==1
    assert 'SECRET' not in str(t['progress']) and 'SECRET' not in str(Journal(api.store).events(key)[-1])

def blocked_after_unknown_answer(client):
    key=create(client);step(key);pending(key)
    submit(client,key,kind='questions',module=None,questions={'unit':'Confirm output unit'})
    assert client.post('/api/automation-tasks/'+key+'/answer',json={'answers':{'unit':'Unknown; do not infer'}}).status_code==200
    pending(key);submit(client,key,kind='blocked',module=None,questions={},explanation='Unit remains unconfirmed')
    return key

def test_blocked_without_questions_accepts_new_facts_and_stale_known_answers(client):
    key=blocked_after_unknown_answer(client)
    assert get(key)['questions']=={}
    r=client.post('/api/automation-tasks/'+key+'/answer',json={'answers':{'unit':'Unknown; do not infer'},'facts':'Provider confirms output mW; factor 1000 is W-to-mW conversion.'})
    assert r.status_code==200,r.text
    t=get(key);assert t['status']=='dispatch_ready'
    assert 'Provider confirms' in api.app.state.automation.worker_packet(t)['facts']
    assert len(api.store.modules())==3 and not api.store.workflows()

def test_blocked_can_resume_with_facts_only(client):
    key=blocked_after_unknown_answer(client)
    r=client.post('/api/automation-tasks/'+key+'/answer',json={'facts':'Confirmed by provider: output unit is mW.'})
    assert r.status_code==200

def test_blocked_rejects_empty_or_unchanged_supplement(client):
    key=blocked_after_unknown_answer(client)
    for data in ({},{'answers':{'unit':'Unknown; do not infer'}}):
        r=client.post('/api/automation-tasks/'+key+'/answer',json=data)
        assert r.status_code==422 and r.json()['diagnostics'][0]['code']=='SUPPLEMENT_REQUIRED'
        assert get(key)['status']=='blocked'
