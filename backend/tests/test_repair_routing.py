import pytest
from pe_workflow.engine import fail, WorkflowError
from pe_workflow.repair_routing import route_failure


@pytest.mark.parametrize('code,route', [
    ('DEPENDENCY_CONFLICT','candidate_repair'),
    ('PLAN_PREFLIGHT','candidate_repair'),
    ('UNKNOWN_UNIT','needs_information'),
    ('CONTRACT_CHANGE_REQUIRES_CONFIRM','needs_confirmation'),
    ('EXAMPLE_RESULT_MISMATCH','uncertain'),
    ('UNRECOGNIZED_ERROR','uncertain'),
])
def test_evidence_routes(code,route):
    with pytest.raises(WorkflowError) as caught:
        fail(code,'evidence','/candidate')
    decision=route_failure(caught.value,{'code':code,'path':'/candidate'})
    assert decision['route']==route
    assert decision['automatic']==(route=='candidate_repair')
    assert decision['evidence']=={'code':code,'path':'/candidate'}


def test_platform_exception_never_enters_candidate_loop():
    decision=route_failure(RuntimeError('database unavailable'),{'code':'DESIGN_VALIDATION'})
    assert decision['route']=='platform_fault' and not decision['automatic']


def test_unclassified_value_error_is_not_assumed_candidate_bug():
    assert route_failure(ValueError('unknown failure'),{'code':'DESIGN_VALIDATION'})['route']=='uncertain'
