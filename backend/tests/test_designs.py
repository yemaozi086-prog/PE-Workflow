import copy
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from pe_workflow.models import Module,Port,Workflow,Node,Binding,DesignWorkerResult
from pe_workflow.storage import Store,Conflict
from pe_workflow.designs import install
from pe_workflow.engine import execute,WorkflowError

def bag(quantity='power',unit='W'):
    return Port(type='flat_dict',kind='flat_dict',fields={'value':Port(type='number',quantity=quantity,unit=unit)})

def tool():
    return Module(id='double',version=1,name='倍增样例',contract_version='3.0',inputs={'data':bag()},outputs={'result':bag()},expressions={'result':"{'value': data['value'] * 2}"},provenance='test fixture only')

def plan(module=True):
    m=tool()
    return {'title':'样例计算链','summary':'验证编排机制，不表示物理模型','steps':[{'id':'calculate','title':'计算输出','purpose':'测试倍增函数','dependencies':[],'inputs':{k:p.model_dump() for k,p in m.inputs.items()},'outputs':{k:p.model_dump() for k,p in m.outputs.items()},'assumptions':['纯演示'],'evidence':['user supplied formula'],'formula_latex':'y=2x','module_ref':'double@1' if module else None,'selection_reason':'用户公式'}],'limitations':['仅测试']}

def workflow(m=None):
    m=m or tool()
    return Workflow(schema_version='3.0',id='candidate',name='candidate',inputs=m.inputs,nodes=[Node(id='calculate',module_id=m.id,module_version=m.version,inputs={'data':Binding(source='input',port='data')})],outputs={'result':Binding(source='node',node='calculate',port='result')},output_ports=m.outputs).model_dump()

@pytest.fixture
def fixture(tmp_path):
    store=Store(tmp_path/'design.sqlite');store.add_module(tool());app=FastAPI()
    def registry():return {f'{m["id"]}@{m["version"]}':Module.model_validate(m) for m in store.modules()}
    def worker(payload,timeout):
        try:return {'status':'succeeded','outputs':execute(Workflow.model_validate(payload['workflow']),{f'{m["id"]}@{m["version"]}':Module.model_validate(m) for m in payload['modules']},payload['inputs'])}
        except Exception as e:return {'status':'failed','error':str(e)}
    def problem(code,message,status=422):raise HTTPException(status,detail=message)
    install(app,lambda:store,registry,worker,problem)
    # No lifespan here: callback tests simulate DSH and never call a real model.
    return TestClient(app),app.state.designs,store

def create(c):return c.post('/api/workflow-designs',json={'instruction':'请根据工具计算倍增结果'}).json()

def callback(c,s,t,**payload):
    current=s.get(t['id']);current.update(status='awaiting_worker',call_id='a'*32,token='secret',calls=current.get('calls',0)+1);s.save(current,'test_agent_dispatched')
    r=c.post('/api/workflow-designs/'+t['id']+'/result',json={'call_id':'a'*32,'token':'secret',**payload});assert r.status_code==200,r.text
    return r.json()

def confirm(c,t):return c.post('/api/workflow-designs/'+t['id']+'/confirm',json={'revision':t['revision'],'allow_unverified':True}).json()

def test_requirement_to_saved_workflow_without_numeric_inputs(fixture):
    c,s,store=fixture;t=create(c);assert 'inputs' not in t['request']
    t=callback(c,s,t,kind='plan',plan=plan());assert t['status']=='awaiting_confirmation'
    t=confirm(c,t);assert t['phase']=='bind'
    t=callback(c,s,t,kind='workflow',workflow=workflow());assert t['status']=='ready_to_save'
    assert t['validation']['execution']=='not_run'
    t=c.post('/api/workflow-designs/'+t['id']+'/publish',json={'revision':t['revision']}).json()
    saved=store.workflow(t['workflow']['id']);assert saved['inputs']=={} and t['status']=='saved'
    assert not store.runs()

