"""Task-bound implementation drafts. No registration or contract mutation tools."""
import asyncio
import ast
import copy
import json
import secrets
import time
import uuid
from pydantic import ValidationError
from .models import FormulaCode, FormulaPrecheck, FormulaSubmission, Module
from .storage import Conflict, canonical, digest, now
from .engine import fail
from .repair_routing import route_failure

MAX_PRECHECKS = 3
MAX_CHECK_SECONDS = 60
MAX_TOTAL_SECONDS = 120


def execution_failure(actual, path):
    diagnostic = (actual.get('diagnostics') or [{}])[0]
    message = diagnostic.get('message') or actual.get('error') or '执行没有生成有效结果'
    code = diagnostic.get('code')
    if code in ('WORKER_START','WORKER_FAILED','WORKER_PROTOCOL','TIMEOUT'):
        fail('FORMULA_WORKER_FAULT','计算服务未完成预检：'+message,path)
    # Only explicit interpreter/type violations justify automatic code repair;
    # numerical/model failures are not evidence that the formula is wrong.
    if code=='MODULE_SYNTAX' or any(text in message for text in ('算术运算仅接受数字或数值数组','文本仅支持字符串＋字符串','不支持文本或对象数组','未登记的函数','禁止的语法','禁止语法')):
        fail('CONTROLLED_RUNTIME_ERROR','受控实现执行错误：'+message,diagnostic.get('path') or path)
    fail('EXAMPLE_EXECUTION_UNCERTAIN','样例执行未完成，需核对具体原因：'+message,path)


def contract(service, task):
    if task['phase'] != 'formula' or not task.get('formula_request'):
        raise Conflict('仅公式生成阶段可预检实现')
    from .designs import configuration
    step = next(s for s in task['plan']['steps'] if s['id'] == task['formula_request']['step_id'])
    frozen = {'step': step, 'request': task['formula_request'], 'target': task.get('registration_target'),
              'provider': configuration()['provider_hash'], 'engine': configuration()['engine_hash']}
    return step, digest(frozen)


def context(service, task):
    step, fingerprint = contract(service, task)
    return {'version': 'formula_code_v1', 'step_id': step['id'], 'contract_hash': fingerprint,
            'code_fields':list(FormulaCode.model_fields), 'schema_section':'schema',
            'precheck_tool': 'design_precheck', 'result_kind': 'module_code', 'result_field': 'implementation',
            'max_prechecks_per_call': MAX_PRECHECKS, 'max_precheck_seconds_per_call': MAX_TOTAL_SECONDS,
            'instruction': 'Only implement this frozen step. Call design_precheck with contract_hash and code; fix only implementation after candidate_repair. Submit kind=module_code, implementation={contract_hash,code,precheck_id} using the latest passed precheck. Never submit inputs/outputs, units, required flags, checks, identity or expected answers. Precheck is not registration or engineering approval.'}


def assemble(service, task, code):
    code = FormulaCode.model_validate(code) if isinstance(code, dict) else code
    step, _ = contract(service, task)
    target = task.get('registration_target') or {}
    identity = target.get('id') or 'generated_' + task['id'][:24]
    return Module(id=identity, version=target.get('version', 1), name=step['title'],
                  description=step['purpose'], contract_version='3.0',
                  project_id=task['request'].get('project_id','local'),
                  inputs=copy.deepcopy(step['inputs']), outputs=copy.deepcopy(step['outputs']),
                  input_checks=copy.deepcopy(step.get('input_checks', [])),
                  provenance=task['formula_request']['evidence'], **code.model_dump())


