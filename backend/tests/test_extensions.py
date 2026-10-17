import pytest
from pe_workflow.extensions import Extensions
from pe_workflow.models import ExtensionProposal,Port
from pe_workflow.storage import Store
from pe_workflow.engine import WorkflowError,convert
from test_designs import fixture,create,callback,plan

def proposal(**extra):
    return ExtensionProposal(kind='unit',name='milli_demo_W',description='测试功率单位',evidence='测试定义：1 milli_demo_W = 0.001 W',unit_definition={'dimension':'power','scale':0.001,'offset':0},**extra)

def test_pending_scope_and_frozen_version(tmp_path):
    e=Extensions(lambda:Store(tmp_path/'extensions.sqlite'))
    r=e.propose('project_a',proposal())
    assert not any(x['name']=='milli_demo_W' for x in e.catalog('project_a')['items'])
    e.decision(r['id'],r['revision'],True)
    assert not any(x['name']=='milli_demo_W' for x in e.catalog('project_b')['items'])
    p=e.resolve_port(Port(type='number',quantity='power',unit='milli_demo_W'),'project_a')
    assert p.unit_definition.scale==.001 and p.extension_refs[0].version==1
    assert convert(1000,p,Port(type='number',quantity='power',unit='W'))==1
    changed=proposal();changed.unit_definition.scale=.002
    r=e.propose('project_a',changed);e.decision(r['id'],r['revision'],True)
    assert e.resolve_port(p,'project_a')==p
    assert e.resolve_port(Port(type='number',quantity='power',unit='milli_demo_W'),'project_a').extension_refs[0].version==2
    with pytest.raises(WorkflowError):e.resolve_port(p,'project_b')

def test_incomplete_and_standard_shadow_never_enabled(tmp_path):
    e=Extensions(lambda:Store(tmp_path/'extensions.sqlite'))
    r=e.propose('local',ExtensionProposal(kind='unit',name='unknown_unit'))
    assert r['status']=='needs_information'
    with pytest.raises(WorkflowError) as err:e.decision(r['id'],r['revision'],True)
    assert err.value.diagnostic.code=='EXTENSION_INCOMPLETE'
    p=proposal();p.name='W'
    with pytest.raises(WorkflowError):e.propose('local',p)
    assert not any(x['source']=='project_extension' for x in e.catalog('local')['items'])

def test_parameter_alias_and_namespace(tmp_path):
    e=Extensions(lambda:Store(tmp_path/'extensions.sqlite'))
    r=e.propose('local',ExtensionProposal(kind='parameter',name='custom_power',description='测试参数',evidence='用户测试定义',declaration=Port(type='number',quantity='power',unit='W')))
    e.decision(r['id'],r['revision'],True)
    r=e.propose('local',ExtensionProposal(kind='alias',name='my_power',description='测试别名',evidence='用户声明等义',alias_target='custom_power'))
    e.decision(r['id'],r['revision'],True)
    assert {'custom_power','my_power'}<={x['name'] for x in e.catalog('local')['items']}
    with pytest.raises(WorkflowError):e.propose('local',ExtensionProposal(kind='parameter',name='my_power'))

def test_unknown_unit_request_approval_derives_without_dsh(fixture):
    c,s,_=fixture;p=plan(False)
    p['steps'][0]['inputs']['data']['fields']['value']['unit']='milli_demo_W'
    t=callback(c,s,create(c),kind='plan',plan=p)
    t=c.post('/api/workflow-designs/'+t['id']+'/formula',json={'revision':t['revision'],'step_id':'calculate','formula':'y=2x','declarations':'测试声明','evidence':'测试公式','examples':[]}).json()
    r=t['extension_request'];before=t['calls']
    r=c.post('/api/extension-requests/'+r['id']+'/edit',json={'revision':r['revision'],'proposal':proposal().model_dump()}).json()
    child=c.post('/api/workflow-designs/'+t['id']+'/extension-decision',json={'revision':r['revision'],'approve':True}).json()
    assert child['parent_id']==t['id'] and child['id']!=t['id']
    assert child['status']=='awaiting_confirmation' and child['calls']==before
    field=child['plan']['steps'][0]['inputs']['data']['fields']['value']
    assert field['unit_definition']['scale']==.001 and field['extension_refs'][0]['version']==1
    assert s.get(t['id'])['plan']==t['plan']

def test_agent_can_request_custom_definition_without_publishing(fixture):
    c,s,store=fixture;t=create(c)
    t=callback(c,s,t,kind='extension_request',extension_request=proposal().model_dump())
    assert t['status']=='waiting_extension' and t['calls']==1
    assert len(store.modules())==1 and not store.runs()
    assert not any(x['name']=='milli_demo_W' for x in s.context(s.get(t['id']))['extension_catalog']['items'])

def test_agent_inline_definition_is_not_approval(tmp_path):
    e=Extensions(lambda:Store(tmp_path/'extensions.sqlite'))
    p=Port(type='number',quantity='power',unit='unapproved',unit_definition={'dimension':'power','scale':1,'offset':0})
    assert e.resolve_port(p,'local').unit_definition is None
    from pe_workflow.models import Module
    frozen=Module(id='legacy',version=1,name='old',inputs={'x':Port(type='number')},outputs={'y':Port(type='number')},expressions={'y':'x'},provenance='legacy serialization fixture').model_dump()
    assert 'extension_refs' not in frozen['inputs']['x'] # Do not alter immutable legacy declarations on startup.

def test_approved_agent_request_can_resume_planning(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='extension_request',extension_request=proposal().model_dump())
    r=t['extension_request']
    child=c.post('/api/workflow-designs/'+t['id']+'/extension-decision',json={'revision':r['revision'],'approve':True}).json()
    assert child['status']=='paused' and child['phase']=='plan'
    assert any(x['name']=='milli_demo_W' for x in s.context(s.get(child['id']))['extension_catalog']['items'])
    assert child['calls']==1 # Approval itself never dispatches a model call.
