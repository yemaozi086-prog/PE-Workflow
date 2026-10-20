"""Source-proven shape corrections; never infer physical mappings or formulas."""
import copy
from .models import Port, PlanPatch
from .storage import digest
from .engine import fail
from .finite_control import row_port
from .control_contracts import describe

SHAPE_KEYS = ('type', 'value_type', 'axes')


def sources(plan, step):
    steps = {s.id: s for s in plan.steps}
    owners = {sid: loop for loop in plan.loops for sid in loop.step_ids}
    formal = {n['id']: n for n in describe(plan)['nodes'] if n['status'] == 'resolved'}
    loop = owners.get(step.id)
    env = plan.external_inputs
    if loop:
        contract = formal.get(loop.id)
        if not contract:
            return {}
        env = {k: Port.model_validate(v) for k, v in contract['inputs'].items() if k != 'rows'}
        env['item'] = row_port(Port.model_validate(contract['inputs']['rows']))
    result = {}
    for name, b in step.input_sources.items():
        source = None
        if b.field or b.source == 'constant':
            continue
        if b.source == 'input':
            source = env.get(b.port)
        elif loop and b.node in loop.step_ids:
            source = steps[b.node].outputs.get(b.port)
        elif not loop and b.node in steps and b.node not in owners:
            source = steps[b.node].outputs.get(b.port)
        elif not loop and b.node in formal:
            raw = formal[b.node]['outputs'].get(b.port)
            if raw:
                source = Port.model_validate(raw)
        if source is not None:
            result[name] = source
    return result


def corrected_inputs(plan, step):
    ports = copy.deepcopy(step.inputs)
    changes = []
    for bag, source in sources(plan, step).items():
        if bag not in ports or ports[bag].type != 'flat_dict' or source.type != 'flat_dict':
            continue
        binding = step.input_sources[bag]
        for name, old in ports[bag].fields.items():
            key = binding.field_map.get(name, name)
            producer = source.fields.get(key)
            if producer is None:
                continue
            from .engine import compatible, WorkflowError
            try:
                compatible(producer, old, '/shape-repair')
            except WorkflowError:
                pass
            else:
                continue
            updates = {k: copy.deepcopy(getattr(producer, k)) for k in SHAPE_KEYS}
            new = old.model_copy(update=updates)
            if new == old:
                continue
            # Do not turn a true physical mismatch into a shape correction.
            try:
                compatible(producer, new, '/shape-repair/' + step.id + '/' + bag + '/' + name)
            except WorkflowError:
                continue
            ports[bag].fields[name] = new
            changes.append({'step_id': step.id, 'port': bag, 'field': name,
                            'source': binding.model_dump(), 'source_field': key,
                            'before': {k: old.model_dump()[k] for k in SHAPE_KEYS},
                            'after': {k: new.model_dump()[k] for k in SHAPE_KEYS}})
    return ports, changes


def validate_edit(plan, old, edit):
    expected, changes = corrected_inputs(plan, old)
    if edit.input_sources != old.input_sources or edit.inputs != expected or not changes:
        fail('PATCH_SHAPE_SCOPE', '形状修复必须逐字段复制已声明来源的类型、维度和轴；不得改变来源、字段、单位、语义、必填性或默认值', '/patch/edits')
    if edit.guard_field_map:
        fail('PATCH_SHAPE_SCOPE', '形状修复不同时改写保护字段映射', '/patch/edits')
    return changes


def preview(plan):
    edits = []
    changes = []
    for step in plan.steps:
        ports, delta = corrected_inputs(plan, step)
        if delta:
            edits.append({'step_id': step.id, 'inputs': ports, 'input_sources': copy.deepcopy(step.input_sources),
                          'repair_shapes': True, 'derive_interface': bool(step.module_ref)})
            changes.extend(delta)
    return PlanPatch(base_hash=digest(plan.model_dump()), edits=edits), changes