class Authoring:
    def __init__(self, service):
        self.service = service
        with service.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS formula_prechecks(task_id TEXT,call_id TEXT,attempt INTEGER,document TEXT,PRIMARY KEY(task_id,call_id,attempt))')

    def rows(self, task):
        with self.service.connect() as con:
            return [json.loads(r[0]) for r in con.execute('SELECT document FROM formula_prechecks WHERE task_id=? AND call_id=? ORDER BY attempt', (task['id'],task.get('call_id','')))]

    def history(self, key):
        self.service.get(key)
        with self.service.connect() as con:
            return [json.loads(r[0]) for r in con.execute('SELECT document FROM formula_prechecks WHERE task_id=? ORDER BY rowid DESC LIMIT 30',(key,))]

    def summary(self, task):
        rows = self.rows(task)
        return {'attempts':len(rows),'limit':MAX_PRECHECKS,'remaining':max(0,MAX_PRECHECKS-len(rows)),
                'seconds_used':round(sum(r.get('seconds',0) for r in rows),2),
                'latest':rows[-1]['report'] if rows and rows[-1].get('report') else None,
                'status':rows[-1]['status'] if rows else 'not_started',
                'notice':'预检不注册、不发布、不自动改接口；模型调用仍按原额度计数。'}

    def guard(self, task):
        if task.get('archived') or task.get('superseded_by') or task.get('worker_result_closed') or task['status'] not in ('dispatching','awaiting_worker'):
            raise Conflict('当前调用已关闭或暂停，不接受预检；请使用原恢复入口')
        if task.get('formula_authoring') != 'formula_code_v1':
            raise Conflict('此旧调用使用原协议；下一次公式调用可启用紧凑协议')

    def store(self, task, row):
        with self.service.connect() as con:
            con.execute('UPDATE formula_prechecks SET document=? WHERE task_id=? AND call_id=? AND attempt=?',
                        (canonical(row),task['id'],task['call_id'],row['attempt']))

    def fence(self, original, fingerprint):
        live = self.service.get(original['id'])
        self.guard(live)
        if live['revision'] != original['revision'] or live.get('call_id') != original['call_id'] or contract(self.service,live)[1] != fingerprint:
            raise Conflict('预检期间任务或契约已变化；结果未应用，请核对最新修订')
        return live

    async def precheck(self, task, raw):
        self.guard(task)
        step, fingerprint = contract(self.service,task)
        if raw.get('contract_hash') != fingerprint:
            raise Conflict('冻结契约已变化，请重新读取公式上下文；不会消耗模型调用')
        rows = self.rows(task)
        if any(r['status']=='running' for r in rows):
            raise Conflict('本调用已有预检在运行或中断；先核对实际进度，不重复执行')
        if len(rows) >= MAX_PRECHECKS or sum(r.get('seconds',0) for r in rows) >= MAX_TOTAL_SECONDS:
            raise Conflict('本调用预检预算已用完，请停止；不能绕过预算继续提交')
        row = {'id':uuid.uuid4().hex,'attempt':len(rows)+1,'status':'running','at':now(),
               'contract_hash':fingerprint,'code':copy.deepcopy(raw.get('code')),'seconds':0}
        with self.service.connect() as con:
            # A competing worker cannot reserve the same bounded attempt.
            con.execute('INSERT INTO formula_prechecks VALUES(?,?,?,?)', (task['id'],task['call_id'],row['attempt'],canonical(row)))
        self.service.progress.tool(task,'design_precheck')
        working = copy.deepcopy(task)
        start = time.monotonic()
        error = None
        module = None
        try:
            request = FormulaPrecheck.model_validate(raw)
            module = assemble(self.service,working,request.code)
            remaining = MAX_TOTAL_SECONDS-sum(r.get('seconds',0) for r in rows)
            module, validation = await asyncio.wait_for(self.service.check_formula_candidate(working,module),min(MAX_CHECK_SECONDS,remaining))
            report = {'status':'passed','verification':validation,'code_hash':digest(request.code.model_dump()),
                      'message':'预检通过；仍须正式提交候选并由用户确认注册。'}
        except Exception as exc:
            error = exc
            diagnostic = getattr(exc,'diagnostic',None)
            code = diagnostic.code if diagnostic else 'RESULT_ENVELOPE' if isinstance(exc,ValidationError) else 'PRECHECK_TIMEOUT' if isinstance(exc,TimeoutError) else 'DESIGN_VALIDATION'
            path = diagnostic.path if diagnostic else '/code'
            message = diagnostic.message if diagnostic else '预检超时；未接受候选' if isinstance(exc,TimeoutError) else str(exc)
            routing = route_failure(exc,{'code':code,'path':path,'message':message})
            report = {'status':'failed','code':code,'path':path,'message':message,'routing':routing,
                      'dependency_audit':working.get('dependency_audit'),
                      'allowed_edits':['implementation','source','expressions','entrypoint','adapter','transforms']}
            if isinstance(exc,ValidationError):report['fields']=[{'path':'/'+('/'.join(map(str,e['loc']))),'message':e['msg']} for e in exc.errors()]
            # Provide a real source line for a named forbidden AST operator.
            if module and module.source:
                try:
                    bad=next((n for n in ast.walk(ast.parse(module.source)) if hasattr(n,'lineno') and (type(n).__name__ in message or isinstance(n,ast.BinOp) and type(n.op).__name__ in message)),None)
                    if bad:report['line']=bad.lineno
                except SyntaxError as syntax:
                    report['line']=syntax.lineno
        row['seconds']=round(time.monotonic()-start,3)
        row['status']=report['status'];row['report']=report
        self.store(task,row)
        try:
            live = self.fence(task,fingerprint)
        except Conflict:
            row['status']='stale'
            row['report']={'status':'stale','code':'PRECHECK_STALE','message':'检查期间任务已暂停、切换或契约已变；结果未应用，请核对最新修订。'}
            self.store(task,row)
            return {**row['report'],'stop_required':True,'submitted':False,'registered':False}
        repeated = report['status']=='failed' and bool(rows) and rows[-1].get('report',{}).get('code')==report.get('code') and rows[-1].get('report',{}).get('path')==report.get('path')
        exhausted = len(rows)+1>=MAX_PRECHECKS or sum(r.get('seconds',0) for r in rows)+row['seconds']>=MAX_TOTAL_SECONDS
        stop = report['status']=='failed' and (repeated or exhausted or not report['routing']['automatic'])
        if stop:
            # Same pause/recovery path as formal validation, with no new call.
            from .design_lifecycle import close_call
            live['previous_candidate']={'kind':'module','module':module.model_dump()} if module else {'kind':'module_code','implementation':{'code':raw.get('code')}}
            for key in ('dependency_audit','verification_feedback'):
                if key in working:live[key]=working[key]
            close_call(self.service,live,'formula_precheck_stopped')
            failed=self.service.retry(live,error,allow_retry=False)
            failed['worker_result_closed']=True
            failed['failure_routing']['stop_reason']='repeated_error' if repeated else 'precheck_budget_exhausted' if exhausted else 'requires_review'
            self.service.save(failed,'formula_precheck_stopped')
        return {**report,'precheck_id':row['id'],'contract_hash':fingerprint,'attempt':row['attempt'],
                'remaining_prechecks':MAX_PRECHECKS-row['attempt'],'stop_required':stop,
                'submitted':False,'registered':False,
                'instruction':'Stop this call. Use platform review/recovery; no registration occurred.' if stop else 'Fix implementation only and precheck again.' if error else 'Submit module_code using this exact code, contract_hash and precheck_id. Do not change contracts.'}

    def recover(self, task):
        from .submission_protocol import normalize_submission
        raw, audit = normalize_submission(task.get('previous_candidate') or {})
        if raw.get('kind') != 'module_code':
            fail('CANDIDATE_MISSING','No retained compact candidate','/candidate')
        req = FormulaSubmission.model_validate(raw['implementation'])
        rows = self.rows(task)
        if not rows or rows[-1]['status'] != 'passed' or rows[-1]['id'] != req.precheck_id or rows[-1]['contract_hash'] != req.contract_hash or rows[-1]['report']['code_hash'] != digest(req.code.model_dump()):
            fail('PRECHECK_REQUIRED','Recovery must match the original passed precheck exactly','/implementation')
        task['compact_recovery_audit'] = {'normalization':audit,'precheck_id':req.precheck_id,'basis':'original_code_revalidated_against_current_confirmed_contract'}
        return assemble(self.service,task,req.code)

    def submission(self, task, raw):
        self.guard(task)
        req = FormulaSubmission.model_validate(raw)
        if req.contract_hash != contract(self.service,task)[1]:
            fail('CONTRACT_CHANGE_REQUIRES_CONFIRM','契约已变化，原预检不能用于此修订','/implementation/contract_hash')
        rows = self.rows(task)
        if not rows or rows[-1]['status']!='passed' or rows[-1]['id']!=req.precheck_id or rows[-1]['report']['code_hash']!=digest(req.code.model_dump()) or rows[-1]['contract_hash']!=req.contract_hash:
            fail('PRECHECK_REQUIRED','正式提交必须匹配本调用最新通过的代码预检；不接受未检查或已改动的代码','/implementation/precheck_id')
        return assemble(self.service,task,req.code)


def install(app, service):
    authoring = Authoring(service)
    @app.get('/api/workflow-designs/{key}/formula-prechecks')
    def history(key:str):
        return {'items':authoring.history(key),'notice':'只读预检记录；不含独立预期答案，不调用模型、不注册工具。'}
    @app.post('/api/workflow-designs/{key}/formula-precheck')
    async def precheck(key:str, req:dict):
        task=service.get(key)
        if req.get('call_id')!=task.get('call_id') or not isinstance(req.get('token'),str) or not secrets.compare_digest(req['token'],task.get('token','')):
            raise Conflict('调用凭证无效')
        with service.receipts.processing(key,task['call_id']):
            return await authoring.precheck(task,req)
    return authoring