def test_missing_tool_formula_registration_and_resume(fixture):
    c,s,store=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False));t=confirm(c,t)
    assert t['status']=='missing_tools'
    payload={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'输入输出均为功率 W；演示而已','evidence':'独立手算 3*2=6','examples':[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}}]}
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json=payload).json();m=tool().model_dump();m['id']='new_double'
    t=callback(c,s,t,kind='module',module=m);assert t['status']=='formula_candidate',t
    t=c.post('/api/workflow-designs/'+t['id']+'/register-formula',json={'revision':t['revision'],'allow_unverified':True}).json();assert t['status']=='awaiting_confirmation'
    assert t['plan']['steps'][0]['module_ref']=='new_double@1'
    t=confirm(c,t);assert t['phase']=='bind'
    t=callback(c,s,t,kind='workflow',workflow=workflow(Module.model_validate(m)));assert t['status']=='ready_to_save'

def test_wrong_formula_output_is_rejected(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'手算','examples':[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':7}}}]}).json()
    m=tool().model_dump();m['id']='wrong';t=callback(c,s,t,kind='module',module=m)
    assert t['status']=='dispatch_ready' and '独立数值样例不通过' in t['last_report']['message']

def test_revision_derives_and_preserves_old_plan(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan());old=copy.deepcopy(t['plan']);p=plan();p['title']='新方案'
    new=c.post('/api/workflow-designs/'+t['id']+'/revise',json={'revision':t['revision'],'plan':p}).json()
    assert new['id']!=t['id'] and new['parent_id']==t['id'] and s.get(t['id'])['plan']==old
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/confirm',json={'revision':1,'allow_unverified':True})

def test_rejects_invented_defaults_and_unconfirmed_modules(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan());t=confirm(c,t);wf=workflow();wf['inputs']['data']['fields']['value']['default']=1;wf['inputs']['data']['fields']['value']['required']=False
    t=callback(c,s,t,kind='workflow',workflow=wf);assert t['status']=='dispatch_ready'
    assert '默认数值' in t['last_report']['message']

def test_no_package_and_no_numeric_claim(fixture):
    c,s,_=fixture
    with pytest.raises(WorkflowError):c.post('/api/workflow-designs',json={'instruction':'test','environment_ref':'imaginary'})
    t=create(c);assert 'token' not in t
    assert s.context(s.get(t['id']))['environment']['status']=='not_installed'

def test_selected_module_contract_is_materialized_without_llm_copying(fixture):
    c,s,_=fixture;p=plan();p['steps'][0]['inputs']={};p['steps'][0]['outputs']={}
    t=callback(c,s,create(c),kind='plan',plan=p)
    assert t['status']=='awaiting_confirmation'
    assert t['plan']['steps'][0]['inputs']['data']['fields']['value']['unit']=='W'

def test_declared_wrong_unit_and_invalid_callback_are_diagnosed(fixture):
    c,s,_=fixture;p=plan();p['steps'][0]['inputs']['data']['fields']['value']['unit']='V'
    t=callback(c,s,create(c),kind='plan',plan=p)
    assert t['status']=='dispatch_ready' and '声明冲突' in t['last_report']['message']
    t=callback(c,s,t,kind='plan',plan={'nonsense':1})
    assert t['last_report'] and t['status'] in ('dispatch_ready','paused')

def test_formula_expected_answers_not_given_to_agent(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'独立手算','examples':[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}}]}).json()
    packet=s.context(s.get(t['id']))
    assert packet['modules']==[] and 'examples' not in packet['formula_request']
    assert packet['formula_request']['verification_inputs']==[{'data':{'value':3}}]

def test_late_callback_after_stop_does_not_publish(fixture):
    c,s,store=fixture;t=create(c);t=s.get(t['id']);t.update(status='awaiting_worker',call_id='b'*32,token='secret');t=s.save(t,'test_dispatched')
    c.post('/api/workflow-designs/'+t['id']+'/stop',json={'revision':t['revision']})
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/result',json={'call_id':'b'*32,'token':'secret','kind':'plan','plan':plan()})
    assert not store.workflows()

def test_two_step_dependency_chain_and_wrong_rewiring(fixture):
    c,s,store=fixture;p=plan();second=copy.deepcopy(p['steps'][0]);second.update(id='second',title='第二步',dependencies=['calculate']);p['steps'].append(second)
    t=callback(c,s,create(c),kind='plan',plan=p);t=confirm(c,t);wf=workflow();n=copy.deepcopy(wf['nodes'][0]);n.update(id='second',inputs={'data':{'source':'node','node':'calculate','port':'result'}});wf['nodes'].append(n);wf['outputs']['result']['node']='second'
    t=callback(c,s,t,kind='workflow',workflow=wf);assert t['status']=='ready_to_save',t
    assert execute(Workflow.model_validate(t['workflow']),{'double@1':tool()},{'data':{'value':3}})['result']['value']==12
    other=callback(c,s,create(c),kind='plan',plan=p);other=confirm(c,other);wf['nodes'][1]['inputs']['data']={'source':'input','port':'data'}
    bad=callback(c,s,other,kind='workflow',workflow=wf);assert bad['status']=='dispatch_ready' and '依赖' in bad['last_report']['message']

