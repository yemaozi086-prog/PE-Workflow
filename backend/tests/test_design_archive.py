from test_designs import fixture,create,callback,plan
from test_tool_completion import source,queue


def test_archived_tasks_hidden_retained_and_restore_is_idle(fixture):
    c,s,_=fixture;t=callback(c,s,create(c),kind='plan',plan=plan(False))
    old=s.get(t['id'])
    archived=c.post('/api/workflow-designs/'+t['id']+'/archive',json={'revision':t['revision'],'archived':True}).json()
    assert archived['archived'] and archived['plan']==old['plan'] and archived['calls']==old['calls']
    assert c.get('/api/workflow-designs').json()['count']==0
    assert c.get('/api/workflow-designs?archived=true').json()['count']==1
    assert s.all(active_only=True)==[]
    restored=c.post('/api/workflow-designs/'+t['id']+'/archive',json={'revision':archived['revision'],'archived':False}).json()
    assert not restored['archived'] and restored['status']=='awaiting_confirmation'
    assert c.get('/api/workflow-designs').json()['count']==1


def test_queue_children_stay_in_queue_not_main_history(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t)
    assert c.get('/api/workflow-designs').json()['count']==1
    child=s.get(q['items'][0]['task_id'])
    assert c.get('/api/workflow-designs/'+child['id']).status_code==200
    t=s.get(t['id']);t['archived']=True;s.save(t,'test_archive')
    s.completion.tick()
    assert s.get(child['id'])['status']=='completion_pending'


def test_restore_related_queue_keeps_it_paused(fixture):
    c,s,_=fixture;t=source(c,s);q,_=queue(c,t)
    archived=c.post('/api/workflow-designs/'+t['id']+'/archive',json={'revision':t['revision'],'archived':True}).json()
    assert all(s.get(i['task_id'])['archived'] for i in q['items'])
    assert s.completion.get(q['id'])['archived']
    restored=c.post('/api/workflow-designs/'+t['id']+'/archive',json={'revision':archived['revision'],'archived':False}).json()
    assert not restored['archived']
    current=s.completion.get(q['id']);assert not current['archived'] and current['status']=='paused'
    s.completion.tick()
    assert all(s.get(i['task_id'])['status']=='completion_pending' for i in current['items'])
