import copy

from test_binding_repair_receipts import blueprint_from, register_mini
from test_designs import callback, create, fixture


def test_known_misplaced_members_are_moved_without_model_call(fixture, monkeypatch):
    c, s, store = fixture
    plan, _ = register_mini(store)
    raw = blueprint_from(plan)
    from pe_workflow.planning_protocol import compile_blueprint
    original_steps = compile_blueprint(copy.deepcopy(raw), s.modules()).model_dump()['steps']
    original_limitations = copy.deepcopy(raw.get('limitations', []))
    raw['port_defs']['steps'] = raw.pop('steps')
    if 'limitations' in raw:
        raw['port_defs']['limitations'] = raw.pop('limitations')
    else:
        raw['port_defs']['limitations'] = []
        original_limitations = []
    raw['explanation'] = '原始候选说明应属于提交信封，不属于蓝图。'

    # Simulate a retained legacy failure from before automatic envelope relocation.
    with monkeypatch.context() as legacy:
        legacy.setattr('pe_workflow.plan_structure_repair.relocate_blueprint_structure', lambda value: (copy.deepcopy(value), [], []))
        task = callback(c, s, create(c), kind='plan_blueprint', blueprint=raw)
    assert task['status'] in ('dispatch_ready', 'paused', 'needs_clarification'), task
    assert task['calls'] == 1
    source_hash = task['candidate_receipt_hash']

    view = c.get('/api/workflow-designs/' + task['id'] + '/structure-repair-preview').json()
    assert view['available'] is True and view['valid'] is True, view
    assert {(m['from'], m['to']) for m in view['moves']} >= {
        ('/blueprint/port_defs/steps', '/blueprint/steps'),
        ('/blueprint/port_defs/limitations', '/blueprint/limitations'),
        ('/blueprint/explanation', '/explanation'),
    }
    assert view['source_receipt_hash'] != source_hash or view['source_receipt_hash']
    assert view['plan']['steps'] == original_steps
    assert view['plan']['limitations'] == original_limitations

    child = c.post('/api/workflow-designs/' + task['id'] + '/structure-repair-confirm', json={
        'revision': view['revision'],
        'preview_hash': view['preview_hash'],
        'confirm': True,
    }).json()
    assert child['id'] != task['id']
    assert child['status'] == 'awaiting_confirmation'
    assert child['calls'] == task['calls']
    assert child['plan']['steps'] == original_steps
    assert child['plan']['limitations'] == original_limitations
    assert s.get(task['id'])['superseded_by'] == child['id']
    assert s.get(task['id'])['calls'] == task['calls']


def test_conflicting_destination_blocks_automatic_move(fixture):
    c, s, store = fixture
    plan, _ = register_mini(store)
    raw = blueprint_from(plan)
    raw['port_defs']['steps'] = raw['steps']
    raw['steps'] = [{'id': 'different'}]
    task = callback(c, s, create(c), kind='plan_blueprint', blueprint=raw)
    view = c.get('/api/workflow-designs/' + task['id'] + '/structure-repair-preview').json()
    assert view['available'] is True
    assert view['valid'] is False
    assert any('已有不同内容' in issue['message'] for issue in view['issues'])


def test_callback_auto_moves_known_members_before_validation(fixture):
    c, s, store = fixture
    plan, _ = register_mini(store)
    raw = blueprint_from(plan)
    original_steps = copy.deepcopy(raw['steps'])
    raw['port_defs']['steps'] = raw.pop('steps')
    raw['port_defs']['limitations'] = raw.pop('limitations', [])
    raw['explanation'] = '应属于提交信封'
    task = callback(c, s, create(c), kind='plan_blueprint', blueprint=raw)
    assert task['status'] == 'awaiting_confirmation', task
    assert task['calls'] == 1 and task['last_report'] is None
    assert [s['id'] for s in task['plan']['steps']] == [s['id'] for s in original_steps]
    assert {(m['from'], m['to']) for m in task['structure_repair_audit']} >= {
        ('/blueprint/port_defs/steps', '/blueprint/steps'),
        ('/blueprint/port_defs/limitations', '/blueprint/limitations'),
        ('/blueprint/explanation', '/explanation'),
    }