def test_formula_change_derives_existing_module_version(fixture):
    c,s,store=fixture;t=callback(c,s,create(c),kind='plan',plan=plan())
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=3x','declarations':'输入输出 W；演示','evidence':'手算','examples':[{'inputs':{'data':{'value':2}},'outputs':{'result':{'value':6}}}]}).json()
    assert s.context(s.get(t['id']))['registration_target']=={'id':'double','version':2,'parent_version':1}
    m=tool().model_dump();m.update(version=2,expressions={'result':"{'value': data['value'] * 3}"})
    t=callback(c,s,t,kind='module',module=m);assert t['status']=='formula_candidate',t
    t=c.post('/api/workflow-designs/'+t['id']+'/register-formula',json={'revision':t['revision'],'allow_unverified':True}).json()
    assert t['plan']['steps'][0]['module_ref']=='double@2'
    assert len(store.modules())==2
    with store.connect() as con:assert con.execute('select count(*) from module_relations where id=? and version=2',('double',)).fetchone()[0]==1

def test_approved_continuation_keeps_configuration_history_and_three_call_limit(fixture):
    c,s,_=fixture;t=create(c);t=s.get(t['id']);old=copy.deepcopy(t['configuration']);t.update(status='paused',calls=3);t=s.save(t,'test_paused')
    t=c.post('/api/workflow-designs/'+t['id']+'/continue',json={'revision':t['revision']}).json()
    assert t['call_limit']==3 and t['calls']==0 and t['configuration']['max_calls']==3
    assert t['configuration_history'][0]['configuration']==old
    assert c.get('/api/design-configuration').json()['provider_hash']==t['configuration']['provider_hash']

def test_protocol_diagnostic_is_concrete_and_does_not_edit_candidate():
    from pe_workflow.design_guidance import adapter_repair
    candidate={'module':{'inputs':{'electrical':{}},'adapter':{'arguments':{'i_rms':{'from_path':['electrical','i_rms']},'resistance':{'from':['inputs','electrical','resistance']}}}}}
    original=copy.deepcopy(candidate);r=adapter_repair(candidate)
    assert r['required_argument_mappings']=={'i_rms':{'from':['inputs','electrical','i_rms']}}
    assert candidate==original

def test_continuation_can_be_limited_to_one_approved_call(fixture):
    c,s,_=fixture;t=create(c);t=s.get(t['id']);t.update(status='paused');t=s.save(t,'test_paused')
    t=c.post('/api/workflow-designs/'+t['id']+'/continue',json={'revision':t['revision'],'max_calls':1}).json()
    assert t['call_limit']==1 and t['configuration']['max_calls']==1

def test_module_repair_guidance_does_not_leak_into_binding_stage(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan());t=confirm(c,t)
    packet=s.context(s.get(t['id']))
    assert packet['phase']=='bind' and packet['required_result_kind']=='workflow'
    assert packet['concrete_protocol_repair'] is None

def test_binding_respects_explicit_remaining_call_budget(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan())
    t=c.post('/api/workflow-designs/'+t['id']+'/confirm',json={'revision':t['revision'],'allow_unverified':True,'max_calls':2}).json()
    assert t['call_limit']==2 and t['configuration']['max_calls']==2

def test_unverified_derived_tool_is_not_labeled_anchor_passed(fixture):
    c,s,store=fixture;t=callback(c,s,create(c),kind='plan',plan=plan())
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=3x','declarations':'W','evidence':'用户公式，未提供数值样例'}).json()
    m=tool().model_dump();m.update(version=2,expressions={'result':"{'value': data['value'] * 3}"})
    t=callback(c,s,t,kind='module',module=m)
    c.post('/api/workflow-designs/'+t['id']+'/register-formula',json={'revision':t['revision'],'allow_unverified':True})
    import json
    with store.connect() as con:record=json.loads(con.execute('select document from module_relations where id=? and version=2',('double',)).fetchone()[0])
    assert record['acceptance_label']=='未提供独立样例，待核对'

