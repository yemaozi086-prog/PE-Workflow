import copy
import pytest
from pe_workflow.models import DesignPlan, Port
from pe_workflow.shape_repair import preview
from pe_workflow.plan_patch import apply_patch
from pe_workflow.engine import WorkflowError
from test_planning_protocol import mini, bag, scalar, node
from test_control_contracts import fixture, stopped


def broken():
    p,registry=mini()
    raw=p.model_dump()
    raw['steps'].append({'id':'collect','title':'collect','purpose':'all rows',
        'inputs':{'loss':bag({'P':scalar('W','power')})},
        'outputs':{'summary':bag({'max_P':scalar('W','power')})},
        'input_sources':{'loss':node('scan_loop','loss')},'dependencies':['scan_loop'],
        'formula_latex':'max(P)', 'input_checks':[]})
    return DesignPlan.model_validate(raw),registry


def test_source_proven_shape_repair_is_bounded_and_atomic():
    p,_=broken();before=p.model_dump();patch,changes=preview(p)
    assert len(changes)==1 and changes[0]['after']['type']=='array'
    new,audit=apply_patch(p,patch)
    assert new.steps[-1].inputs['loss'].fields['P'].value_type.shape==['batch_rows']
    assert new.steps[-1].formula_latex==p.steps[-1].formula_latex
    assert new.steps[-1].outputs==p.steps[-1].outputs and p.model_dump()==before
    assert audit['shape_changes']==changes
    for update in ({'unit':'mW'},{'required':False},{'quantity':'voltage'},{'default':0}):
        bad=patch.model_copy(deep=True)
        field=bad.edits[0].inputs['loss'].fields['P']
        bad.edits[0].inputs['loss'].fields['P']=field.model_copy(update=update)
        with pytest.raises(WorkflowError) as error:apply_patch(p,bad)
        assert error.value.diagnostic.code=='PATCH_SHAPE_SCOPE'


def test_unknown_sources_no_guess_and_guard_regression_not_allowed():
    p,_=broken();p.steps[-1].input_sources['loss'].port='missing'
    assert not preview(p)[1]
    p,_=broken();p.steps[-1].input_checks=['loss["P"] >= 0']
    patch,_=preview(p)
    with pytest.raises(WorkflowError):apply_patch(p,patch)
    assert p.steps[-1].input_checks==['loss["P"] >= 0']


def test_registered_interface_requires_derivation():
    p,_=broken();p.steps[-1].module_ref='collector@1';patch,_=preview(p)
    new,_=apply_patch(p,patch)
    assert new.steps[-1].module_ref is None and new.steps[-1].derives_from=='collector@1'
    patch.edits[0].derive_interface=False
    with pytest.raises(WorkflowError):apply_patch(p,patch)


def test_preview_confirm_preserves_old_task_and_budget(fixture):
    c,s,store=fixture;p,registry=broken()
    for m in registry.values():store.add_module(m)
    t=stopped(s,c,p.model_dump());before=copy.deepcopy(s.get(t['id']))
    view=c.get('/api/workflow-designs/'+t['id']+'/shape-repair-preview').json()
    assert view['valid'],view
    assert s.get(t['id'])==before and view['model_calls']==0
    body={'revision':view['revision'],'patch_hash':view['patch_hash']}
    child=c.post('/api/workflow-designs/'+t['id']+'/shape-repair-confirm',json=body).json()
    assert child['status']=='awaiting_confirmation',child
    assert child['parent_id']==t['id'] and child['calls']==10 and child['remaining_calls']==0
    assert child['repeat_count']==2
    assert c.post('/api/workflow-designs/'+t['id']+'/shape-repair-confirm',json=body).json()['id']==child['id']
    assert s.get(t['id'])==before and not store.runs()


def test_stale_preview_and_changed_source_are_rejected(fixture):
    from pe_workflow.storage import Conflict
    c,s,store=fixture;p,registry=broken()
    for m in registry.values():store.add_module(m)
    t=stopped(s,c,p.model_dump())
    view=c.get('/api/workflow-designs/'+t['id']+'/shape-repair-preview').json()
    with pytest.raises(Conflict):
        c.post('/api/workflow-designs/'+t['id']+'/shape-repair-confirm',json={'revision':t['revision'],'patch_hash':'0'*64})
    patch,_=preview(p);patch.edits[0].input_sources['loss'].port='other'
    with pytest.raises(WorkflowError) as e:apply_patch(p,patch)
    assert e.value.diagnostic.code=='PATCH_SHAPE_SCOPE'


def test_string_payload_rejected_at_envelope_with_precise_path():
    from pe_workflow.submission_protocol import normalize_submission,EnvelopeError
    for payload in ('{"edits":[]}', '{broken', [], 1):
        with pytest.raises(EnvelopeError) as error:normalize_submission({'kind':'plan_patch','patch':payload})
        assert error.value.issues[0]['path']=='/patch'
        assert 'JSON 对象' in str(error.value)
