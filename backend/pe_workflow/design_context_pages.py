"""Small exact module indexes and bounded, explicitly paged JSON sections."""
import json
from .storage import digest

PAGE_SIZE=6000


def compact(value):
    if isinstance(value,dict):return {k:compact(v) for k,v in value.items() if v not in (None,'',[],{})}
    if isinstance(value,list):return [compact(v) for v in value]
    return value


def page(value,section,offset=0):
    text=json.dumps(value if section=='schema' else compact(value),ensure_ascii=False,separators=(',',':'))
    return {'section':section,'encoding':'json_text','offset':offset,'total_chars':len(text),'content_hash':digest(value),
            'next_offset':offset+PAGE_SIZE if offset+PAGE_SIZE<len(text) else None,'text':text[offset:offset+PAGE_SIZE],
            'notice':'分段不是完整 JSON；按 next_offset 读取剩余片段。禁止根据截断内容猜测字段。'}


def registry(service,t):
    if t.get('frozen_modules') is not None:return t['frozen_modules']
    if t.get('catalog_context_id'):return service.store().context(t['catalog_context_id'])['modules']
    return t.get('catalog_snapshot') or [m.model_dump() for m in service.modules().values()]


def index_data(service,t):
    modules=registry(service,t)
    data={'protocol':'workflow_design_paged_v1','phase':t['phase'],
          'submission_protocol':{'version':'plan_blueprint_v2' if t.get('planning_protocol')=='blueprint_v2' else 'workflow_design_v1','instruction':'kind=plan_blueprint 时，完整蓝图放在顶层 blueprint，禁止 plan.blueprint；plan_delta 仅提交 delta={base_hash,edits}。只提交一个与 kind 对应的候选字段。'},
          'request_summary':t['request']['instruction'][:400],
          'required_result_kind':'plan_patch' if t.get('repair_mode')=='plan_patch' and t['phase']=='plan' else ('plan_delta' if t.get('format_repair') else 'plan_blueprint') if t.get('planning_protocol')=='blueprint_v2' and t['phase']=='plan' else {'plan':'plan','bind':'workflow','formula':'module'}[t['phase']],
          'steps':[{'id':s['id'],'title':s['title'],'module_ref':s.get('module_ref'),'dependencies':s.get('dependencies',[])} for s in t.get('plan',{}).get('steps',[])],
          'module_refs':[f'{m["id"]}@{m["version"]}' for m in modules],
          'modules':[{'ref':f'{m["id"]}@{m["version"]}','name':m['name'],'description':m.get('description','')[:160],'capabilities':m.get('capabilities',[])} for m in modules] if t['phase']=='plan' else [],
          'capabilities':{'planning':'explicit grid and one-level finite batch only; read controls before planning','batch_member_outputs':['number','array','string (scalar only)'],'nested_planning':False,'convergence_loop_planning':False,'syntax':'Read rules controlled_syntax.manifest, numeric_catalog and array_logic before first formula generation: operators, functions, keywords, transforms and limits come from the interpreter.'},
          'sections':['request','plan','schema','rules','diagnostics','formula','extensions','step','controls','candidate'],
          'control_contract_instruction':'For grids/batch read section=controls. Grid output is fixed rows, numeric-column flat_dict with batch_rows axis. Never invent PlanGrid.outputs or ask users for platform-owned port names.',
          'instructions':['先按 phase 读取对应 section；所有 section 均按 next_offset 分页。',
                          'module_refs 是准确冻结引用，只能按这些引用读取；MODULE_NOT_FOUND 时停止并报告，禁止猜名或反复重试。',
                          'plan/step 中的来源是待确认或已确认声明，不能按类型单位猜测新连接。',
                          '不要读取无关模块；优先读取步骤已经选定的准确版本。'],
          'last_report':{k:v for k,v in (t.get('last_report') or {}).items() if k in ('code','path','message')}}
    if t['phase']=='formula' and t.get('formula_authoring')=='formula_code_v1':
        from .formula_authoring import context as authoring_context
        data['required_result_kind']='module_code'
        data['formula_authoring']=authoring_context(service,t)
    return data


def index(service,t):
    data=index_data(service,t)
    if len(json.dumps(data,ensure_ascii=False))>PAGE_SIZE:
        return {'protocol':'workflow_design_paged_v1','index_page':page(data,'index'),'instructions':['按 section=index 与 next_offset 读取完整索引，不猜模块名称。']}
    return data


def read(service,t,section,offset=0,step_id=''):
    if section=='index':return index(service,t) if not offset else page(index_data(service,t),'index',offset)
    if section=='request':data=t['request']
    elif section=='candidate':data=t.get('previous_candidate') or {}
    elif section=='plan':data=t.get('plan')
    elif section=='controls':
        from .finite_control import control_spec
        from .control_contracts import describe
        plan=t.get('plan') or t.get('patch_base_plan')
        data=describe(plan) if plan else {'specification':control_spec(),'nodes':[]}
    elif section=='step':
        data=next((s for s in t.get('plan',{}).get('steps',[]) if s['id']==step_id),None)
        if data is None:return {'status':'failed','code':'STEP_NOT_FOUND','available_step_ids':[s['id'] for s in t.get('plan',{}).get('steps',[])],'stop_required':True}
    else:
        context=service.context(t)
        data={'schema':context['schemas'],'rules':{k:context.get(k) for k in ('stage_instruction','rules','controlled_syntax','implementation_protocols','adapter_examples','planning_protocol')},
              'diagnostics':{k:context.get(k) for k in ('last_report','dependency_audit','previous_candidate','previous_patch','plan_repair','repair_mode','patch_base','repair_constraints','answers','previous_plan','replan_diagnostics','repair_budget','format_repair','repair_ticket')},
              'formula':{k:context.get(k) for k in ('formula_request','registration_target','concrete_protocol_repair','formula_authoring')},
              'extensions':{k:context.get(k) for k in ('extension_catalog','extension_proposal_schema','environment')}}[section]
    if section=='diagnostics' and t.get('format_repair'):
        for key in ('previous_candidate','previous_plan','patch_base'):data.pop(key,None)
        data['candidate_reference']={'receipt_hash':t.get('candidate_receipt_hash'),'section':'candidate','instruction':'Local repair current_values, allowed_paths and path_rules are complete for authorized edits. Full original is available only if needed; do not reread or rewrite it to fix local formatting.'}
    return page(data,section,offset)

