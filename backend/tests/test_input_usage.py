import copy
import pytest
from test_designs import fixture,callback,create,plan,tool
from test_tool_completion import queue
from pe_workflow.models import Port,Module,Workflow,Node,Binding
from pe_workflow.input_usage import proposal
from pe_workflow.dependency_audit import audit_dependencies
from pe_workflow.engine import execute,WorkflowError


def setup(c,s):
    p=plan(False);step=p['steps'][0]
    step['inputs']['data']['fields']['carry']=Port(type='number',quantity='power',unit='W').model_dump()
    step['inputs']['data']['fields']['check']=Port(type='number',quantity='power',unit='W').model_dump()
    step['input_checks']=['data["check"] > 0']
    step['input_sources']={'data':Binding(source='input',port='data').model_dump()}
    p['external_inputs']=copy.deepcopy(step['inputs'])
    t=callback(c,s,create(c),kind='plan',plan=p);q,_=queue(c,t)
    s.completion.tick();task=s.get(q['items'][0]['task_id'])
    m=tool().model_dump();m['id']='usage_double';m['inputs']=copy.deepcopy(step['inputs'])
    m['inputs']['data']['fields']['carry']['required']=False
    failed=callback(c,s,task,kind='module',module=m)
    assert failed['status']=='paused',failed
    return t,q,failed


def test_guard_dependency_is_not_dummy_formula_read():
    m=tool();m.inputs['data'].fields['check']=Port(type='number',quantity='power',unit='W')
    m.input_checks=['data["check"] > 0']
    audit=audit_dependencies(m)
    assert audit['status']=='passed' and audit['check_fields']==['data.check']
    assert 'data.check' not in audit['read_fields']
    w=Workflow(schema_version='3.0',id='guard_tool',name='guard',inputs=m.inputs,nodes=[Node(id='calc',module_id=m.id,module_version=1,inputs={'data':Binding(source='input',port='data')})],outputs={'result':Binding(source='node',node='calc',port='result')},output_ports=m.outputs)
    trace=[]
    with pytest.raises(WorkflowError) as e:execute(w,{m.id+'@1':m},{'data':{'value':3,'check':0}},trace)
    assert e.value.diagnostic.code=='INPUT_PRECONDITION'
    assert execute(w,{m.id+'@1':m},{'data':{'value':3,'check':1}})['result']['value']==6


def test_usage_preview_confirm_rechecks_without_calls_or_registration(fixture):
    c,s,store=fixture;t,q,failed=setup(c,s)
    before=copy.deepcopy(s.get(failed['id']));modules=store.modules()
    url='/api/tool-completion-queues/'+q['id']
    view=c.get(url+'/input-usage-preview').json()
    assert view['plan_check']['valid'] and view['items'][0]['eligible'],view
    review=view['items'][0]['review']
    assert review['changes'][0]['field']=='data.carry'
    assert s.get(failed['id'])==before
    body={'revision':view['revision'],'preview_hash':view['preview_hash'],'confirm':True}
    result=c.post(url+'/input-usage-confirm',json=body).json()
    item=result['items'][0];child=s.get(item['task_id'])
    assert child['status']=='formula_candidate',child
    assert child['calls']==before['calls'] and child['call_limit']==before['call_limit']
    assert child['candidate']['source']==(before['previous_candidate']['module']['source'])
    assert 'carry' not in child['candidate']['inputs']['data']['fields']
    assert child['candidate']['input_checks']==['data["check"] > 0']
    assert s.get(failed['id'])==before and store.modules()==modules and not store.runs()
    assert c.post(url+'/input-usage-confirm',json=body).json()['items'][0]['task_id']==child['id']
    consolidated=s.get(result['result_design_id'])
    assert 'carry' in consolidated['plan']['external_inputs']['data']['fields']
    registered=c.post(url+'/register',json={'revision':result['revision'],'task_ids':[child['id']],'allow_unverified':True}).json()
    assert registered['queue']['items'][0]['registered']


