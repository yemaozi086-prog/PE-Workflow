"""Call fencing, explicit recovery actions and bounded transport failures."""
import hashlib,hmac,json,time
from pydantic import BaseModel,Field
from .storage import Conflict,now,canonical,digest

ACTIVE={'dispatching','awaiting_worker','delivery_unknown'}
SAFE_CODES={'RESULT_ENVELOPE','TOOL_ARGUMENT_OBJECT','AGENT_ENDED_WITHOUT_RESULT','UNDECLARED_TRANSFORM'}


def actions(t):
    if t.get('archived') or t.get('superseded_by'):return []
    if t.get('status') in ACTIVE:return [{'id':'reconcile','label':'核对 DSH 是否已结束（不调用模型）','model_call':False}]
    if t.get('status') not in ('paused','blocked') or t.get('questions'):return []
    route=(t.get('failure_routing') or {}).get('route')
    code=(t.get('last_report') or {}).get('code')
    if code=='CANDIDATE_PROCESSING_INTERRUPTED':return [{'id':'recheck-received-candidate','label':'复检已收到的候选（不调用模型）','model_call':False}]
    if code=='TOOL_RUNTIME_MISMATCH':return [{'id':'recheck-runtime','label':'复检已安装预设（不调用模型）','model_call':False}]
    safe=route in (None,'candidate_repair') or code in SAFE_CODES
    if not safe:return []
    remaining=max(0,t.get('call_limit',4)-t.get('calls',0))
    if remaining and t.get('repeat_count',0)<2:
        return [{'id':'resume','label':f'使用剩余 {remaining} 次继续修正','model_call':True,'remaining':remaining,'additional_calls':0}]
    return [{'id':'grant','label':'核对后追加修正次数','model_call':True,'remaining':remaining,'maximum_additional':3,'requires_approval':True}]


def close_call(service,t,reason):
    if not all(t.get(k) for k in ('id','call_id','token')):return
    with service.connect() as c:
        c.execute('INSERT OR IGNORE INTO design_closed_calls VALUES(?,?,?,?,?)',(t['id'],t['call_id'],hashlib.sha256(t['token'].encode()).hexdigest(),reason,now()))


def old_result(service,t,req):
    call,token=req.get('call_id'),req.get('token')
    if not isinstance(call,str) or not isinstance(token,str):return None
    with service.connect() as c:
        row=c.execute('SELECT credential_hash FROM design_closed_calls WHERE task_id=? AND call_id=?',(t['id'],call)).fetchone()
        if not row or not hmac.compare_digest(row[0],hashlib.sha256(token.encode()).hexdigest()):return None
        payload={k:v for k,v in req.items() if k!='token'}
        c.execute('INSERT OR IGNORE INTO design_late_results VALUES(?,?,?,?,?)',(t['id'],call,digest(payload),canonical(payload),now()))
    return {'status':'recorded_late_result','applied':False,'active_design_id':t.get('superseded_by')}


def terminal_event(history,since):
    events=[row.get('event',row) for row in history.get('events',[]) if isinstance(row,dict)]
    events=[e for e in events if e.get('type') in ('turn/start','turn/end','prompt/queued','prompt/accepted','user/message')]
    if not events or events[-1].get('type')!='turn/end':return None
    e=events[-1];stamp=e.get('time')
    if isinstance(stamp,str):
        try:
            from datetime import datetime
            stamp=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
        except ValueError:return None
    if not isinstance(stamp,(int,float)):return None
    stamp=stamp/1000 if stamp>10**11 else stamp
    if stamp<since:return None
    return {'seq':e.get('seq'),'time':stamp,'type':'turn/end'}


