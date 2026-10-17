"""Scoped deterministic naming rules; LLM proposes, the platform verifies."""
import re
from .models import NamingRule


def resolve_rule(rule: NamingRule,source: str):
    if rule.source_pattern.count('{index}')>1 or rule.target_pattern.count('{index}')>1:raise ValueError('编号模板最多使用一个 {index}')
    if '{' in rule.source_pattern.replace('{index}','') or '}' in rule.source_pattern.replace('{index}',''):raise ValueError('仅支持明确的 {index} 编号模板，不执行任意正则')
    if any(c in rule.target_pattern.replace('{index}','') for c in '{}'):raise ValueError('目标仅支持 {index} 模板')
    if ('{index}' in rule.target_pattern) and '{index}' not in rule.source_pattern:raise ValueError('目标编号必须来自明确的源编号')
    pattern=re.escape(rule.source_pattern).replace(re.escape('{index}'),r'(?P<index>[1-9][0-9]{0,5})')
    match=re.fullmatch(pattern,source)
    if not match:return None
    index=match.groupdict().get('index','')
    return {'target':rule.target_pattern.replace('{index}',index),'declaration':rule.declaration.model_dump()|{'instance_id':rule.declaration.instance_id.replace('{index}',index)}}


def check_rule(rule):
    from .engine import check_port
    if rule.declaration.type in ('flat_dict','collection') or not rule.declaration.quantity:raise ValueError('命名规则必须附单字段物理/引用语义')
    check_port(rule.declaration,'/rule/declaration')
    positive=negative=0
    for example in rule.examples:
        result=resolve_rule(rule,example.source)
        if (result['target'] if result else None)!=example.target:raise ValueError(f'命名规则不满足验证样例：{example.source}')
        positive+=example.target is not None;negative+=example.target is None
    if not positive or not negative:raise ValueError('命名规则必须包含正例和反例')


def check_module_rules(module):
    from .engine import fail
    seen=set()
    for rule in module.naming_rules:
        try:
            check_rule(rule)
            if (rule.id,rule.version) in seen:raise ValueError('重复命名规则')
            seen.add((rule.id,rule.version))
            if rule.scope=='module' and rule.scope_id not in (module.id,f'{module.id}@{module.version}'):raise ValueError('模块规则不能越过指定模块作用域')
            if rule.scope=='project' and (not module.project_id or rule.scope_id!=module.project_id):raise ValueError('项目规则必须属于明确的同一 project_id')
        except ValueError as exc:fail('NAMING_RULE',str(exc),'/module/naming_rules')