def test_all_carried_fields_remove_empty_input_bag_and_source(fixture):
    c,s,store=fixture
    p=plan(False);step=p['steps'][0]
    step['inputs']['unused']=Port(type='flat_dict',fields={
        'noise':Port(type='number',quantity='power',unit='W')}).model_dump()
    p['external_inputs']=copy.deepcopy(step['inputs'])
    step['input_sources']={
        'data':Binding(source='input',port='data').model_dump(),
        'unused':Binding(source='input',port='unused').model_dump(),
    }
    t=callback(c,s,create(c),kind='plan',plan=p);q,_=queue(c,t)
    s.completion.tick();task=s.get(q['items'][0]['task_id'])
    m=tool().model_dump();m['id']='usage_empty_bag';m['inputs']=copy.deepcopy(step['inputs'])
    failed=callback(c,s,task,kind='module',module=m)
    assert failed['status']=='formula_candidate'
    legacy=s.get(failed['id']);legacy.update(status='paused',previous_candidate={'kind':'module','module':legacy['candidate']},last_report={'code':'INPUT_USAGE_REVIEW_REQUIRED','path':'/module/inputs','message':'legacy redundancy stop'})
    failed=s.save(legacy,'fixture_legacy_stop')
    url='/api/tool-completion-queues/'+q['id'];view=c.get(url+'/input-usage-preview').json()
    assert view['plan_check']['valid'],view['plan_check']
    assert [x['field'] for x in view['items'][0]['review']['changes']]==['unused.noise']
    result=c.post(url+'/input-usage-confirm',json={'revision':view['revision'],'preview_hash':view['preview_hash'],'confirm':True}).json()
    child=s.get(result['items'][0]['task_id'])
    assert child['status']=='formula_candidate',child
    assert 'unused' not in child['candidate']['inputs']
    step=next(x for x in child['plan']['steps'] if x['id']=='calculate')
    assert 'unused' not in step['inputs'] and 'unused' not in step['input_sources']
    consolidated=s.get(result['result_design_id'])
    consolidated_step=next(x for x in consolidated['plan']['steps'] if x['id']=='calculate')
    assert 'unused' not in consolidated_step['inputs'] and 'unused' not in consolidated_step['input_sources']


def test_semantic_or_output_changes_never_become_usage_repairs(fixture):
    c,s,_=fixture;_,_,failed=setup(c,s);t=s.get(failed['id'])
    t['previous_candidate']['module']['inputs']['data']['fields']['value']['unit']='mW'
    with pytest.raises(ValueError):proposal(t)


def test_overdeclared_new_candidate_routes_to_review_not_automatic_retry(fixture):
    c,s,_=fixture;_,q,failed=setup(c,s);t=s.get(failed['id'])
    m=t['previous_candidate']['module'];m['inputs']['data']['fields']['carry']['required']=True
    t=callback(c,s,t,kind='module',module=m)
    assert t['status']=='formula_candidate',t
    assert t['formula_validation']['dependency_audit']['warnings']
    assert t['candidate']['inputs']['data']['fields']['carry']['required']


def test_samples_preserve_original_and_project_only_unused_inputs(fixture):
    c,s,_=fixture;_,q,failed=setup(c,s);t=s.get(failed['id'])
    original=[{'inputs':{'data':{'value':3,'check':1,'carry':999}},'outputs':{'result':{'value':6}}}]
    t['formula_request']['examples']=copy.deepcopy(original);s.save(t,'test_add_samples')
    url='/api/tool-completion-queues/'+q['id'];view=c.get(url+'/input-usage-preview').json()
    result=c.post(url+'/input-usage-confirm',json={'revision':view['revision'],'preview_hash':view['preview_hash'],'confirm':True}).json()
    child=s.get(result['items'][0]['task_id'])
    assert child['status']=='formula_candidate',child
    assert child['usage_original_examples']==original
    assert child['formula_request']['examples'][0]['outputs']==original[0]['outputs']
    assert child['formula_request']['examples'][0]['inputs']=={'data':{'value':3,'check':1}}
    assert len(child['formula_validation']['independent_examples'])==1


def test_batch_checks_module_guard_for_all_rows_before_compute():
    from test_planning_protocol import mini
    from pe_workflow.plan_controls import compile_plan
    p,registry=mini();registry['copper@1'].input_checks=['item["I"] >= 0']
    w=compile_plan(p,'module_guard_batch',registry=registry)
    trace=[]
    with pytest.raises(WorkflowError) as e:
        execute(w,registry,{'scan':{'currents':[1,-1,2]},'device':{'R':0.5}},trace)
    assert e.value.diagnostic.code=='INPUT_PRECONDITION'
    assert not any(row['node_id']=='copper' for row in trace)


def test_recheck_keeps_real_code_failure_paused_without_new_calls(fixture):
    c,s,_=fixture;_,q,failed=setup(c,s);t=s.get(failed['id'])
    t['previous_candidate']['module']['expressions']['result']="{'value': data['value'] & 2}"
    s.save(t,'test_unsupported_operator')
    url='/api/tool-completion-queues/'+q['id'];view=c.get(url+'/input-usage-preview').json()
    result=c.post(url+'/input-usage-confirm',json={'revision':view['revision'],'preview_hash':view['preview_hash'],'confirm':True}).json()
    child=s.get(result['items'][0]['task_id'])
    assert child['status']=='paused' and child['calls']==failed['calls']
    assert child['last_report']['code']=='MODULE_SYNTAX'
    assert 'BitAnd' in child['last_report']['message']
    assert result['running'] is False


def test_description_named_output_is_not_treated_as_metadata():
    from pe_workflow.input_usage import contract
    assert contract({'description':Port(type='number',quantity='power',unit='W')})
