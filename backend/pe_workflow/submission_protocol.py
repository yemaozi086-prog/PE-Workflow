"""Lossless envelope adaptation and observable, evidence-based repair tickets."""
import copy
from .models import Diagnostic

PAYLOADS = ('plan','blueprint','delta','patch','workflow','module','questions','extension_request','implementation')
EXPECTED = {'plan':'plan','plan_blueprint':'blueprint','plan_delta':'delta','plan_patch':'patch','workflow':'workflow','module':'module','module_code':'implementation','questions':'questions','extension_request':'extension_request'}

class EnvelopeError(ValueError):
    def __init__(self, path, message):
        self.issues=[{'path':path,'message':message}]
        self.diagnostic=Diagnostic(code='RESULT_ENVELOPE',path=path,message=message)
        super().__init__(message)

def normalize_submission(req, blueprint_mode=False):
    value=copy.deepcopy(req);audit=[]
    # Only unwrap the exact extra wrapper. Multiple possible payloads are ambiguous.
    plan=value.get('plan')
    if blueprint_mode and value.get('kind') in ('plan','plan_blueprint') and isinstance(plan,dict) and set(plan)=={'blueprint'} and value.get('blueprint') is None:
        value['blueprint']=value.pop('plan')['blueprint'];value['kind']='plan_blueprint'
        audit.append({'path':'/plan/blueprint','target':'/blueprint','basis':'exact_single_blueprint_wrapper','notice':'仅移动包装层，内部内容逐字保留；未重新生成方案。'})
    if blueprint_mode and value.get('kind')=='plan_blueprint' and value.get('blueprint') is None and isinstance(value.get('plan'),dict) and {'title','summary','steps','port_defs'} <= set(value['plan']):
        value['blueprint']=value.pop('plan')
        audit.append({'path':'/plan','target':'/blueprint','basis':'explicit_blueprint_kind_complete_compact_payload','notice':'kind 已明确为蓝图；完整紧凑蓝图仅移动到对应顶层字段，内容未改变。'})
    if value.get('kind')=='module_code' and isinstance(plan,dict) and set(plan)=={'implementation'} and isinstance(plan['implementation'],dict) and not any(value.get(k) is not None for k in PAYLOADS if k!='plan'):
        value['implementation']=value.pop('plan')['implementation']
        audit.append({'path':'/plan/implementation','target':'/implementation','basis':'exact_single_implementation_wrapper','notice':'Only the envelope moved; code and contracts unchanged.'})
    kind=value.get('kind');field=EXPECTED.get(kind)
    present=[k for k in PAYLOADS if value.get(k) is not None]
    if kind=='blocked':
        if present:raise EnvelopeError('/'+present[0],'blocked 不得同时提交计算候选')
    elif field:
        if field not in present:raise EnvelopeError('/'+field,'kind='+str(kind)+' 必须在顶层提供 '+field+' 对象；不能省略或放进其他候选字段')
        if not isinstance(value[field],dict):raise EnvelopeError('/'+field,'kind='+str(kind)+' 的 '+field+' 必须是 JSON 对象，不能是序列化字符串；请直接提交对象，不要再次 JSON.stringify。原始提交已保留。')
        if present!=[field]:raise EnvelopeError('/','一次只允许一个与 kind 对应的候选字段；收到：'+', '.join(present))
    return value,audit

def execution_state(t):
    names={'dispatch_ready':'排队中','dispatching':'正在发送请求','awaiting_worker':'请求已送达，等待结果','delivery_unknown':'送达状态待核实','needs_clarification':'等待补充信息','paused':'已暂停','awaiting_confirmation':'方案待核对','missing_tools':'等待补齐工具','ready_to_save':'结构通过，等待保存','saved':'已保存','blocked':'已受阻'}
    report=t.get('last_report') or {};manifest=t.get('format_repair') or {};routing=t.get('failure_routing') or {}
    issues=report.get('issues') or manifest.get('issues') or (report.get('plan_preflight') or {}).get('issues') or ([{'path':report.get('path','/'),'message':report.get('message','')}] if report else [])
    return {'label':names.get(t.get('status'),t.get('status','未知')),'phase':t.get('phase'),
      'used':t.get('calls',0),'limit':t.get('call_limit',4),'remaining':max(0,t.get('call_limit',4)-t.get('calls',0)),
      'last_event_at':(t.get('events') or [{}])[-1].get('at'),
      'route':routing.get('route'),'next_action':routing.get('next_action') or t.get('message'),
      'stop_reason':routing.get('stop_reason'),'issues':issues,'allowed_paths':manifest.get('allowed_paths',[]),
      'base_hash':manifest.get('base_hash'),'receipt_hash':t.get('candidate_receipt_hash'),
      'preserved':'原始候选已留存；局部修正禁止改变公式、已明确来源、单位与保护条件。',
      'activity_notice':'这是平台状态；请求送达或工具读取不证明模型持续运行。'}



def continuation_action(t):
    route=(t.get('failure_routing') or {}).get('route')
    blocked={'platform_fault':'平台问题尚未解决，需要先检查诊断。','uncertain':'需要先确认公式或验证依据，不能靠增加次数跳过。','needs_information':'当前缺少可执行依据，暂不追加；请核对停止说明，确定需要补充资料还是处理平台支持范围。','needs_confirmation':'请先核对并恢复已有候选，再决定继续。'}
    if t.get('control_contract_revision') and (t.get('last_report') or {}).get('code')=='PLAN_PREFLIGHT':
        blocked['needs_confirmation']='请核对复检发现的剩余接口问题；方案已保留，无需重新回答文字输出问题。'
    allowed=t.get('status') in ('paused','blocked') and not t.get('archived') and not t.get('superseded_by') and not t.get('questions') and route not in blocked
    return {'allowed':bool(allowed),'additional_limit':3,'used':t.get('calls',0),'current_limit':t.get('call_limit',4),
            'new_limit':max(t.get('call_limit',4),t.get('calls',0))+3,'reason':blocked.get(route,'') if not allowed else '需要你明确批准，DSH 才会再次收到修正任务。',
            'scope':(t.get('format_repair') or {}).get('allowed_paths',[])}