def test_incomplete_examples_rejected_before_agent_call(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False));before=s.get(t['id'])
    with pytest.raises(WorkflowError) as error:
        c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'手算','examples':[{'inputs':{'data':{'value':''}},'outputs':{}},{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}}]})
    assert error.value.diagnostic.code=='EXAMPLE_DATA_INVALID'
    assert error.value.diagnostic.path=='/examples/0/inputs/data/value'
    assert s.get(t['id'])==before


def test_legacy_bad_example_cannot_consume_continue_budget(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False));old=s.get(t['id'])
    old.update(status='paused',phase='formula',formula_request={'step_id':'calculate','examples':[{'inputs':{'data':{'value':''}},'outputs':{}}]})
    t=s.save(old,'test_old_invalid_example');before=copy.deepcopy(t)
    with pytest.raises(WorkflowError):c.post('/api/workflow-designs/'+t['id']+'/continue',json={'revision':t['revision']})
    assert s.get(t['id'])==before
    child=c.post('/api/workflow-designs/'+t['id']+'/revise',json={'revision':t['revision'],'plan':t['plan']}).json()
    assert child['formula_draft']==old['formula_request'] and child['status']=='awaiting_confirmation'
    assert s.get(t['id'])==before


def test_empty_expected_output_rejected_before_generation(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    with pytest.raises(WorkflowError) as error:
        c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'手算','examples':[{'inputs':{'data':{'value':3}},'outputs':{}}]})
    assert error.value.diagnostic.path=='/examples/0/outputs'


def test_subset_guidance_distinguishes_output_contract_from_adapter():
    from pe_workflow.design_guidance import adapter_repair
    guidance=adapter_repair({'module':{'implementation':'python_subset','outputs':{'ripple':{'fields':{'D':{},'delta_i':{}}}}}})
    assert guidance['required_output_dictionaries']=={'ripple':['D','delta_i']}
    assert 'required_argument_mappings' not in guidance


@pytest.mark.parametrize('case', ['extra_required','undeclared','optional_unsafe','dynamic','change_required'])
def test_dependency_gate_blocks_bad_candidates_without_extra_calls(fixture,case):
    c,s,store=fixture;p=plan(False);m=tool().model_dump();m['id']='dependency_candidate'
    values={'data':{'value':3}}
    if case=='extra_required':
        extra=Port(type='number',quantity='power',unit='W').model_dump()
        p['steps'][0]['inputs']['data']['fields']['extra']=extra
        m['inputs']['data']['fields']['extra']=extra;values['data']['extra']=9
    elif case=='optional_unsafe':
        p['steps'][0]['inputs']['data']['fields']['value']['required']=False
        m['inputs']['data']['fields']['value']['required']=False
    elif case=='change_required':m['inputs']['data']['fields']['value']['required']=False
    elif case=='undeclared':m['expressions']['result']="{'value': data['unknown'] * 2}"
    else:
        m.update(implementation='python_subset',source='def compute(inputs):\n    key = "value"\n    return {"result": {"value": inputs["data"][key] * 2}}',expressions={})
    t=callback(c,s,create(c),kind='plan',plan=p)
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'手算','examples':[{'inputs':values,'outputs':{'result':{'value':6}}}]}).json()
    t=callback(c,s,t,kind='module',module=m)
    assert t['status']==('needs_clarification' if case=='dynamic' else 'paused'),t.get('last_report')
    if case=='dynamic':assert t['questions']
    assert t['calls']==1
    assert t['last_report']['code'].startswith('DEPENDENCY_') or t['last_report']['code']=='CONTRACT_CHANGE_REQUIRES_CONFIRM'
    assert t['previous_candidate']['module']['id']=='dependency_candidate'
    assert not any(x['id']=='dependency_candidate' for x in store.modules())
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/register-formula',json={'revision':t['revision'],'allow_unverified':True})


def test_formula_report_includes_minimum_and_extra_field_probes(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'手算','examples':[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}}]}).json()
    m=tool().model_dump();m['id']='dependency_ok';t=callback(c,s,t,kind='module',module=m)
    assert t['status']=='formula_candidate',t
    report=t['formula_validation']
    assert report['dependency_audit']['status']=='passed'
    assert report['input_probes']==[{'example':1,'minimum_inputs':'passed','extra_fields':'passed'}]


