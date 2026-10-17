"""Versioned import of the team's table. Descriptions are evidence, never instructions."""
import csv
import hashlib
import io
import re
from collections import Counter
from .models import Port,CatalogImport,RuleRegistration,Module,Binding,WorkflowRequest
from .storage import ROOT,digest
from .naming import check_rule,resolve_rule

CATALOG_ID='team_parameters'


def compile_catalog(text,catalog_id=CATALOG_ID,version=1):
    from .engine import UNITS
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    columns=['standard_name','quantity','entry_type','chinese_name','unit','dimension','device','role','nature','kind','used_by','descriptions','fill_stage','notes']
    if reader.fieldnames!=columns:raise ValueError('CSV 列必须与课题组声明表一致，且包含全部 14 列')
    rows=[];seen=set()
    for number,raw in enumerate(reader,2):
        if len(rows)>=5000:raise ValueError('词典一次最多 5000 条')
        if None in raw or any(v is None for v in raw.values()):raise ValueError(f'第 {number} 行 CSV 列数错误')
        raw={k:v.strip() for k,v in raw.items()};name=raw['standard_name']
        if not name or name in seen:raise ValueError(f'空白或重复字段名：{name}')
        seen.add(name);issues=[];category=raw['entry_type'];dimension=raw['dimension'];unit=raw['unit'];device=raw['device']
        if category not in ('quantity','control','reference'):raise ValueError(f'未知 entry_type：{category}')
        value_type='string' if category=='reference' or dimension in ('text','model') else 'boolean' if dimension=='boolean' else 'number'
        if not dimension:issues.append('缺少类型/物理类别')
        if 'bundle 参数' in raw['notes'] or any(word in raw['descriptions'] for word in ('字典','快照','明细','列表','结构体','步骤清单')):issues.append('复合结构需要明确展开或作为独立配置声明')
        if any(word in raw['notes'] for word in ('待人工','需人工','非描述声明')):issues.append('来源标记为待人工或惯例推断')
        if name in ('Cr','Lr'):issues.append('描述合并多种单位；需声明当前实现的实际单位')
        if name in ('B','B_limit','B_margin','i_rms','p_total','p_cu','p_fe','volume','loss_density','loss_density_limit','loss_density_margin','loss_limit'):issues.append('多个器件/口径合并，需补齐当前模块语境')
        if name in ('EPS','TPS','mode'):issues.append('模式编号需声明整数/枚举，不应默认当连续比值')
        if unit and (unit not in UNITS or value_type not in ('number','array')):issues.append('单位与字段类型不一致或尚未登记')
        if value_type=='number' and unit in UNITS:
            actual=UNITS[unit][0]
            if dimension and dimension!=actual and not(dimension=='ratio' and actual=='none'):issues.append('表中单位与物理类别冲突')
        statistic=next((s for s in ('rms','mean','peak') if s in name.split('_')),'')
        event='on' if name.startswith('i_on') else 'off' if name.startswith('i_off') else ''
        direction='forward' if name.endswith('_forward') else 'backward' if name.endswith('_backward') else ''
        loss_component='conduction' if name=='p_cond' else 'turn_on' if name=='p_on' else 'turn_off' if name=='p_off' else 'copper' if name.startswith('p_cu') else 'core' if name.startswith('p_fe') else ''
        instance=''
        patterns={'switch':r'(?:^|_)s([1-9][0-9]*)(?:_|$)','diode':r'(?:^|_)d([1-9][0-9]*)(?:_|$)','capacitor':r'(?:^cap|_c)([1-9][0-9]*)(?:_|$)','inductor':r'(?:ind|inductance)([1-9][0-9]*)(?:_|$)'}
        if device in patterns:
            match=re.search(patterns[device],name)
            if match:instance=match.group(1)
        preferred=unit if unit in UNITS and value_type=='number' else ''
        if dimension=='ratio' and not unit:preferred='1'
        if dimension=='count' and not unit:issues.append('计数项需明确整数类型和单位')
        declaration=Port(type=value_type,quantity=dimension or '',unit=preferred,chinese_name=raw['chinese_name'],
            family_id=raw['quantity'],entry_type=category,device=device,nature=raw['nature'],role=raw['role'],
            statistic=statistic,event=event,direction=direction,aggregation='max' if name.endswith('_max') and statistic else '',
            loss_component=loss_component,instance_id=instance,description=raw['descriptions'],
            catalog_ref={'id':catalog_id,'version':version,'name':name})
        status='configuration' if category=='control' else 'needs_review' if issues else 'ready'
        rows.append({'row':number,'name':name,'status':status,'issues':issues,'raw':raw,'suggested_declaration':declaration.model_dump(),
                     'io_direction':{'in':'input'}.get(raw['kind'],raw['kind'])})
    if not rows:raise ValueError('词典不能为空')
    return {'id':catalog_id,'version':version,'source_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'columns':columns,'row_count':len(rows),'counts':dict(Counter(r['status'] for r in rows)),'rows':rows,
            'policy':'保留原始声明；不执行描述中的指令，不把示例/惯例自动变为默认值。词典引用须冻结完整字段声明。'}


