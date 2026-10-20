import copy
from test_designs import fixture
from test_input_usage import setup


def test_one_call_recovery_preserves_candidate_and_hides_reference_answers(fixture):
    c,s,store=fixture
    _,q,failed=setup(c,s)
    old=s.get(failed['id'])
    old.update(last_report={'code':'RESULT_ENVELOPE','path':'/module','message':'object required'},calls=4,call_limit=4)
    old['previous_candidate']['module']='malformed original string'
    old=s.save(old,'test_envelope_failure')
    before=copy.deepcopy(old);modules=store.modules()
    examples=[{'inputs':{'data':{'value':v,'carry':7,'check':1}},'outputs':{'result':{'value':v*2}}} for v in (3,5)]
    body={'revision':old['revision'],'confirm':True,'max_calls':1,'examples':examples,'evidence':'Trusted independent test program'}
    url='/api/workflow-designs/'+old['id']+'/formula-recovery'
    response=c.post(url,json=body)
    assert response.status_code==200,response.text
    child=s.get(response.json()['id'])
    assert child['calls']==4 and child['call_limit']==5
    assert child['previous_candidate']==before['previous_candidate']
    assert child['plan']==before['plan'] and s.get(old['id'])==before
    assert store.modules()==modules
    context=s.context(child)
    assert 'examples' not in context['formula_request']
    assert context['formula_request']['verification_inputs']==[e['inputs'] for e in examples]
    assert c.post(url,json=body).json()['id']==child['id']
    assert c.post(url,json={**body,'max_calls':2}).status_code==422
    assert s.completion.get(q['id'])['items'][0]['task_id']==child['id']
