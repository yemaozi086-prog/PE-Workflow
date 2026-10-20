import copy,json
import pytest
from pe_workflow.models import Port,Module,Workflow,Node,Binding
from pe_workflow.engine import execute,validate_value,check_port,compatible,WorkflowError
from pe_workflow.expressions import evaluate,parse
from test_planning_protocol import array,bag


def ports():
    a=Port.model_validate(array('V','voltage','groups'));a.missing_when='valid'
    return Port(type='flat_dict',fields={'value':a,'valid':array('1','ratio','groups')})


def test_masked_values_missing_semantics_is_json_safe():
    m=Module(id='missing_test',version=1,name='missing test',contract_version='3.0',provenance='test only',
        inputs={'data':bag({'n':array('V','voltage','groups'),'d':array('1','ratio','groups')})},outputs={'result':ports()},
        expressions={'result':"{'valid': (data['d'] > 0) * 1, 'value': pe.masked_values(data['n'] / (data['d'] + ((data['d'] == 0) * 1)), (data['d'] > 0) * 1)}"})
    w=Workflow(schema_version='3.0',id='missing_wf',name='missing',inputs=m.inputs,nodes=[Node(id='calc',module_id=m.id,module_version=1,inputs={'data':Binding(source='input',port='data')})],outputs={'result':Binding(source='node',node='calc',port='result')},output_ports=m.outputs)
    result=execute(w,{m.id+'@1':m},{'data':{'n':[10,0,20],'d':[2,0,4]}})
    assert result['result']=={'value':[5,None,5],'valid':[1,0,1]}
    assert 'null' in json.dumps(result,allow_nan=False)
    with pytest.raises(WorkflowError):execute(w,{m.id+'@1':m},{'data':{'n':[None],'d':[0]}})


@pytest.mark.parametrize('value,mask',[([0],[0]),([None],[1]),([None],[2]),([None],[]),([float('nan')],[0])])
def test_invalid_missing_or_fake_zero_rejected(value,mask):
    with pytest.raises((WorkflowError,ValueError)):validate_value({'value':value,'valid':mask},ports(),'/test')


def test_plain_arrays_still_reject_missing_and_policy_is_explicit():
    p=ports();plain=p.fields['value'].model_copy(update={'missing_when':None})
    with pytest.raises((WorkflowError,ValueError)):validate_value([None],plain,'/plain')
    with pytest.raises(WorkflowError):compatible(p.fields['value'],plain,'/connection')
    p.fields['value'].missing_when='unknown'
    with pytest.raises(WorkflowError):check_port(p,'/contract')


def test_supported_array_logic_replacements():
    import numpy as np
    values={'a':np.array([True,False,True]),'b':np.array([False,False,True])}
    assert evaluate(parse('((a * 1) * (b * 1)) > 0'),values).tolist()==[False,False,True]
    assert evaluate(parse('((a * 1) + (b * 1)) > 0'),values).tolist()==[True,False,True]


def test_guard_quote_style_is_not_a_contract_change():
    from pe_workflow.plan_audit import same_checks
    assert same_checks(['limits["V_target"] > 0'],["limits['V_target']>0"])
    assert not same_checks(['limits["V_target"] > 0'],['limits["V_target"] >= 0'])


from test_designs import fixture,callback,create,plan,tool
from test_tool_completion import queue


def test_confirmed_missing_policy_derives_task_without_rewriting_source(fixture):
    c,s,_=fixture;p=plan(False);step=p['steps'][0]
    output=ports();output.fields['value'].missing_when=None
    step['outputs']={'result':output.model_dump()}
    step['input_sources']={'data':Binding(source='input',port='data').model_dump()}
    p['external_inputs']=copy.deepcopy(step['inputs'])
    t=callback(c,s,create(c),kind='plan',plan=p);q,_=queue(c,t);s.completion.tick()
    task=s.get(q['items'][0]['task_id']);m=tool().model_dump();m['id']='missing_policy_tool';m['outputs']=step['outputs']
    m['expressions']={'result':"{'valid': numpy.asarray([0]), 'value': data['value'] & 1}"}
    failed=callback(c,s,task,kind='module',module=m);assert failed['status']=='paused'
    before=copy.deepcopy(s.get(failed['id']))
    body={'revision':failed['revision'],'port':'result','mask':'valid','fields':['value'],
          'evidence':'User requires NaN for invalid recommendations','confirm_missing_nan':True}
    child=c.post('/api/workflow-designs/'+failed['id']+'/missing-output-revision',json=body).json()
    assert child['id']!=failed['id'] and child['calls']==failed['calls'] and child['call_limit']==failed['call_limit'],child
    assert child['previous_candidate']['module']['expressions']==m['expressions']
    assert child['previous_candidate']['module']['outputs']['result']['fields']['value']['missing_when']=='valid'
    assert s.get(failed['id'])==before
    assert c.post('/api/workflow-designs/'+failed['id']+'/missing-output-revision',json=body).json()['id']==child['id']
