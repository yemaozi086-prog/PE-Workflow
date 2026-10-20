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
    import uuid
    call_id=uuid.uuid4().hex
    current=s.get(t['id']);current.update(status='awaiting_worker',call_id=call_id,token='secret',calls=current.get('calls',0)+1);s.save(current,'test_agent_dispatched')
    r=c.post('/api/workflow-designs/'+t['id']+'/result',json={'call_id':call_id,'token':'secret',**payload});assert r.status_code==200,r.text
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
    assert t['status']=='needs_clarification' and '独立数值样例不通过' in t['last_report']['message']
    assert t['failure_routing']['route']=='uncertain'

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
    result=c.post('/api/workflow-designs/'+t['id']+'/result',json={'call_id':'b'*32,'token':'secret','kind':'plan','plan':plan()}).json()
    assert result['status']=='recorded_late_result' and result['applied'] is False
    assert s.get(t['id'])['status']=='paused'
    assert not store.workflows()

def test_two_step_dependency_chain_and_wrong_rewiring(fixture):
    c,s,store=fixture;p=plan();second=copy.deepcopy(p['steps'][0]);second.update(id='second',title='第二步',dependencies=['calculate']);p['steps'].append(second)
    p['external_inputs']=copy.deepcopy(p['steps'][0]['inputs'])
    p['steps'][0]['input_sources']={'data':{'source':'input','port':'data'}}
    second['input_sources']={'data':{'source':'node','node':'calculate','port':'result'}}
    t=callback(c,s,create(c),kind='plan',plan=p);t=confirm(c,t);wf=workflow();n=copy.deepcopy(wf['nodes'][0]);n.update(id='second',inputs={'data':{'source':'node','node':'calculate','port':'result'}});wf['nodes'].append(n);wf['outputs']['result']['node']='second'
    t=callback(c,s,t,kind='workflow',workflow=wf);assert t['status']=='ready_to_save',t
    assert execute(Workflow.model_validate(t['workflow']),{'double@1':tool()},{'data':{'value':3}})['result']['value']==12
    other=callback(c,s,create(c),kind='plan',plan=p);other=confirm(c,other);wf['nodes'][1]['inputs']['data']={'source':'input','port':'data'}
    bad=callback(c,s,other,kind='workflow',workflow=wf);assert bad['status']=='dispatch_ready' and '依赖' in bad['last_report']['message']

def test_old_unbound_chain_is_read_only_and_replan_preserves_it(fixture):
    c,s,store=fixture;t=callback(c,s,create(c),kind='plan',plan=plan())
    raw=s.get(t['id']);second=copy.deepcopy(raw['plan']['steps'][0]);second.update(id='second',dependencies=['calculate']);raw['plan']['steps'].append(second)
    old=s.save(raw,'legacy_fixture');before=copy.deepcopy(old)
    public=c.get('/api/workflow-designs/'+old['id']).json()
    assert not public['plan_preflight']['valid'] and s.get(old['id'])==before
    with pytest.raises(WorkflowError) as e:confirm(c,public)
    assert e.value.diagnostic.code=='PLAN_PREFLIGHT'
    new=c.post('/api/workflow-designs/'+old['id']+'/replan',json={'revision':old['revision'],'instruction':'请保持公式，为每个字典明确来源，并添加已声明的计算前提'}).json()
    assert new['parent_id']==old['id'] and new['status']=='dispatch_ready'
    assert s.get(old['id'])==before and not store.runs()
    ctx=s.context(s.get(new['id']))
    assert ctx['previous_plan']==old['plan'] and not ctx['replan_diagnostics']['valid']

def test_plan_preflight_feedback_and_guard_cannot_be_removed(fixture):
    c,s,_=fixture;p=plan();p['external_inputs']=copy.deepcopy(p['steps'][0]['inputs'])
    t=callback(c,s,create(c),kind='plan',plan=p)
    assert t['status']=='dispatch_ready' and t['last_report']['code']=='PLAN_PREFLIGHT'
    p['steps'][0]['input_sources']={'data':{'source':'input','port':'data'}}
    p['steps'][0]['input_checks']=["data['value'] > 0"]
    t=callback(c,s,t,kind='plan',plan=p);t=confirm(c,t)
    wf=workflow();t=callback(c,s,t,kind='workflow',workflow=wf)
    assert '输入保护' in t['last_report']['message']
    wf['nodes'][0]['input_checks']=["data['value'] > 0"]
    t=callback(c,s,t,kind='workflow',workflow=wf)
    assert t['status']=='ready_to_save'

def test_different_preflight_errors_are_not_counted_as_same_failure(fixture):
    c,s,_=fixture;p=plan();p['external_inputs']=copy.deepcopy(p['steps'][0]['inputs'])
    t=callback(c,s,create(c),kind='plan',plan=p)
    assert t['repeat_count']==1
    p['steps'][0]['input_sources']={'data':{'source':'input','port':'data'}}
    p['steps'][0]['input_checks']=["unknown['value'] > 0"]
    t=callback(c,s,t,kind='plan',plan=p)
    assert t['status']=='dispatch_ready' and t['repeat_count']==1
    assert t['last_report']['plan_preflight']['repairs'][0]['strategy']=='repair_input_checks'
    ctx=s.context(s.get(t['id']));assert ctx['plan_repair']['repairs']
    t=callback(c,s,t,kind='plan',plan=p,explanation='再次提交仍未修正同一保护路径')
    assert t['status']=='paused' and t['repeat_count']==2

