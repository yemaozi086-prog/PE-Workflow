"""Credential-free raw candidate receipts, saved BEFORE model/schema parsing."""
import json
import threading
from contextlib import contextmanager
from .storage import canonical,digest,now


class Receipts:
    def __init__(self,service):
        self.service=service
        self._processing=set();self._lock=threading.RLock()
        with service.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS design_candidate_receipts(task_id TEXT,call_id TEXT,payload_hash TEXT,document TEXT,created_at TEXT,processed INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(task_id,call_id,payload_hash))')

    @contextmanager
    def processing(self,key,call):
        from .storage import Conflict
        pair=(key,call)
        with self._lock:
            if pair in self._processing:raise Conflict('本调用候选正在校验，请勿重复提交或恢复')
            self._processing.add(pair)
        try:yield
        finally:
            with self._lock:self._processing.discard(pair)

    def pending(self,t):
        with self.service.connect() as c:
            rows=c.execute('SELECT payload_hash,document FROM design_candidate_receipts WHERE task_id=? AND call_id=? AND processed=0 ORDER BY rowid',(t['id'],t.get('call_id',''))).fetchall()
        return [{'payload_hash':h,'payload':json.loads(doc)} for h,doc in rows]

    def state(self,t):
        with self._lock:running=(t['id'],t.get('call_id','')) in self._processing
        pending=self.pending(t)
        active=t['status'] in ('dispatching','awaiting_worker','delivery_unknown')
        interrupted=(t.get('last_report') or {}).get('code')=='CANDIDATE_PROCESSING_INTERRUPTED'
        status='validating' if running and (active or interrupted) else 'interrupted' if pending and (active or interrupted) else 'checked' if pending else 'none'
        return {'status':status,'pending_count':len(pending),'replayable':not running and status=='interrupted' and len(pending)==1,'model_call':False}

    def reconcile(self,t):
        """A saved receipt without an owning handler is an interrupted check.

        The local application runs one server process (the launcher binds its
        port exclusively). Never infer interruption from elapsed validation time.
        """
        with self._lock:
            if t['status'] not in ('dispatching','awaiting_worker','delivery_unknown') or self.state(t)['status']!='interrupted':return t
            from .design_lifecycle import close_call
            close_call(self.service,t,'candidate_processing_interrupted')
            t.update(status='paused',worker_result_closed=True,last_report={'code':'CANDIDATE_PROCESSING_INTERRUPTED','path':'/candidate-receipt','message':'候选原文已保存，但没有正在处理它的校验请求；请复检原候选，不必重新生成'},
                     failure_routing={'route':'platform_fault','automatic':False,'reason':'候选接收后的校验中断','next_action':'复检已收到的候选（不调用模型）'},message='候选已收到，平台校验中断；可复检原候选，不消耗模型额度')
            return self.service.save(t,'candidate_processing_interrupted')

    def capture(self,t,req):
        allowed=('kind','plan','blueprint','delta','patch','workflow','module','questions','extension_request','explanation')
        payload={k:req[k] for k in allowed if k in req};fingerprint=digest(payload)
        with self.service.connect() as c:
            c.execute('INSERT OR IGNORE INTO design_candidate_receipts(task_id,call_id,payload_hash,document,created_at) VALUES(?,?,?,?,?)',
                      (t['id'],t['call_id'],fingerprint,canonical(payload),now()))
            row=c.execute('SELECT processed FROM design_candidate_receipts WHERE task_id=? AND call_id=? AND payload_hash=?',(t['id'],t['call_id'],fingerprint)).fetchone()
        return {'payload_hash':fingerprint,'payload':payload,'processed':bool(row[0])}

    def complete(self,t,receipt):
        with self.service.connect() as c:c.execute('UPDATE design_candidate_receipts SET processed=1 WHERE task_id=? AND call_id=? AND payload_hash=?',(t['id'],t['call_id'],receipt['payload_hash']))

    def list(self,key):
        self.service.get(key)
        with self.service.connect() as c:
            rows=c.execute('SELECT payload_hash,created_at,document,processed FROM design_candidate_receipts WHERE task_id=? ORDER BY rowid DESC LIMIT 20',(key,)).fetchall()
        return {'items':[{'payload_hash':h,'received_at':at,'kind':json.loads(doc).get('kind'),'processed':bool(done),'candidate':json.loads(doc)} for h,at,doc,done in rows],
                'notice':'校验前收到的原始候选，已移除调用凭证；不代表候选检查通过。'}


