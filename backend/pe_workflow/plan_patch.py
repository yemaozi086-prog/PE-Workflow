"""Apply a bounded interface patch to frozen plan metadata, never to code."""
import ast
import copy
from collections import Counter
from .engine import fail,WorkflowError
from .expressions import parse
from .storage import canonical,digest
from .plan_audit import preflight


def declarations(ports):
    return Counter(canonical({k:v for k,v in f.model_dump().items() if k not in ('description','chinese_name')})
                   for p in ports.values() for f in p.fields.values())


def origin(step,bag,key,plan):
    if bag in step.inputs and key in step.inputs[bag].fields and bag in step.input_sources:
        b=step.input_sources[bag]
        return (b.source,b.node,b.port,b.field_map.get(key,key))
    if bag in plan.external_inputs and key in plan.external_inputs[bag].fields:return ('input',None,bag,key)
    return None


def translate_checks(old,new,plan,field_map):
    translations=[];used=set()
    def resolve(bag,key):
        address=bag+'.'+key;old_origin=origin(old,bag,key,plan)
        candidates=[(name,f) for name,p in new.inputs.items() for f in p.fields
                    if old_origin is not None and origin(new,name,f,plan)==old_origin]
        old_bag=old.inputs.get(bag) or plan.external_inputs.get(bag)
        old_field=old_bag.fields.get(key) if old_bag else None
        if old_field:
            candidates=[(n,f) for n,f in candidates if new.inputs[n].fields[f].unit==old_field.unit and new.inputs[n].fields[f].unit_definition==old_field.unit_definition]
        if address in field_map:
            used.add(address);target=field_map[address].split('.')
            if len(target)!=2 or target[0] not in new.inputs or target[1] not in new.inputs[target[0]].fields:
                fail('PATCH_GUARD_MAPPING','保护字段映射必须指向声明的局部字段','/patch/guard_field_map/'+address)
            pair=tuple(target)
            if candidates and pair not in candidates:fail('PATCH_GUARD_MAPPING','不能改变已有保护字段的实际来源','/patch/guard_field_map/'+address)
            before=old.inputs.get(bag) or plan.external_inputs.get(bag)
            if before is None or key not in before.fields or declarations({'x':before.model_copy(update={'fields':{key:before.fields[key]}})})!=declarations({'x':new.inputs[target[0]].model_copy(update={'fields':{target[1]:new.inputs[target[0]].fields[target[1]]}})}):
                fail('PATCH_GUARD_MAPPING','保护字段类型、单位、语义和必填性必须保持一致','/patch/guard_field_map/'+address)
            return pair
        if len(candidates)!=1:fail('PATCH_GUARD_MAPPING_REQUIRED','保护条件无法唯一对应到新接口，请提供明确字段映射：'+address,'/patch/guard_field_map/'+address)
        return candidates[0]
    class Rename(ast.NodeTransformer):
        def visit_Subscript(self,node):
            if not(isinstance(node.value,ast.Name) and isinstance(node.slice,ast.Constant)):
                fail('PATCH_GUARD_SYNTAX','保护条件只支持明确的字典字段引用','/patch/input_checks')
            bag,key=resolve(node.value.id,node.slice.value)
            return ast.copy_location(ast.Subscript(value=ast.Name(id=bag,ctx=ast.Load()),slice=ast.Constant(value=key),ctx=ast.Load()),node)
    checks=[]
    for expression in old.input_checks:
        try:
            tree=Rename().visit(parse(expression))
            translated=expression if ast.dump(tree)==ast.dump(parse(expression)) else ast.unparse(tree)
        except (SyntaxError,ValueError) as e:fail('PATCH_GUARD_SYNTAX',str(e),'/patch/input_checks')
        checks.append(translated)
        if translated!=expression:translations.append({'step_id':old.id,'before':expression,'after':translated,'basis':'explicit_source_bindings'})
    if used!=set(field_map):fail('PATCH_GUARD_MAPPING','映射包含未被保护条件使用的字段','/patch/guard_field_map')
    return checks,translations


def apply_patch(base,patch):
    if patch.base_hash!=digest(base.model_dump()):fail('PATCH_BASE_MISMATCH','补丁不属于当前冻结方案','/patch/base_hash')
    result=base.model_copy(deep=True);steps={s.id:s for s in result.steps};original={s.id:s for s in base.steps}
    ids=[e.step_id for e in patch.edits]
    if len(ids)!=len(set(ids)):fail('PATCH_DUPLICATE_STEP','同一步骤只能提交一次修改','/patch/edits')
    shape_changes=[]
    for edit in patch.edits:
        if edit.step_id not in steps:fail('PATCH_UNKNOWN_STEP','不能新增、改名或删除步骤','/patch/edits')
        old=original[edit.step_id];step=steps[edit.step_id]
        if set(edit.inputs)!=set(edit.input_sources):fail('PATCH_SOURCES','每个输入字典必须声明唯一来源','/patch/edits')
        if edit.repair_shapes:
            from .shape_repair import validate_edit
            shape_changes.extend(validate_edit(base,old,edit))
        elif any(p.type!='flat_dict' for p in edit.inputs.values()) or declarations(edit.inputs)!=declarations(old.inputs):
            fail('PATCH_CONTRACT_CHANGE','补丁只能拆分或改名输入字典，不能增删字段或改变单位、语义、必填性和默认值','/patch/edits')
        if old.module_ref and (edit.inputs!=old.inputs or edit.derive_interface):
            if not edit.derive_interface:fail('PATCH_DERIVATION_REQUIRED','注册工具接口修改必须派生新版本','/patch/edits')
            step.derives_from=old.module_ref;step.module_ref=None
        elif edit.derive_interface and not old.derives_from:fail('PATCH_DERIVATION_REQUIRED','没有已声明的派生来源','/patch/edits')
        dependencies={b.node for b in edit.input_sources.values() if b.source=='node'}
        if dependencies!=set(old.dependencies):fail('PATCH_DEPENDENCIES','接口修复不能改变计算依赖；改变计算链需重新规划','/patch/edits')
        step.inputs=copy.deepcopy(edit.inputs);step.input_sources=copy.deepcopy(edit.input_sources)
    # Guards are compiled from the frozen base; the agent never supplies expressions.
    maps={e.step_id:e.guard_field_map for e in patch.edits};translations=[]
    for step in result.steps:
        step.input_checks,changed=translate_checks(original[step.id],step,result,maps.get(step.id,{}));translations.extend(changed)
    report=preflight(result)
    if not report['valid']:
        try:fail('PLAN_PREFLIGHT','补丁接线检查未通过：'+canonical(report['issues']),'/patch')
        except WorkflowError as error:
            error.plan_preflight=report
            raise
    return result,{'base_hash':patch.base_hash,'edited_steps':ids,'guard_translations':translations,'shape_changes':shape_changes,
                   'frozen':['step_ids','order','dependencies','outputs','formulas','assumptions','evidence','external_inputs','guard_operators_and_thresholds']}
