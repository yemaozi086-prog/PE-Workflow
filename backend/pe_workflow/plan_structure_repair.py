"""Platform-owned recovery for unambiguously misplaced plan envelope fields.

This repair only moves existing content between known schema positions.  It
never rewrites formulas, ports, constants, dependencies, checks or source text.
The source is the preserved original candidate receipt, not a later malformed
repair attempt.
"""
import copy
import json
import uuid

from .models import DesignPlan, StructureRepairConfirm
from .planning_protocol import compile_blueprint, ProtocolError
from .repair_routing import budget_snapshot
from .storage import Conflict, canonical, digest, now
from .engine import WorkflowError, fail


ROOT_FIELDS = {
    'title': str,
    'summary': str,
    'external_inputs': dict,
    'steps': list,
    'grids': list,
    'loops': list,
    'limitations': list,
}


def _type_name(expected):
    return {str: 'string', dict: 'object', list: 'array'}[expected]


def relocate_blueprint_structure(envelope):
    """Move only known top-level plan members out of port_defs/blueprint.

    A move is accepted only when the destination is absent or has identical
    content.  Any type mismatch or conflicting destination is returned as a
    blocking issue instead of being guessed.
    """
    corrected = copy.deepcopy(envelope)
    blueprint = corrected.get('blueprint')
    if not isinstance(blueprint, dict):
        return corrected, [], [{'path': '/blueprint', 'message': '缺少蓝图对象，不能执行结构移动'}]
    port_defs = blueprint.get('port_defs')
    if 'port_defs' not in blueprint:
        # No members to relocate: preserve the schema validator's complete
        # missing-field diagnostic instead of masking it with a wrapper error.
        return corrected, [], []
    if not isinstance(port_defs, dict):
        return corrected, [], [{'path': '/blueprint/port_defs', 'message': '端口定义表不是对象，不能执行结构移动'}]

    moves = []
    issues = []

    def move_from_port_defs(field, value):
        if field in blueprint and blueprint[field] != value:
            issues.append({'path': '/blueprint/port_defs/' + field,
                           'message': '目标位置已有不同内容：/blueprint/' + field + '；不能覆盖，需单独核对'})
            return False
        if field not in blueprint:
            blueprint[field] = copy.deepcopy(value)
        del port_defs[field]
        moves.append({'from': '/blueprint/port_defs/' + field,
                      'to': '/blueprint/' + field,
                      'content_hash': digest(value),
                      'basis': 'known_schema_member_moved_without_change'})
        return True

    for field, expected in ROOT_FIELDS.items():
        if field not in port_defs:
            continue
        value = port_defs[field]
        if not isinstance(value, expected):
            issues.append({'path': '/blueprint/port_defs/' + field,
                           'message': '该字段是已知顶层成员，但当前内容不是 ' + _type_name(expected) + '；不能自动移动'})
            continue
        destination = field
        if destination not in blueprint or blueprint[destination] is None:
            move_from_port_defs(field, value)
        elif blueprint[destination] == value:
            del port_defs[field]
            moves.append({'from': '/blueprint/port_defs/' + field,
                          'to': '/blueprint/' + destination,
                          'content_hash': digest(value),
                          'basis': 'duplicate_same_content_removed_from_wrong_level'})
        else:
            issues.append({'path': '/blueprint/port_defs/' + field,
                           'message': '目标 /blueprint/' + destination + ' 已有不同内容；不能覆盖'})

    # explanation belongs to the worker-result envelope, not to the plan.
    if 'explanation' in blueprint:
        explanation = blueprint['explanation']
        if not isinstance(explanation, str):
            issues.append({'path': '/blueprint/explanation',
                           'message': '说明字段不是字符串；不能自动移动到提交信封'})
        elif not corrected.get('explanation'):
            corrected['explanation'] = explanation
            del blueprint['explanation']
            moves.append({'from': '/blueprint/explanation',
                          'to': '/explanation',
                          'content_hash': digest(explanation),
                          'basis': 'known_envelope_member_moved_without_change'})
        elif corrected['explanation'] == explanation:
            del blueprint['explanation']
            moves.append({'from': '/blueprint/explanation',
                          'to': '/explanation',
                          'content_hash': digest(explanation),
                          'basis': 'duplicate_same_content_removed_from_wrong_level'})
        else:
            issues.append({'path': '/blueprint/explanation',
                           'message': '/explanation 已有不同内容；不能覆盖'})

    return corrected, moves, issues