def install(app,service):
    receipts=Receipts(service)
    @app.get('/api/workflow-designs/{key}/candidate-receipts')
    def listing(key:str):return receipts.list(key)
    @app.get('/api/workflow-designs/{key}/candidate-replay')
    def replay(key:str):
        from .submission_protocol import normalize_submission
        from .planning_protocol import compile_blueprint,normalize_binding_ports,repair_manifest,repair_context
        task=service.get(key);items=[]
        for record in receipts.list(key)['items']:
            raw=record['candidate'];item={'receipt_hash':record['payload_hash'],'applied':False}
            try:
                normalized,audit=normalize_submission(raw,task.get('planning_protocol')=='blueprint_v2')
                item['envelope_audit']=audit
                if normalized.get('kind')!='plan_blueprint':
                    item['status']='not_blueprint';items.append(item);continue
                blueprint,binding_audit=normalize_binding_ports(normalized['blueprint'],service.modules())
                item['binding_audit']=binding_audit
                plan=compile_blueprint(blueprint,service.modules())
                service.audit_plan(plan)
                report=service.plan_report(task,plan)
                item['recovery_available']=task.get('phase')=='plan' and not task.get('plan') and not task.get('superseded_by') and ((task.get('status')=='paused' and not task.get('questions') and not task.get('format_repair')) or (task.get('status')=='needs_clarification' and (task.get('last_report') or {}).get('code')=='PLAN_PROTOCOL_FORMAT' and bool(task.get('format_repair'))))
                item['revision']=task['revision']
                item['preview_hash']=digest({'revision':task['revision'],'receipt':record['payload_hash'],'blueprint':blueprint,'report':report})
                item.update(status='structure_passed' if report['valid'] else 'needs_repair',steps=len(plan.steps),grids=len(plan.grids),loops=len(plan.loops),preflight=report)
            except (ValueError,TypeError) as error:
                item.update(status='needs_repair',issues=getattr(error,'issues',None) or [{'path':getattr(getattr(error,'diagnostic',None),'path','/'),'message':str(error)}])
            items.append(item)
        return {'items':items,'notice':'只读回放：不改变任务、额度、候选、工具或配置，不调用 DSH；结构通过不代表数值或物理验证通过。'}
    @app.post('/api/workflow-designs/{key}/recover-candidate')
    async def recover(key:str,req:dict):
        import copy
        from .storage import Conflict
        from .submission_protocol import normalize_submission
        from .planning_protocol import normalize_binding_ports,compile_blueprint
        from .models import DesignWorkerResult
        task=service.get(key);service.check_revision(task,req.get('revision'))
        preview=next((x for x in replay(key)['items'] if x['receipt_hash']==req.get('receipt_hash')),None)
        if not preview or not preview.get('recovery_available') or preview.get('preview_hash')!=req.get('preview_hash') or req.get('confirm') is not True:
            raise Conflict('恢复预览已失效或任务不适用；请重新核对候选，不会追加或调用模型')
        record=next(x for x in receipts.list(key)['items'] if x['payload_hash']==req['receipt_hash'])
        normalized,audit=normalize_submission(record['candidate'],True)
        blueprint,binding_audit=normalize_binding_ports(normalized['blueprint'],service.modules())
        plan=compile_blueprint(blueprint,service.modules())
        task['latest_blueprint']=copy.deepcopy(blueprint);task['blueprint_origin']=True
        task['candidate_receipt_hash']=req['receipt_hash']
        task.setdefault('candidate_recovery_history',[]).append({'at':now(),'receipt_hash':req['receipt_hash'],'previous_report':copy.deepcopy(task.get('last_report')),'previous_questions':copy.deepcopy(task.get('questions')),'previous_format_repair':copy.deepcopy(task.get('format_repair')),'used':task.get('calls',0),'limit':task.get('call_limit',4)})
        for field in ('questions','format_repair','failure_routing','last_report'):task.pop(field,None)
        for item in audit:item.update(candidate_receipt_hash=req['receipt_hash'])
        task.setdefault('envelope_repair_audit',[]).extend(audit)
        task.setdefault('binding_repair_audit',[]).extend(binding_audit)
        result=await service.receive(task,DesignWorkerResult(kind='plan',plan=plan,call_id=task['call_id'],token=task['token'],explanation=normalized.get('explanation','')),revalidate=True)
        return result
    return receipts