def pause_without_result(service,t,code,message,evidence=None):
    live=service.get(t['id'])
    if live.get('call_id')!=t.get('call_id') or live['revision']!=t['revision'] or live['status'] not in ACTIVE:return live
    # A received candidate is being validated or already has a decision. Never fence it.
    with service.connect() as c:
        if c.execute('SELECT 1 FROM design_candidate_receipts WHERE task_id=? AND call_id=? LIMIT 1',(t['id'],t.get('call_id',''))).fetchone():return live
    close_call(service,live,code)
    route='platform_fault' if code=='TOOL_RUNTIME_MISMATCH' else 'candidate_repair'
    live.update(status='paused',worker_result_closed=True,last_report={'code':code,'path':'/transport','message':message},
                failure_routing={'route':route,'automatic':False,'reason':message,'next_action':'核对后使用原剩余额度恢复；额度耗尽时明确追加','evidence':evidence or {'code':code}},message=message)
    return service.save(live,'call_closed_without_submission')


def install(app,service):
    with service.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS design_closed_calls(task_id TEXT,call_id TEXT,credential_hash TEXT,reason TEXT,created_at TEXT,PRIMARY KEY(task_id,call_id))')
    class Resume(BaseModel):
        revision:int=Field(ge=1)
    @app.post('/api/workflow-designs/{key}/resume')
    def resume(key:str,req:Resume):
        from .designs import configuration
        t=service.get(key);service.check_revision(t,req.revision)
        if not any(a['id']=='resume' for a in actions(t)):raise Conflict('当前不能使用剩余额度恢复；请先核对诊断或明确批准追加')
        close_call(service,t,'user_resumed')
        t.setdefault('configuration_history',[]).append({'at':now(),'configuration':t.get('configuration')})
        t.update(status='dispatch_ready',configuration=configuration(),worker_result_closed=False,message='按原剩余额度恢复，未增加次数；只修正允许的候选范围')
        t.pop('callback_hash',None)
        return service.public(service.save(t,'user_resumed_remaining_budget'))
    @app.post('/api/workflow-designs/{key}/reconcile')
    async def reconcile(key:str,req:Resume):
        import asyncio
        t=service.get(key);service.check_revision(t,req.revision)
        await asyncio.to_thread(service.progress.refresh,key)
        return service.public(service.get(key))
    @app.post('/api/workflow-designs/{key}/recheck-runtime')
    def recheck(key:str,req:Resume):
        from pathlib import Path
        from .designs import configuration,PRESET
        from .preset_runtime import tool_filename
        t=service.get(key);service.check_revision(t,req.revision)
        if not any(a['id']=='recheck-runtime' for a in actions(t)):raise Conflict('当前无需复检预设')
        config=configuration();source=(PRESET/'flux-design-tools.js').read_bytes();home=Path.home()/'.dsh/.agent-presets/flux-design-v3'
        if not (home/tool_filename(source)).exists() or (home/tool_filename(source)).read_bytes()!=source or not (home/'agent.cordis.yml').exists() or (home/'agent.cordis.yml').read_bytes()!=config['prompt'].encode('utf-8'):
            raise Conflict('已安装预设仍与项目不一致；未调用模型，请先重新安装预设')
        t.update(last_report={'code':'RUNTIME_RECHECK_PASSED','path':'/transport','message':'预设复检通过；旧会话保持关闭，请使用新调用恢复'},failure_routing={'route':'candidate_repair','automatic':False},message='预设复检通过，尚未调用模型；可使用剩余额度或明确追加')
        return service.public(service.save(t,'runtime_rechecked'))
    @app.post('/api/workflow-designs/{key}/transport-failure')
    def failure(key:str,req:dict):
        t=service.get(key)
        if req.get('call_id')!=t.get('call_id') or not isinstance(req.get('token'),str) or not hmac.compare_digest(req['token'],t.get('token','')):raise Conflict('调用凭证不匹配')
        code=req.get('code')
        if code not in ('TOOL_ARGUMENT_OBJECT','TOOL_RUNTIME_MISMATCH'):raise Conflict('未知传输错误')
        message={'TOOL_ARGUMENT_OBJECT':'工具参数不是完整对象，未收到候选；本轮已关闭，可核对后恢复','TOOL_RUNTIME_MISMATCH':'工具运行版本不匹配，本轮已关闭；须更新预设后再恢复'}[code]
        return {**service.public(pause_without_result(service,t,code,message)),'stop_required':True}
