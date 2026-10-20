"""Observable transport/tool progress, without exposing model reasoning or prompts."""
import time
import json
import uuid
import os
from datetime import datetime
from pydantic import BaseModel,Field
from .storage import canonical,now


class Progress:
    def __init__(self,service):
        self.service=service
        with service.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS design_progress(task_id TEXT,call_id TEXT,document TEXT,PRIMARY KEY(task_id,call_id))')

    def read(self,t):
        with self.service.connect() as c:r=c.execute('SELECT document FROM design_progress WHERE task_id=? AND call_id=?',(t['id'],t.get('call_id',''))).fetchone()
        return json.loads(r[0]) if r else {}

    def merge(self,t,updates):
        with self.service.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT document FROM design_progress WHERE task_id=? AND call_id=?',(t['id'],t.get('call_id',''))).fetchone()
            data=json.loads(r[0]) if r else {};data.update(updates)
            c.execute('INSERT OR REPLACE INTO design_progress VALUES(?,?,?)',(t['id'],t.get('call_id',''),canonical(data)))
        return data

    def tool(self,t,name,module_ref=None):
        self.merge(t,{'last_tool':name,'last_module_ref':module_ref,'last_tool_at':now()})

    def refresh(self,key):
        t=self.service.get(key)
        if hasattr(self.service,'receipts'):t=self.service.receipts.reconcile(t)
        previous=self.read(t)
        if time.time()-previous.get('checked_epoch',0)<15:return self.snapshot(key)
        update={'checked_at':now(),'checked_epoch':time.time()}
        if not t.get('session_id'):
            update.update(observation='unavailable',query_error='此旧调用没有保存 DSH 会话编号，不能推断模型正在运行')
        else:
            try:
                from .automation import rpc
                result=rpc('session.list',{},uuid.uuid4().hex)
                row=next((r for r in result.get('items',[]) if r.get('sessionId')==t['session_id']),None)
                if not row:update.update(observation='unavailable',query_error='DSH 当前列表未包含此会话；不把未找到当作已停止')
                else:
                    seq=(row.get('projections') or {}).get('asOfSeq')
                    update.update(observation='observed',running_reported=row.get('running'),observed_seq=seq,query_error=None)
                    if seq is not None and previous.get('observed_seq') is not None and seq>previous['observed_seq']:
                        update['last_activity_observed_at']=now()
                    if row.get('running') is False and t['status'] in ('dispatching','awaiting_worker','delivery_unknown'):
                        idle_since=previous.get('idle_since') if previous.get('running_reported') is False and previous.get('observed_seq')==seq else time.time()
                        idle_since=idle_since if isinstance(idle_since,(int,float)) else time.time()
                        update['idle_since']=idle_since
                        if time.time()-idle_since>=15 and time.time()-t.get('dispatched_at',time.time())>=30:
                            from .design_lifecycle import terminal_event,pause_without_result
                            history=rpc('session.history',{'sessionId':t['session_id'],'maxMessages':8},uuid.uuid4().hex)
                            terminal=terminal_event(history,t.get('dispatched_at',time.time()))
                            if terminal:
                                pause_without_result(self.service,t,'AGENT_ENDED_WITHOUT_RESULT','DSH 本轮已结束但未提交有效候选；已保留现场，等待核对后恢复',terminal)
                                update['terminal_observed']=terminal
                    elif row.get('running') is True:update['idle_since']=None
            except Exception as error:update.update(observation='query_failed',query_error=type(error).__name__+'：进度查询失败，不能推断模型状态')
        self.merge(t,update)
        return self.snapshot(key)

    def snapshot(self,key):
        t=self.service.get(key);data=self.read(t)
        with self.service.connect() as c:n=c.execute('SELECT count(*) FROM design_late_results WHERE task_id=?',(key,)).fetchone()[0]
        elapsed=max(0,round(time.time()-t['dispatched_at'])) if t.get('dispatched_at') else 0
        try:threshold=max(60,min(3600,int(os.getenv('PE_DESIGN_WAIT_WARNING_SECONDS','600'))))
        except ValueError:threshold=600
        threshold=data.get('warning_after_seconds',threshold)
        active=t['status'] in ('dispatching','awaiting_worker','delivery_unknown')
        if not active and t.get('dispatched_at') and t.get('events'):
            try:
                ends=[datetime.fromisoformat(e['at']).timestamp() for e in t['events'] if e.get('event') in ('validation_failed','user_stopped','agent_result:plan','agent_result:workflow','agent_result:module','agent_result:blocked','agent_result:questions') and datetime.fromisoformat(e['at']).timestamp()>=t['dispatched_at']]
                elapsed=max(0,round((min(ends) if ends else datetime.fromisoformat(t['events'][-1]['at']).timestamp())-t['dispatched_at']))
            except (KeyError,ValueError,TypeError):pass
        durations=[];events=[e for e in t.get('events',[]) if e.get('event') in ('stage_requested:plan','dispatch_intent','prompt_accepted','validation_failed','agent_result:plan','agent_result:workflow','agent_result:module')]
        for a,b in zip(events,events[1:]):
            try:seconds=max(0,round((datetime.fromisoformat(b['at'])-datetime.fromisoformat(a['at'])).total_seconds()))
            except (KeyError,ValueError):continue
            durations.append({'from':a['event'],'to':b['event'],'seconds':seconds})
        return {**data,'candidate_processing':self.service.receipts.state(t),'status':t['status'],'active':active,'elapsed_seconds':elapsed,
                'warning_after_seconds':threshold,'long_wait':active and elapsed>=threshold,
                'warning':'长时间未收到可用结果；工具读取/会话心跳不等于方案完成。请检查进度或明确停止，不自动重发。' if active and elapsed>=threshold else None,
                'stage_durations':durations[-12:],
                'late_results':n,'session_recorded':bool(t.get('session_id')),
                'notice':'请求送达不证明模型仍在工作。超时不等于取消；不会自动重发。停止或切换后，迟到结果仅记录。'}


def install(app,service):
    progress=Progress(service)
    @app.get('/api/workflow-designs/{key}/progress')
    async def read(key:str,refresh:bool=False):
        if refresh:
            import asyncio
            return await asyncio.to_thread(progress.refresh,key)
        return progress.snapshot(key)
    class WaitWarning(BaseModel):
        revision:int=Field(ge=1)
        seconds:int=Field(ge=60,le=3600)
    @app.post('/api/workflow-designs/{key}/wait-warning')
    def warning(key:str,req:WaitWarning):
        t=service.get(key);service.check_revision(t,req.revision)
        progress.merge(t,{'warning_after_seconds':req.seconds})
        return progress.snapshot(key)
    return progress
