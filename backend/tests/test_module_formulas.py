import copy
from test_designs import fixture,tool
from test_tool_completion import source,queue,candidate
from pe_workflow.models import ToolCompletionRegister,BindingAssemblyConfirm,DesignConfirm


def test_formula_provenance_is_read_only_and_scoped_to_exact_version(fixture):
    c,s,store=fixture
    assert c.get('/api/module-formulas').json()['items']=={}
    t=source(c,s,1);q,_=queue(c,t);candidate(c,s,q,0);s.completion.tick()
    q=s.completion.get(q['id'])
    r=s.completion.register(q['id'],ToolCompletionRegister(revision=q['revision'],task_ids=[q['items'][0]['task_id']],allow_unverified=True))
    modules=copy.deepcopy(store.modules())
    result=c.get('/api/module-formulas').json()
    assert result['items']['completion_0@1']['formula_latex']=='y=2x'
    assert 'double@1' not in result['items'] and store.modules()==modules
    # Registered notes survive archival without changing immutable module data.
    task=s.get(q['items'][0]['task_id']);task['archived']=True;s.save(task,'archive_fixture')
    assert c.get('/api/module-formulas').json()['items']['completion_0@1']['formula_latex']=='y=2x'
    # A mismatched implementation must not borrow a formula from an old candidate.
    task=s.get(task['id']);task['candidate']['source']='different';s.save(task,'mismatch_fixture')
    assert 'completion_0@1' not in c.get('/api/module-formulas').json()['items']


def test_workflow_formula_attaches_only_to_matching_saved_node(fixture):
    c,s,_=fixture
    m=tool();t=source(c,s,1);raw=s.get(t['id']);raw['workflow']={'id':'saved_demo','nodes':[{'id':'calculate','kind':'compute','module_id':m.id,'module_version':1}]}
    raw['plan']['steps'][0]['module_ref']='double@1';s.save(raw,'saved_formula_fixture')
    d=c.get('/api/module-formulas?workflow_id=saved_demo').json()
    assert d['nodes']['calculate']['module_ref']=='double@1' and d['nodes']['calculate']['formula_latex']=='y=2x'
    assert c.get('/api/module-formulas?workflow_id=other').json()['nodes']=={}
