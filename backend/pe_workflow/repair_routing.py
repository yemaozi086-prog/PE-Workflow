"""Evidence-based routing; this module never edits candidates or acceptance data."""
from .engine import WorkflowError
import copy


def budget_snapshot(task):
    result=copy.deepcopy(task.get('stage_budgets',{}))
    if 'call_limit' in task:
        phase=task.get('phase','plan')
        scope=phase+(':'+task.get('formula_request',{}).get('step_id','') if phase=='formula' else '')
        result.setdefault(scope,{'used':task.get('calls',0),'limit':task['call_limit']})
    return result


def changed_paths(before, after, path='/plan'):
    """Human-readable revision impact; no values or acceptance answers in logs."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [p for key in sorted(set(before) | set(after))
                for p in changed_paths(before.get(key), after.get(key), path+'/'+key)]
    if isinstance(before, list) and isinstance(after, list) and len(before)==len(after):
        return [p for index,(a,b) in enumerate(zip(before,after))
                for p in changed_paths(a,b,path+'/'+str(index))]
    return [path]


def route_failure(error, report):
    code = report['code']
    route, reason, action = 'uncertain', '现有证据不足以确定错误归属', '保存任务，集中补充诊断所缺证据'
    if not isinstance(error, (WorkflowError, ValueError)):
        route, reason, action = 'platform_fault', '平台执行出现非候选校验异常', '检查平台诊断；不让任务智能体修改校验器'
    elif code == 'COMPLETION_PREPARATION_FAILED':
        route, reason, action = 'platform_fault', '平台内部请求准备失败，尚未生成候选', '修复平台请求传递后恢复原步骤；不消耗候选修正额度，不要求用户改写声明'
    elif code == 'RESULT_ENVELOPE':
        route, reason, action = 'candidate_repair', '提交外层协议不完整，尚未检查计算内容', '只修正包装与指定顶层字段；保留公式、接口和保护条件，不解析或猜补损坏源码'
    elif code == 'TRANSFORM_EXAMPLES_REQUIRED':
        route, reason, action = 'needs_information', '显式变换缺少独立验证材料', '提供至少两组独立样例及变换前提；不得由生成候选的智能体编造验收答案'
    elif code == 'UNKNOWN_UNIT':
        route, reason, action = 'needs_information', '单位尚未解析，不能通过修改计算代码解决', '核对标准与项目自定义声明；缺少定义时提交自定义声明请求'
    elif code == 'INPUT_USAGE_REVIEW_REQUIRED':
        route, reason, action = 'needs_confirmation', '工具声明与实际计算/保护依赖需要核对', '查看本批输入依赖修订差异；确认后复检原候选，不追加调用或伪造字段读取'
    elif code in ('EXAMPLE_DATA_INVALID', 'CONTRACT_CHANGE_REQUIRES_CONFIRM'):
        route, reason, action = 'needs_confirmation', '涉及验证依据或已确认接口', '派生修订并核对差异，确认后继续'
    elif code == 'EXAMPLE_RESULT_MISMATCH':
        route, reason, action = 'uncertain', '数值不一致不能单独证明候选或样例哪方有误', '核对实际输出、公式与独立依据；不得自动改写预期答案'
    elif code in ('FIELD_SEMANTIC_REQUIRED', 'UNIT_CONFLICT', 'UNIT_DECLARATION', 'SEMANTIC_CONFLICT', 'UNRESOLVED_DEFINITION'):
        route, reason, action = 'needs_information', '物理声明缺失或存在冲突，不能自动猜测含义', '集中核对含义、单位定义及来源；改变已确认声明时先派生修订'
    elif code == 'DEPENDENCY_UNCERTAIN':
        route, reason, action = 'needs_information', '受控分析无法可靠确定字段读取', '补充允许读取范围，或明确允许改成显式字段访问'
    elif code in ('DEPENDENCY_CONFLICT', 'DEPENDENCY_MINIMUM_FAILED', 'DEPENDENCY_EXTRA_FAILED',
                  'PATCH_REQUIRED', 'PATCH_UNEXPECTED', 'PLAN_PROTOCOL_FORMAT', 'PLAN_DELTA_SCOPE', 'PLAN_PREFLIGHT', 'INPUT_CHECK_INVALID',
                  'MISSING_DICT_FIELD', 'DICTIONARY_PORT_REQUIRED', 'UNKNOWN_FIELD_MAP', 'FIELD_MAP_VERSION',
                  'UNDECLARED_TRANSFORM', 'EXPRESSION_NAME', 'MODULE_SYNTAX', 'UNKNOWN_REFERENCE', 'UNKNOWN_FIELD', 'MISSING_BINDING', 'DUPLICATE_NODE',
                  'CYCLE', 'OUTPUT_CONTRACT', 'EXPRESSION_OUTPUTS', 'RESERVED_INPUT', 'BRANCH_CONTRACT', 'BUILTIN_REFERENCE'):
        route, reason, action = 'candidate_repair', '声明、字段读取或接线的检查提供了候选修正证据', '仅修正候选，保留冻结公式、接口边界与保护条件'
    elif isinstance(error, ValueError):
        # Deliberate validation messages and schema errors, never arbitrary exceptions.
        from pydantic import ValidationError
        if isinstance(error, ValidationError) or str(error).startswith(('步骤', '模块', '依赖', '当前阶段', '实际接线', '节点必须', '工作流必须', '实际来源', '不能删除或改写', '首版规划不允许', '仅允许受控', '必须按指定基准', '新工具必须', '不可覆盖', '问题不能为空')):
            route, reason, action = 'candidate_repair', '结构校验明确定位到候选声明', '按具体诊断修正候选，不改变验收依据'
    return {'route': route, 'reason': reason, 'next_action': action,
            'evidence': {'code': code, 'path': report.get('path', '/')},
            'automatic': route == 'candidate_repair'}