class StructureRepair:
    def __init__(self, service):
        self.service = service

    def original_blueprint_receipt(self, task_id):
        with self.service.connect() as con:
            rows = list(con.execute(
                "SELECT payload_hash,document,created_at FROM design_candidate_receipts "
                "WHERE task_id=? AND json_extract(document,'$.blueprint') IS NOT NULL "
                "ORDER BY created_at ASC, rowid ASC", (task_id,)))
        for payload_hash, document, created_at in rows:
            try:
                value = json.loads(document)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and isinstance(value.get('blueprint'), dict):
                return {'payload_hash': payload_hash, 'document': value, 'created_at': created_at}
        return None

    def preview(self, key):
        task = self.service.get(key)
        receipt = self.original_blueprint_receipt(key)
        if not receipt:
            return {'available': False, 'task_id': key, 'revision': task['revision'],
                    'reason': '没有保存的原始蓝图候选回执，不能执行结构修复'}
        corrected, moves, issues = relocate_blueprint_structure(receipt['document'])
        plan = None
        plan_report = None
        if not issues:
            try:
                plan = compile_blueprint(corrected['blueprint'], self.service.modules())
                self.service.audit_plan(plan)
                plan_report = self.service.plan_report(task, plan)
                if not plan_report.get('valid'):
                    issues.extend(plan_report.get('issues', []))
            except ProtocolError as error:
                issues.extend(error.issues)
            except (WorkflowError, ValueError) as error:
                diagnostic = getattr(error, 'diagnostic', None)
                issues.append({'code': getattr(diagnostic, 'code', 'STRUCTURE_REPAIR_PLAN'),
                               'path': getattr(diagnostic, 'path', '/blueprint'),
                               'message': getattr(diagnostic, 'message', str(error))})
        valid = bool(plan is not None and not issues)
        result = {
            'available': bool(moves or issues),
            'valid': valid,
            'task_id': key,
            'revision': task['revision'],
            'source_receipt_hash': receipt['payload_hash'],
            'source_created_at': receipt['created_at'],
            'moves': moves,
            'issues': issues,
            'plan': plan.model_dump() if valid else None,
            'explanation': corrected.get('explanation', ''),
            'plan_report': plan_report,
            'notice': ('只移动已知的错层字段，不改公式、端口、单位、保护条件或依赖；'
                       '复检基于保存的原始候选回执，不调用模型、不追加额度。'),
        }
        result['preview_hash'] = digest(result)
        return result

    def confirm(self, key, req):
        old = self.service.get(key)
        self.service.check_revision(old, req.revision)
        if old.get('archived'):
            raise Conflict('任务已归档，请先恢复')
        view = self.preview(key)
        if view['preview_hash'] != req.preview_hash:
            raise Conflict('结构修复预览已变化，请刷新后重新核对')
        if not view['available'] or not view['valid']:
            fail('STRUCTURE_REPAIR_BLOCKED', '结构移动后仍有阻断问题，不能自动生成修订：' +
                 canonical(view.get('issues', [])), '/blueprint')
        receipt = f'{key}:{view["preview_hash"]}'
        with self.service.connect() as con:
            row = con.execute(
                "SELECT document FROM workflow_designs WHERE json_extract(document,'$.structure_repair_receipt')=?",
                (receipt,)).fetchone()
        if row:
            return self.service.public(json.loads(row[0]))

        child_id = uuid.uuid4().hex
        child = {
            'id': child_id,
            'parent_id': key,
            'request': copy.deepcopy(old['request']),
            'created_at': now(),
            'updated_at': now(),
            'revision': 1,
            'phase': 'plan',
            'status': 'awaiting_confirmation',
            'planning_protocol': old.get('planning_protocol', 'blueprint_v2'),
            'plan': copy.deepcopy(view['plan']),
            'structure_repair_receipt': receipt,
            'structure_repair_source_receipt': view['source_receipt_hash'],
            'structure_repair_moves': copy.deepcopy(view['moves']),
            'structure_repair_explanation': view.get('explanation', ''),
            'stage_budgets': budget_snapshot(old),
            'calls': old.get('calls', 0),
            'call_limit': old.get('call_limit', 0),
            'total_calls': old.get('total_calls', old.get('calls', 0)),
            'message': '平台已把错放的已知字段移回正确层级并复检原始方案；未调用模型、未追加额度。请核对恢复后的方案。',
            'events': [{'event': 'platform_structure_repair', 'actor': 'platform', 'at': now(),
                        'source_receipt_hash': view['source_receipt_hash'],
                        'moves': copy.deepcopy(view['moves']), 'model_calls': 0}],
        }
        with self.service.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            live_row = con.execute('SELECT document FROM workflow_designs WHERE id=?', (key,)).fetchone()
            if not live_row:
                raise Conflict('任务不存在')
            live = json.loads(live_row[0])
            if live['revision'] != req.revision:
                raise Conflict('任务已更新，请刷新后重新核对')
            live.update(status='paused', superseded_by=child_id,
                        message='已切换到平台结构修复复检方案；原始候选、调用次数和诊断保留')
            from .completion_recovery import Recovery
            Recovery.persist(con, live, 'superseded_by_structure_repair')
            con.execute('INSERT INTO workflow_designs VALUES(?,?,?)',
                        (child_id, 1, canonical(child)))
        return self.service.public(self.service.get(child_id))


def install(app, service):
    repair = StructureRepair(service)
    service.structure_repair = repair

    @app.get('/api/workflow-designs/{key}/structure-repair-preview')
    def preview(key: str):
        return repair.preview(key)

    @app.post('/api/workflow-designs/{key}/structure-repair-confirm')
    def confirm(key: str, req: StructureRepairConfirm):
        return repair.confirm(key, req)

    return repair