def test_optional_explicit_default_can_change_result_when_omitted(fixture):
    c,s,_=fixture;p=plan(False);extra=Port(type='number',quantity='ratio',unit='1',required=False,default=1).model_dump()
    p['steps'][0]['inputs']['data']['fields']['factor']=extra
    t=callback(c,s,create(c),kind='plan',plan=p)
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2*x*factor','declarations':'factor 是显式可选倍率，缺省 1','evidence':'手算','examples':[{'inputs':{'data':{'value':3,'factor':4}},'outputs':{'result':{'value':24}}}]}).json()
    m=tool().model_dump();m['id']='optional_factor';m['inputs']=p['steps'][0]['inputs'];m['expressions']['result']="{'value': data['value'] * 2 * data['factor']}"
    t=callback(c,s,t,kind='module',module=m)
    assert t['status']=='formula_candidate',t
    assert t['formula_validation']['input_probes'][0]['minimum_inputs']=='passed'

def test_identical_inputs_with_conflicting_expected_results_are_rejected(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False));before=s.get(t['id'])
    with pytest.raises(WorkflowError) as error:
        c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'手算','examples':[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}},{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':7}}}]})
    assert error.value.diagnostic.code=='EXAMPLE_DATA_INVALID'
    assert '输入完全相同' in error.value.diagnostic.message
    assert s.get(t['id'])==before


def test_failed_example_feedback_is_human_only_and_identifies_example(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'独立参考','examples':[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}},{'inputs':{'data':{'value':4}},'outputs':{'result':{'value':1234567}}}]}).json()
    m=tool().model_dump();m['id']='example_feedback';t=callback(c,s,t,kind='module',module=m)
    assert t['verification_feedback']['example']==2
    assert t['verification_feedback']['actual']=={'result':{'value':8}}
    assert t['verification_feedback']['expected']=={'result':{'value':1234567}}
    packet=s.context(s.get(t['id']))
    import json
    assert 'verification_feedback' not in packet
    assert '1234567' not in json.dumps(packet)


def test_saved_candidate_revalidation_consumes_no_generation_and_preserves_source(fixture):
    c,s,store=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'手算','examples':[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':6}}}]}).json()
    m=tool().model_dump();m['id']='saved_candidate'
    saved=s.get(t['id']);saved.update(status='blocked',calls=2,call_id='old-call',previous_candidate={'module':copy.deepcopy(m)},last_report={'code':'UNKNOWN_UNIT','message':'old platform issue'})
    t=s.save(saved,'test_saved_candidate');old=copy.deepcopy(t)
    t=c.post('/api/workflow-designs/'+t['id']+'/revalidate-candidate',json={'revision':t['revision']}).json()
    assert t['status']=='formula_candidate' and t['candidate']==m
    assert t['calls']==2 and t['call_limit']==old['call_limit']
    assert t['previous_candidate']==old['previous_candidate'] and t['configuration']==old['configuration']
    assert t['events'][-1]['event']=='platform_candidate_revalidated'
    assert t['events'][-1]['actor']=='platform'
    assert not any(x['id']=='saved_candidate' for x in store.modules())


def test_failed_revalidation_does_not_automatically_dispatch(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'独立手算','examples':[{'inputs':{'data':{'value':3}},'outputs':{'result':{'value':7}}}]}).json()
    m=tool().model_dump();m['id']='failed_recheck';old=s.get(t['id']);old.update(status='paused',calls=1,previous_candidate={'module':m});t=s.save(old,'test_saved_candidate')
    t=c.post('/api/workflow-designs/'+t['id']+'/revalidate-candidate',json={'revision':t['revision']}).json()
    assert t['status']=='paused' and t['calls']==1
    assert t['last_report']['code']=='EXAMPLE_RESULT_MISMATCH'


def test_unknown_unit_is_rejected_before_formula_generation(fixture):
    c,s,_=fixture;p=plan(False);p['steps'][0]['inputs']['data']['fields']['value']['unit']='unregistered-unit'
    t=callback(c,s,create(c),kind='plan',plan=p);before=s.get(t['id'])
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'用户单位','evidence':'声明','examples':[]}).json()
    assert t['status']=='waiting_extension'
    assert t['calls']==before['calls']
    assert t['extension_request']['status']=='needs_information'
    assert t['extension_request']['proposal']['name']=='unregistered-unit'
