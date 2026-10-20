"""Reviewable opt-in protocol migration. No model calls or budget resets."""
import copy
import json
import uuid
from .storage import digest,canonical,now,Conflict
from .models import BindingAssemblyConfirm,DesignConfirm
from .repair_routing import budget_snapshot


class Migration:
    def __init__(self,service):self.service=service

    def preview(self,key):
        t=self.service.get(key)
        if t.get('planning_protocol')=='blueprint_v2':raise Conflict('任务已经使用新规划协议')
        data={'source_id':key,'revision':t['revision'],'status':t['status'],
              'used':t.get('calls',0),'limit':t.get('call_limit',4),'remaining':max(0,t.get('call_limit',4)-t.get('calls',0)),
              'changes':['规划格式：完整Port重复声明 → 简化接口定义与引用','增加显式有限网格、子图成员与共享输入声明','格式错误仅允许哈希绑定的局部修改'],
              'preserved':['原需求、原方案与失败记录','已用额度、重复错误停止限制、独立验收依据'],
              'requires_review':'原始缺失的数组轴或语义不会猜测补齐。派生后不自动调用模型，也不自动注册。',
              'active':t['status'] in ('dispatch_ready','dispatching','awaiting_worker')}
        data['preview_hash']=digest(data)
        return data

    def confirm(self,key,req):
        receipt=key+':'+digest(req.model_dump())
        with self.service.connect() as c:r=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.planning_migration_receipt')=?",(receipt,)).fetchone()
        if r:return self.service.public(json.loads(r[0]))
        old=self.service.get(key);self.service.check_revision(old,req.revision)
        view=self.preview(key)
        if view['preview_hash']!=req.preview_hash:raise Conflict('迁移预览已变化，请重新核对')
        if view['active']:raise Conflict('请等待原调用结束或明确停止后再迁移；不自动取消')
        from .designs import configuration
        child={'id':uuid.uuid4().hex,'parent_id':key,'request':copy.deepcopy(old['request']),
               'planning_protocol':'blueprint_v2','planning_migration_receipt':receipt,'migration_ready':True,
               'phase':'plan','status':'paused','created_at':now(),'calls':old.get('calls',0),'call_limit':old.get('call_limit',4),
               'repeat_count':old.get('repeat_count',0),'failure_signature':old.get('failure_signature'),
               'stage_budgets':budget_snapshot(old),'total_calls':old.get('total_calls',old.get('calls',0)),
               'configuration':configuration(),'migration_diff':view,
               'message':'协议迁移已派生；未调用模型。核对剩余额度后明确继续，额度耗尽需另行批准。'}
        if old.get('plan'):child['previous_plan']=copy.deepcopy(old['plan'])
        # Save original intact. Late old results cannot silently replace the new child.
        child.update(revision=1,updated_at=now(),events=[{'event':'user_confirmed_planning_migration','actor':'user','at':now(),'revision':1}])
        with self.service.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute("SELECT document FROM workflow_designs WHERE json_extract(document,'$.planning_migration_receipt')=?",(receipt,)).fetchone()
            if r:return self.service.public(json.loads(r[0]))
            live=json.loads(c.execute('SELECT document FROM workflow_designs WHERE id=?',(key,)).fetchone()[0])
            self.service.check_revision(live,req.revision)
            c.execute('INSERT INTO workflow_designs VALUES(?,?,?)',(child['id'],1,canonical(child)))
        return self.service.public(child)


def install(app,service):
    migration=Migration(service)
    def configuration_preview(key):
        from .designs import configuration
        t=service.get(key);current=configuration();old=t.get('configuration',{})
        view={'source_id':key,'revision':t['revision'],'used':t.get('calls',0),'limit':t.get('call_limit',4),
              'remaining':max(0,t.get('call_limit',4)-t.get('calls',0)),
              'changes':[k for k in ('prompt_hash','tool_hash','provider_hash','engine_hash') if old.get(k)!=current[k]],
              'current_hashes':{k:current[k] for k in ('prompt_hash','tool_hash','provider_hash','engine_hash')},
              'notice':'仅更新生成配置；原需求、公式、验收依据与停止限制保留，不追加额度。'}
        view['preview_hash']=digest(view)
        return view
    @app.get('/api/workflow-designs/{key}/configuration-review')
    def review_preview(key:str):return configuration_preview(key)
    @app.post('/api/workflow-designs/{key}/configuration-review')
    def review_configuration(key:str,req:BindingAssemblyConfirm):
        from .designs import configuration
        t=service.get(key);receipt=digest(req.model_dump())
        if t.get('configuration_review_receipt')==receipt:return service.public(t)
        service.check_revision(t,req.revision)
        if not service.public(t)['configuration_review_required']:raise Conflict('当前不是配置变更保护暂停')
        view=configuration_preview(key)
        if view['preview_hash']!=req.preview_hash:raise Conflict('配置或任务已变化，请重新核对')
        if not view['remaining'] or t.get('repeat_count',0)>=2 or t.get('questions'):raise Conflict('额度、重复错误或待补充信息阻止继续；配置确认不会绕过停止限制')
        t.setdefault('configuration_history',[]).append({'at':now(),'configuration':copy.deepcopy(t.get('configuration'))})
        t.update(configuration=configuration(),configuration_review_receipt=receipt,status='dispatch_ready',message='用户已核对新配置；仅使用剩余额度继续，不追加调用')
        return service.public(service.save(t,'user_confirmed_configuration_remaining_budget'))
    @app.get('/api/workflow-designs/{key}/planning-migration')
    def preview(key:str):return migration.preview(key)
    @app.post('/api/workflow-designs/{key}/planning-migration')
    def confirm(key:str,req:BindingAssemblyConfirm):return migration.confirm(key,req)
    @app.post('/api/workflow-designs/{key}/resume-migrated-planning')
    def resume(key:str,req:DesignConfirm):
        t=service.get(key);service.check_revision(t,req.revision)
        if not t.get('migration_ready') or t['status']!='paused':raise Conflict('当前没有待继续的协议迁移')
        if t.get('calls',0)>=t.get('call_limit',4) or t.get('repeat_count',0)>=2:raise Conflict('额度耗尽或触发重复错误停止，请另行明确批准追加，不能通过迁移重置')
        t.update(migration_ready=False,status='dispatch_ready',message='按迁移后协议使用剩余额度继续')
        return service.public(service.save(t,'user_resumed_migrated_planning'))
    return migration
