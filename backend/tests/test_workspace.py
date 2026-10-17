import copy
import json
import pytest
from pe_workflow.models import DraftRequest,WorkspaceCopy
from pe_workflow.storage import Store,Conflict
from pe_workflow.workspace import Workspace
from pe_workflow.examples import demo,builtins
from pe_workflow.assembly_form import suggestions


@pytest.fixture
def example(tmp_path):
    store=Store(tmp_path/'workspace.sqlite');w=Workspace(store);wf,inputs=demo()
    for module in builtins():store.seed_module(module)
    store.save(wf,inputs,0)
    return store,w,wf,inputs


def test_copy_is_independent_pinned_draft_without_runs(example):
    store,w,wf,inputs=example
    before=store.workflow(wf.id)
    copied=w.copy(wf.id,WorkspaceCopy(name='独立方案'))
    assert copied['workflow']['id']!=wf.id and copied['revision']==0
    assert copied['workflow']['nodes']==before['workflow']['nodes']
    assert copied['inputs']==inputs and copied['origin']=={'workflow_id':wf.id,'revision':1}
    request=DraftRequest(workflow=copied['workflow'],inputs={**inputs,'i_rms':12},expected_draft_revision=1,base_revision=0)
    w.save_draft(copied['workflow']['id'],request)
    assert store.workflow(wf.id)==before and not store.runs()


def test_invalid_numeric_draft_survives_and_does_not_create_version(example):
    store,w,wf,inputs=example
    edited=copy.deepcopy(inputs);edited[next(iter(edited))]=''
    w.save_draft(wf.id,DraftRequest(workflow=wf,inputs=edited,expected_draft_revision=0,base_revision=1))
    restored=w.load(wf.id)
    assert restored['inputs']==edited and restored['draft_revision']==1 and restored['revision']==1
    assert store.workflow(wf.id)['inputs']==inputs
    with pytest.raises(Conflict,match='其他页面'):w.save_draft(wf.id,DraftRequest(workflow=wf,inputs=inputs,expected_draft_revision=0,base_revision=1))
    with pytest.raises(Conflict,match='草稿已改变'):store.save(wf,inputs,1,0)
    assert store.save(wf,inputs,1,1)==2
    assert w.load(wf.id)['draft_revision']==0


def test_rename_does_not_mutate_old_version_or_run(example):
    store,w,wf,inputs=example;old=store.workflow(wf.id)
    w.rename(wf.id,'新显示名称')
    assert store.workflow(wf.id)==old
    assert w.list()[0]['name']=='新显示名称' and store.workflows()[0]['name']=='新显示名称'


def test_archive_restore_preserves_draft_modules_and_versions(example):
    store,w,wf,inputs=example
    w.save_draft(wf.id,DraftRequest(workflow=wf,inputs=inputs,expected_draft_revision=0,base_revision=1))
    modules=store.modules();w.archive(wf.id,True)
    assert not w.list() and not store.workflows() and w.list(True)[0]['id']==wf.id
    with pytest.raises(Conflict,match='回收站'):store.save(wf,inputs,1)
    with pytest.raises(Conflict,match='回收站'):w.save_draft(wf.id,DraftRequest(workflow=wf,inputs=inputs,expected_draft_revision=1,base_revision=1))
    w.archive(wf.id,False)
    assert w.load(wf.id)['draft_revision']==1 and store.modules()==modules and store.workflow(wf.id)['revision']==1
    assert store.workflow_epoch(wf.id)==2


def test_late_agent_publication_cannot_bypass_archive_then_restore(example):
    store,w,wf,inputs=example
    w.archive(wf.id,True);w.archive(wf.id,False)
    with pytest.raises(Conflict,match='归档/恢复'):
        store.commit_change(None,wf,inputs,{'id':'stale','workspace_epoch':0}, {})
    assert store.workflow(wf.id)['revision']==1


def test_copy_explicit_draft_keeps_invalid_values_and_origin_hash(example):
    store,w,wf,inputs=example;edited={**inputs,'i_rms':'not a number'}
    w.save_draft(wf.id,DraftRequest(workflow=wf,inputs=edited,expected_draft_revision=0,base_revision=1))
    copied=w.copy(wf.id,WorkspaceCopy(name='草稿副本',use_draft=True))
    assert copied['inputs']==edited and copied['origin']['draft_revision']==1
    assert len(copied['origin']['snapshot_hash'])==64


@pytest.mark.parametrize('text,field,value',[
    ('输入电压390 V','Vin',390),('输出电压400 V','Vout',400),
    ('输出功率1.5 kW','Pout',1500),('Vout=500mV','Vout',.5)])
def test_natural_language_suggestions_are_explicit_and_unit_aware(text,field,value):
    proposal=suggestions(text)[0]
    assert proposal['path']==['point',field] and proposal['value']==value
    assert proposal['status']=='suggestion' and proposal['evidence']


def test_missing_or_unknown_units_and_multiple_values_are_not_guessed():
    assert suggestions('输出电压400')[0]['status']=='needs_unit'
    assert suggestions('输出功率2 MW')[0]['status']=='needs_unit'
    proposals=suggestions('输出电压350 V，输出电压400 V')
    assert len(proposals)==2 and all(p['status']=='conflict' for p in proposals)


def test_late_draft_cannot_overwrite_new_published_base(example):
    store,w,wf,inputs=example;store.save(wf,inputs,1)
    with pytest.raises(Conflict,match='已保存版本已改变'):
        w.save_draft(wf.id,DraftRequest(workflow=wf,inputs=inputs,expected_draft_revision=0,base_revision=1))


def test_background_poll_only_loads_active_tasks_and_summary_hides_payloads(example):
    from pe_workflow.automation import Journal
    store,_,_,_=example;journal=Journal(store)
    journal.save({'id':'active','status':'queued','token':'secret','request':{'source':'large'},'result':{'trace':[1,2]}},{'event':'created'})
    journal.save({'id':'done','status':'applied','token':'secret','request':{'source':'large'},'result':{'trace':[1,2]}},{'event':'created'})
    assert [t['id'] for t in journal.active()]==['active']
    assert all('token' not in t and 'request' not in t and 'result' not in t for t in journal.summaries())
    assert journal.get('done')['result']['trace']==[1,2]
