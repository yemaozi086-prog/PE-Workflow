import copy
import pytest
from test_designs import fixture
from test_tool_completion import source,queue
from pe_workflow.models import ToolCompletionConcurrency,DesignConfirm
from pe_workflow.storage import Conflict


def states(s,q):return [s.get(i['task_id']) for i in q['items']]


def test_default_two_and_maximum_ten(fixture):
    c,s,_=fixture;t=source(c,s,12)
    q,_=queue(c,t,concurrency=10);s.completion.tick()
    tasks=states(s,q)
    assert sum(t['status']=='dispatch_ready' for t in tasks)==10
    assert sum(t['status']=='completion_pending' for t in tasks)==2
    assert all(t['calls']==0 for t in tasks)
    before=[(t['calls'],t['call_limit']) for t in tasks]
    q=s.completion.get(q['id'])
    q=s.completion.configure(q['id'],ToolCompletionConcurrency(revision=q['revision'],concurrency=2))
    assert [(t['calls'],t['call_limit']) for t in states(s,q)]==before
    # Finishing a reserved item releases only its own slot; lower limits wait.
    for task in tasks[:9]:task['status']='formula_candidate';s.save(task,'finished_fixture')
    s.completion.tick()
    assert sum(t['status']=='dispatch_ready' for t in states(s,q))==2
    assert c.post('/api/tool-completion-queues/'+q['id']+'/concurrency',json={'revision':q['revision'],'concurrency':11}).status_code==422
    with pytest.raises(Conflict):s.completion.configure(q['id'],ToolCompletionConcurrency(revision=1,concurrency=3))


def test_new_default_and_legacy_default(fixture):
    c,s,_=fixture;t=source(c,s,3)
    q,body=queue(c,t,concurrency=2);body.pop('concurrency')
    q2=c.post('/api/workflow-designs/'+t['id']+'/tool-completion',json=body).json()
    assert q2['id']==q['id'] and q2['concurrency']==2
    s.completion.tick();assert sum(t['status']=='dispatch_ready' for t in states(s,q))==2
    live=s.completion.get(q['id']);old=live['revision'];live.pop('concurrency')
    with s.connect() as con:s.completion.write(con,live,old)
    assert s.completion.public(s.completion.get(q['id']))['concurrency']==1


def test_global_limit_and_round_robin_queue_fill(fixture):
    c,s,_=fixture;qs=[queue(c,source(c,s,8),concurrency=10)[0] for _ in range(2)]
    s.completion.tick()
    assert [sum(t['status']=='dispatch_ready' for t in states(s,q)) for q in qs]==[5,5]
    # Unknown delivery occupies one slot. Other steps can continue without resend.
    t=states(s,qs[0])[0];t['status']='delivery_unknown';t['calls']=1;s.save(t,'unknown_fixture')
    s.completion.tick();assert len(s.completion.active_tasks({'dispatch_ready','delivery_unknown'}))==10
    assert s.get(t['id'])['calls']==1
    another=states(s,qs[0])[1];another['status']='blocked';s.save(another,'needs_information_fixture')
    s.completion.tick();assert len(s.completion.active_tasks({'dispatch_ready','delivery_unknown'}))==10


def test_dispatch_respects_pause_inflight_retry_and_global_cap(fixture):
    c,s,_=fixture;q,_=queue(c,source(c,s,4),concurrency=2);s.completion.tick()
    tasks=states(s,q);first=tasks[0];first['status']='awaiting_worker';first['calls']=1;s.save(first,'running_fixture')
    second=tasks[1];second['status']='delivery_unknown';second=s.save(second,'unknown_fixture')
    third=tasks[2];third['status']='dispatch_ready';s.save(third,'repair_ready_fixture')
    assert not s.completion.can_dispatch(third)
    second['status']='formula_candidate';s.save(second,'finished_fixture')
    assert s.completion.can_dispatch(third)
    live=s.completion.get(q['id']);s.completion.control(q['id'],DesignConfirm(revision=live['revision']),True)
    assert not s.completion.can_dispatch(third)
    assert s.get(first['id'])['status']=='awaiting_worker'
    live=s.completion.get(q['id']);s.completion.control(q['id'],DesignConfirm(revision=live['revision']),False)
    assert s.completion.can_dispatch(third)
    for n in range(9):
        x=copy.deepcopy(first);x.update(id='global_'+str(n),revision=1,queue_id=None)
        with s.connect() as con:
            from pe_workflow.storage import canonical
            con.execute('insert into workflow_designs values(?,?,?)',(x['id'],1,canonical(x)))
    assert not s.completion.can_dispatch(third)


def test_duplicate_tick_and_missing_information_do_not_duplicate_work(fixture):
    c,s,_=fixture;t=source(c,s,4)
    raw=s.get(t['id']);raw['plan']['steps'][0]['formula_latex']='';t=s.save(raw,'missing_fixture')
    q,_=queue(c,t,concurrency=2);s.completion.tick()
    before=states(s,q);assert before[0]['status']=='blocked'
    assert sum(t['status']=='dispatch_ready' for t in before)==2
    s.completion._tick_lock.acquire()
    try:s.completion.tick()
    finally:s.completion._tick_lock.release()
    s.completion.tick();assert states(s,q)==before


def test_paused_unsent_work_releases_global_capacity(fixture):
    c,s,_=fixture;q,_=queue(c,source(c,s,10),concurrency=10);s.completion.tick()
    live=s.completion.get(q['id']);s.completion.control(q['id'],DesignConfirm(revision=live['revision']),True)
    other,_=queue(c,source(c,s,3),concurrency=2);s.completion.tick()
    assert sum(t['status']=='dispatch_ready' for t in states(s,other))==2
    assert s.completion.reserved_count()==2
    assert all(t['calls']==0 for t in states(s,q))


def test_lowered_concurrency_releases_excess_unsent_reservations(fixture):
    c,s,_=fixture;q,_=queue(c,source(c,s,10),concurrency=10);s.completion.tick()
    live=s.completion.get(q['id']);s.completion.configure(q['id'],ToolCompletionConcurrency(revision=live['revision'],concurrency=1))
    assert s.completion.reserved_count()==1
    other,_=queue(c,source(c,s,8),concurrency=10);s.completion.tick()
    assert sum(t['status']=='dispatch_ready' for t in states(s,other))==8
    assert s.completion.reserved_count()==9