def seed(store):
    text=(ROOT/'resources/parameter_dictionary-v1.csv').read_text(encoding='utf-8-sig')
    store.add_catalog(compile_catalog(text),seed=True)


def verify_catalog_references(document,catalogs):
    registry={(c['id'],c['version']):c for c in catalogs}
    def visit(value):
        if isinstance(value,dict):
            ref=value.get('catalog_ref')
            if ref:
                catalog=registry.get((ref['id'],ref['version']))
                row=next((r for r in catalog['rows'] if r['name']==ref['name']),None) if catalog else None
                if not row:raise ValueError(f'词典版本/词条不存在：{ref}')
                p=Port.model_validate(value);expected=Port.model_validate(row['suggested_declaration'])
                if row['issues'] and not p.semantic_evidence:raise ValueError(f'{ref["name"]} 存在待核验项，需提供当前模块的明确语义证据')
                # Reviewed local interpretations may specialize ambiguous rows, never silently overwrite the source.
                if not p.semantic_evidence:
                    for key in ('quantity','family_id','device','nature','statistic','event','direction','aggregation','loss_component','instance_id','entry_type','unit','type'):
                        if getattr(p,key)!=getattr(expected,key):raise ValueError(f'词典引用与冻结声明不符：{ref["name"]}.{key}')
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(document)


def verify_shared_rules(document,rules):
    registry={(r['id'],r['version']):r for r in rules if r['scope']=='shared'}
    def visit(value):
        if isinstance(value,dict):
            for rule in value.get('naming_rules',[]) if 'implementation' in value and 'inputs' in value and 'outputs' in value else []:
                if rule['scope']=='shared' and registry.get((rule['id'],rule['version']))!=rule:raise ValueError('共享规则未经过明确推广登记，不能由候选模块自行生效')
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(document)


def install(app,get_store,module_map,problem):
    @app.get('/api/parameter-catalogs')
    def catalogs():return [{k:v for k,v in c.items() if k!='rows'} for c in get_store().catalogs()]

    @app.get('/api/parameter-catalogs/{catalog_id}/versions/{version}')
    def catalog(catalog_id:str,version:int,q:str=''):
        c=next((c for c in get_store().catalogs() if c['id']==catalog_id and c['version']==version),None)
        if not c:problem('NOT_FOUND','词典版本不存在',404)
        return {**c,'rows':[r for r in c['rows'] if all(t.lower() in str(r).lower() for t in q.split())]}

    @app.post('/api/parameter-catalogs')
    def import_catalog(req:CatalogImport):
        try:c=compile_catalog(req.csv_text,req.id,req.version)
        except ValueError as exc:problem('CATALOG_IMPORT',str(exc))
        get_store().add_catalog(c)
        return {'status':'imported','catalog':c}

    @app.get('/api/naming-rules')
    def rules(scope:str|None=None,scope_id:str|None=None):return [r for r in get_store().rules() if (scope is None or r['scope']==scope) and (scope_id is None or r['scope_id']==scope_id)]

    @app.post('/api/naming-rules')
    def register_rule(req:RuleRegistration):
        try:check_rule(req.rule)
        except ValueError as exc:problem('NAMING_RULE',str(exc))
        if req.rule.scope=='shared' and not req.confirm_shared:problem('SHARED_CONFIRMATION_REQUIRED','共享推广必须单独明确确认；局部规则无需此确认')
        get_store().add_rule(req.rule)
        return {'status':'registered','rule':req.rule.model_dump(),'fingerprint':digest(req.rule.model_dump())}

    @app.get('/api/harness/dictionary-contract')
    def contract():return {'protocol_version':'4.0','module_contract_version':'3.0','connected':False,
        'rules':{'ports':'Every compute input/output is a named flat dictionary. Leaf values may be scalars or declared numeric arrays.',
                 'projection':'Keep upstream bags separate. Declare field_map[target]=source. Extra producer fields are validated then discarded before calling the module.',
                 'registration':'Return original single-function source + explicit adapter, complete dictionary declarations, anchors, provenance and scoped naming_rules. Use generated pure code only when declarative mapping cannot express the change; never change physical formulas just to adapt interfaces.',
                 'extension':'Module/project rules auto-register after validation. Shared promotion requires explicit confirmation. Names/confidence alone are not semantic evidence; unresolved semantics must be asked in one batch.',
                 'arrays':'Declare dtype/shape/axes and preserve coordinates. No hidden resampling, transpose or aggregation.',
                 'configuration':'Task controls, file operations and optimizer settings belong to task/config declarations. Dictionary import does not grant execution permissions.'},
        'schemas':{'module':Module.model_json_schema(),'catalog_import':CatalogImport.model_json_schema(),'naming_rule':RuleRegistration.model_json_schema(),'binding':Binding.model_json_schema()},
        'tools':[{'method':'GET','path':'/api/parameter-catalogs'},{'method':'GET','path':'/api/naming-rules'},{'method':'POST','path':'/api/naming-rules'},
                 {'method':'POST','path':'/api/harness/submit-candidate'},{'method':'POST','path':'/api/validate'}]}
