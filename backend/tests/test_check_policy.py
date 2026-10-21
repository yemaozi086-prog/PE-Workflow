import copy
from pe_workflow.models import Port,Module
from pe_workflow.plan_audit import preflight
from test_designs import fixture,plan,tool
from test_tool_completion import source,queue


def test_legacy_unused_stop_rechecks_without_model_or_contract_change(fixture):
    c,s,store=fixture;t=source(c,s,count=1);q,_=queue(c,t)
    task=s.get(q['items'][0]['task_id']);m=tool().model_dump();m['id']='policy_original'
    extra=Port(type='number',quantity='power',unit='W').model_dump()
    task['plan']['steps'][0]['inputs']['data']['fields']['carried']=extra
    m['inputs']=copy.deepcopy(task['plan']['steps'][0]['inputs'])
    task.update(status='paused',calls=1,repeat_count=2,last_report={'code':'INPUT_USAGE_REVIEW_REQUIRED','path':'/module/inputs','message':'legacy'},previous_candidate={'kind':'module','module':m})
    task=s.save(task,'fixture_legacy_policy');before=copy.deepcopy(task);modules=store.modules()
    url='/api/tool-completion-queues/'+q['id']+'/recovery'
    view=c.get(url).json();assert view['items'][0]['action']=='revalidate_candidate'
    assert view['items'][0]['remaining_calls']==0 and s.get(task['id'])==before
    body={'revision':view['revision'],'preview_hash':view['preview_hash'],'confirm':True}
    result=c.post(url,json=body).json();current=s.get(task['id'])
    assert current['status']=='formula_candidate',current.get('last_report')
    assert current['calls']==1 and current['repeat_count']==2
    assert current['plan']==before['plan'] and current['candidate']['inputs']==m['inputs']
    assert current['candidate']['expressions']==m['expressions']
    assert current['formula_validation']['dependency_audit']['warnings']
    revision=current['revision']
    assert c.post(url,json=body).status_code==200 and s.get(task['id'])['revision']==revision
    assert store.modules()==modules and not store.runs()


def test_all_declaration_errors_are_reported_before_tool_generation():
    from pe_workflow.models import DesignPlan
    p=plan(False);fields=p['steps'][0]['outputs']['result']['fields']
    fields['count']=Port(type='number',quantity='count',unit='1').model_dump()
    fields['reason']=Port(type='string').model_dump()
    report=preflight(DesignPlan.model_validate(p))
    assert not report['valid']
    assert any(x['code']=='UNIT_DECLARATION' and x['path'].endswith('/count') for x in report['issues'])
    assert any(x['code']=='FIELD_SEMANTIC_REQUIRED' and x['path'].endswith('/reason') for x in report['issues'])


def test_unused_input_warning_does_not_mask_unsafe_access():
    from pe_workflow.dependency_audit import audit_dependencies
    m=tool();m.inputs['data'].fields['unused']=Port(type='number',quantity='power',unit='W')
    m.inputs['data'].fields['optional']=Port(type='number',quantity='power',unit='W',required=False)
    m.expressions['result']="{'value':data['optional']}"
    report=audit_dependencies(m)
    assert report['status']=='failed' and report['warnings']
    assert report['unsafe_optional_fields']==['data.optional']