def test_repair_accepts_patch_only_and_preserves_frozen_plan(fixture):
    c,s,store=fixture;p=plan(False);p['external_inputs']={'upstream':copy.deepcopy(p['steps'][0]['inputs']['data'])}
    step=p['steps'][0];step['input_sources']={'data':{'source':'input','port':'upstream'}};step['input_checks']=["upstream['value'] > 0"]
    t=callback(c,s,create(c),kind='plan',plan=p)
    raw=s.get(t['id']);raw['status']='paused';old=s.save(raw,'test_stopped')
    repaired=c.post('/api/workflow-designs/'+old['id']+'/repair',json={'revision':old['revision'],'max_calls':2}).json()
    assert repaired['status']=='awaiting_confirmation' and repaired['calls']==0 and repaired['patch_origin']=='platform'
    ctx=s.context(s.get(repaired['id']))
    assert ctx['required_result_kind']=='plan_patch' and ctx['schemas']['patch']
    assert ctx['schemas']['patch']['$defs']['PlanStepEdit']['properties']['step_id']['enum']==['calculate']
    bad=callback(c,s,repaired,kind='plan',plan=plan(False))
    assert bad['last_report']['code']=='PATCH_REQUIRED'
    fixed=callback(c,s,bad,kind='plan_patch',patch={'base_hash':ctx['patch_base']['hash'],'edits':[]})
    assert fixed['status']=='awaiting_confirmation' and fixed['plan_preflight']['valid']
    assert fixed['plan']['steps'][0]['input_checks']==["data['value'] > 0"]
    assert fixed['patch_audit']['frozen'] and s.get(old['id'])==old
    assert len(store.modules())==1 and not store.runs()

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

def test_new_interface_derives_version_without_overwriting_original(fixture):
    c,s,store=fixture;p=plan(False);p['steps'][0]['derives_from']='double@1'
    data=p['steps'][0]['inputs'].pop('data');p['steps'][0]['inputs']['measurement']=data
    p['external_inputs']={'measurement':copy.deepcopy(data)}
    p['steps'][0]['input_sources']={'measurement':{'source':'input','port':'measurement'}}
    t=callback(c,s,create(c),kind='plan',plan=p)
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x，接口改名为 measurement','declarations':'保持 W 与原公式','evidence':'原工具公式','examples':[{'inputs':{'measurement':{'value':3}},'outputs':{'result':{'value':6}}}]}).json()
    assert s.context(s.get(t['id']))['registration_target']=={'id':'double','version':2,'parent_version':1}
    m=tool().model_dump();m.update(version=2,inputs={'measurement':data},expressions={'result':"{'value': measurement['value']*2}"})
    t=callback(c,s,t,kind='module',module=m);assert t['status']=='formula_candidate'
    child=c.post('/api/workflow-designs/'+t['id']+'/register-formula',json={'revision':t['revision'],'allow_unverified':True}).json()
    assert child['plan']['steps'][0]['module_ref']=='double@2'
    original=next(m for m in store.modules() if m['version']==1)
    assert Module.model_validate(original)==tool()

def test_approved_continuation_keeps_configuration_history_and_three_call_limit(fixture):
    c,s,_=fixture;t=create(c);t=s.get(t['id']);old=copy.deepcopy(t['configuration']);t.update(status='paused',calls=3);t=s.save(t,'test_paused')
    t=c.post('/api/workflow-designs/'+t['id']+'/continue',json={'revision':t['revision']}).json()
    assert t['call_limit']==7 and t['calls']==3 and t['remaining_calls']==4 and t['configuration']['max_calls']==3
    assert t['budget_history'][0]['used']==3
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
    assert t['call_limit']==5 and t['calls']==0 and t['configuration']['max_calls']==1

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
    assert t['status']==('needs_clarification' if case=='dynamic' else 'paused' if case in ('change_required','extra_required') else 'dispatch_ready'),t.get('last_report')
    if case=='dynamic':assert t['questions']
    assert t['calls']==1
    assert t['last_report']['code'].startswith('DEPENDENCY_') or t['last_report']['code'] in ('CONTRACT_CHANGE_REQUIRES_CONFIRM','INPUT_USAGE_REVIEW_REQUIRED')
    if case=='extra_required':assert t['failure_routing']['route']=='needs_confirmation'
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


def test_clarification_does_not_renew_budget(fixture):
    c,s,_=fixture
    t=create(c);t=s.get(t['id']);t.update(status='needs_clarification',calls=4,call_limit=4,questions={'unit':'请提供依据'})
    t=s.save(t,'test_clarification')
    t=c.post('/api/workflow-designs/'+t['id']+'/answer',json={'revision':t['revision'],'answers':{'unit':'暂时没有资料'}}).json()
    assert t['status']=='paused' and t['calls']==4 and t['remaining_calls']==0
    assert not t['questions']


