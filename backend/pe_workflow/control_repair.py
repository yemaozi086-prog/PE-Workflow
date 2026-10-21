"""User-confirmed control reference repair, immutable parent and unchanged budgets."""
import copy,json,uuid
from .models import DesignPlan,BindingAssemblyConfirm
from .storage import Conflict,digest,canonical,now
from .control_contracts import reviewed_corrections,describe
from .finite_control import control_spec
from .repair_routing import budget_snapshot
from .engine import WorkflowError

class ControlRepair:
    def __init__(self,service):self.service=service
    def preview(self,key):
        task=self.service.get(key);raw=task.get('plan') or task.get('patch_base_plan')
        available=bool(raw) and not task.get('archived') and not task.get('superseded_by') and task.get('phase')=='plan' and task.get('status') in ('paused','blocked','needs_clarification','awaiting_confirmation','missing_tools')
        data={'source_id':key,'revision':task['revision'],'available':False,'edits':[],
              'used':task.get('calls',0),'limit':task.get('call_limit',4),'remaining':max(0,task.get('call_limit',4)-task.get('calls',0)),
              'notice':'预览网格端口或有明确证据的当前行来源修订；确认后派生修订，不调用模型、不追加额度、不注册工具、不运行。'}
        if available:
            try:
                corrected,edits,contracts=reviewed_corrections(raw)
                contract_hash=digest(control_spec())
                recheck=bool((raw.get('grids') or raw.get('loops')) and task.get('status') in ('paused','blocked','needs_clarification') and task.get('control_contract_revision')!=contract_hash)
                data.update(mode='scope_repair' if any(e.get('kind')=='scope' for e in edits) else 'port_repair' if edits else 'runtime_recheck',contract_revision=contract_hash,available=bool(edits) or recheck,edits=edits,contracts=contracts,after_contracts=describe(corrected),base_hash=digest(raw),corrected_hash=digest(corrected),
                            after_check=self.service.plan_report(task,DesignPlan.model_validate(corrected)),preserved=['全部节点；仅变更本预览明确列出的来源与过时依赖','公式、输入输出字段与单位','保护条件、网格轴与遍历顺序','旧记录、已用额度和重复错误限制'])
            except (ValueError,KeyError,TypeError,WorkflowError) as error:
                data.update(available=False,issues=[{'code':'CONTROL_REPAIR_UNCERTAIN','path':'/plan/controls','message':str(error)}])
        data['preview_hash']=digest(data);return data
    def confirm(self,key,req):
        if not req.allow_unverified:raise Conflict('请明确确认来源修订预览后继续；不会自动改变连接')
        receipt=key+':'+digest(req.model_dump())
        with self.service.connect() as c:
            r=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.control_repair_receipt')=?",(receipt,)).fetchone()
        if r:return self.service.public(json.loads(r[0]))
        old=self.service.get(key);self.service.check_revision(old,req.revision);view=self.preview(key)
        if not view['available'] or view['preview_hash']!=req.preview_hash:raise Conflict('控制修正预览已改变或当前不适用，请重新核对')
        raw=old.get('plan') or old.get('patch_base_plan');corrected,edits,_=reviewed_corrections(raw)
        if digest(corrected)!=view['corrected_hash']:raise Conflict('控制契约已改变，请重新预览')
        from .designs import configuration
        report=view['after_check'];child={'id':uuid.uuid4().hex,'parent_id':key,'request':copy.deepcopy(old['request']),
          'control_contract_revision':view['contract_revision'],'control_repair_receipt':receipt,'control_repair_audit':{'preview':view,'edits':edits,'original_hash':digest(raw)},
          'plan':corrected,'phase':'plan','planning_protocol':old.get('planning_protocol'),'status':'awaiting_confirmation' if report['valid'] else 'paused',
          'calls':old.get('calls',0),'call_limit':old.get('call_limit',4),'total_calls':old.get('total_calls',old.get('calls',0)),
          'repeat_count':old.get('repeat_count',0),'failure_signature':old.get('failure_signature'),'stage_budgets':budget_snapshot(old),
          'configuration':configuration(),'created_at':now(),'revision':1,'updated_at':now(),
          'message':('控制连接已按确认差异修正。' if edits else '已按最新控制契约重新检查，方案内容未改动。')+('结构检查通过，请核对方案；尚未验证数值或物理正确性。' if report['valid'] else '仍有其他检查未通过，请查看剩余问题。')+'未调用模型。',
          'events':[{'at':now(),'event':'user_confirmed_control_port_repair','actor':'user','revision':1,'status':'awaiting_confirmation' if report['valid'] else 'paused'}]}
        if not report['valid']:
            child['last_report']={'code':'PLAN_PREFLIGHT','path':'/plan','message':'网格端口修正后仍需处理其他问题','plan_preflight':report,'issues':report['issues']}
            child['failure_routing']={'route':'needs_confirmation','automatic':False,'reason':'已按新契约重新检查，剩余问题需要单独核对' if not edits else '局部端口已修正，剩余问题需要单独核对','next_action':'查看剩余诊断，不追加或重写整份方案','evidence':{'code':'CONTROL_REPAIR_RECHECK'}}
        with self.service.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.control_repair_receipt')=?",(receipt,)).fetchone()
            if r:return self.service.public(json.loads(r[0]))
            live=json.loads(c.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()[0]);self.service.check_revision(live,req.revision)
            c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child['id'],1,canonical(child)))
        return self.service.public(child)

def install(app,service):
    repair=ControlRepair(service)
    @app.get('/api/workflow-designs/{key}/control-repair')
    def preview(key:str):return repair.preview(key)
    @app.post('/api/workflow-designs/{key}/control-repair')
    def confirm(key:str,req:BindingAssemblyConfirm):return repair.confirm(key,req)
    return repair