def test_platform_fault_cannot_be_retried_as_candidate(fixture):
    c,s,_=fixture;t=s.get(create(c)['id'])
    t=s.retry(t,RuntimeError('database failure'))
    assert t['failure_routing']['route']=='platform_fault' and t['status']=='paused'
    with pytest.raises(Conflict):
        c.post('/api/workflow-designs/'+t['id']+'/continue',json={'revision':t['revision']})


def test_same_candidate_error_stops_after_two(fixture):
    c,s,_=fixture;t=s.get(create(c)['id'])
    t.update(calls=1)
    t=s.retry(t,ValueError('步骤 ID 重复'))
    assert t['status']=='dispatch_ready'
    t.update(calls=2)
    t=s.retry(t,ValueError('步骤 ID 重复'))
    assert t['status']=='paused' and t['failure_routing']['stop_reason']=='repeated_error'
    assert t['calls']==2


def test_derived_revision_keeps_step_budget_and_exposes_impact(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'user formula','examples':[]}).json()
    old=s.get(t['id']);old.update(calls=4,call_limit=4,status='paused');old=s.save(old,'test_exhausted')
    revised=copy.deepcopy(old['plan']);revised['steps'][0]['formula_latex']='y=3x'
    child=c.post('/api/workflow-designs/'+old['id']+'/revise',json={'revision':old['revision'],'plan':revised}).json()
    assert '/plan/steps/0/formula_latex' in child['revision_changes']
    child=c.post('/api/workflow-designs/'+child['id']+'/formula',json={'revision':child['revision'],'step_id':'calculate','formula':'y=3x','declarations':'W','evidence':'changed formula','examples':[]}).json()
    assert child['calls']==4 and child['remaining_calls']==0 and child['status']=='paused'
    assert s.get(old['id'])['plan']!=child['plan']


def test_evidence_review_keeps_basis_and_remaining_budget(fixture):
    c,s,_=fixture;t=s.get(create(c)['id'])
    t.update(status='needs_clarification',phase='formula',calls=2,call_limit=4,repeat_count=1,formula_request={'step_id':'calculate'},last_report={'code':'EXAMPLE_RESULT_MISMATCH'},verification_feedback={'example':1,'execution_status':'succeeded'},failure_routing={'route':'uncertain'},questions={'failure_evidence':'provide evidence'})
    t=s.save(t,'test_mismatch')
    t=c.post('/api/workflow-designs/'+t['id']+'/review-evidence',json={'revision':t['revision'],'evidence':'Verified against independent derivation; keep basis','keep_confirmed_basis':True}).json()
    assert t['status']=='dispatch_ready' and t['calls']==2 and t['remaining_calls']==2
    assert t['repeat_count']==1 and t['evidence_reviews'][0]['keep_confirmed_basis']


def test_syntax_error_routes_to_candidate_repair_before_semantic_questions(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'W','evidence':'user formula','examples':[]}).json()
    m=tool().model_dump();m.update(id='syntax_candidate',implementation='python_subset',source='def compute(inputs):\n    return {',expressions={})
    t=callback(c,s,t,kind='module',module=m)
    assert t['last_report']['code']=='MODULE_SYNTAX'
    assert t['failure_routing']['route']=='candidate_repair' and t['status']=='dispatch_ready'
    assert not t['questions']


def test_defer_preserves_questions_and_budget(fixture):
    c,s,_=fixture;t=s.get(create(c)['id']);t.update(status='needs_clarification',calls=2,questions={'units':'please provide unit evidence'})
    t=s.save(t,'test_questions');before=copy.deepcopy(t)
    t=c.post('/api/workflow-designs/'+t['id']+'/defer',json={'revision':t['revision']}).json()
    assert t['status']=='blocked' and t['calls']==2 and t['questions']==before['questions']
    assert t['call_limit']==before['call_limit']


def test_agent_questions_cannot_be_bypassed_with_extra_calls(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='questions',questions={'unit':'please clarify unit'},explanation='unit is unknown')
    assert t['failure_routing']['route']=='needs_information'
    t=s.get(t['id']);t.update(status='blocked');t=s.save(t,'test_blocked')
    with pytest.raises(Conflict):c.post('/api/workflow-designs/'+t['id']+'/continue',json={'revision':t['revision']})
    t=c.post('/api/workflow-designs/'+t['id']+'/answer',json={'revision':t['revision'],'answers':{'unit':'use the existing W declaration'}}).json()
    assert t['calls']==1 and t['status']=='dispatch_ready' and t['failure_routing'] is None


def test_answers_do_not_bypass_repeat_stop(fixture):
    c,s,_=fixture;t=s.get(create(c)['id']);t.update(status='needs_clarification',calls=2,call_limit=4,repeat_count=2,questions={'scope':'clarify fields'})
    t=s.save(t,'test_repeat_stop')
    t=c.post('/api/workflow-designs/'+t['id']+'/answer',json={'revision':t['revision'],'answers':{'scope':'keep the original fields'}}).json()
    assert t['status']=='paused' and t['repeat_count']==2 and t['remaining_calls']==2
