import numpy as np
import pytest
from pe_workflow.expressions import evaluate,parse,run_function,ExpressionError,syntax_manifest,MAX_TEXT_CHARS


def test_scalar_text_concatenation_preserves_chinese_empty_and_order():
    assert evaluate(parse('a+b+c'),{'a':'电流超限；','b':'','c':'温度超限。'})=='电流超限；温度超限。'
    assert evaluate(parse('a+b'),{'a':'','b':''})==''
    assert evaluate(parse('a+b'),{'a':2,'b':3})==5
    np.testing.assert_array_equal(evaluate(parse('a+b'),{'a':np.array([1,2]),'b':2}),[3,4])


@pytest.mark.parametrize('expr,values',[
    ('a+b',{'a':'温度：','b':80}),('a+b',{'a':80,'b':'℃'}),
    ('a*b',{'a':'x','b':3}),('a-b',{'a':'x','b':'y'}),
    ('a+b',{'a':np.array(['a','b']),'b':np.array(['c','d'])}),
])
def test_text_does_not_open_implicit_coercion_or_array_arithmetic(expr,values):
    with pytest.raises(ExpressionError,match='第 1 行'):
        evaluate(parse(expr),values)


def test_text_result_budget_enforced_before_allocation():
    assert len(evaluate(parse('a+b'),{'a':'x'*(MAX_TEXT_CHARS-1),'b':'y'}))==MAX_TEXT_CHARS
    with pytest.raises(ExpressionError,match='字符预算'):
        evaluate(parse('a+b'),{'a':'x'*MAX_TEXT_CHARS,'b':'y'})
    assert syntax_manifest()['text_rules']['maximum_result_chars']==MAX_TEXT_CHARS


@pytest.mark.parametrize('current,temp,applicable,expected',[
    (1,25,1,''),(3,25,1,'电流超限；'),(3,150,1,'电流超限；温度超限；'),(1,25,0,'模型不适用；'),
])
def test_constraint_reason_paths(current,temp,applicable,expected):
    source='''def compute(inputs):
    r1 = "模型不适用；" if inputs["x"]["applicable"] == 0 else ""
    r2 = "电流超限；" if inputs["x"]["current"] > 2 else ""
    r3 = "温度超限；" if inputs["x"]["temp"] > 125 else ""
    return {"result": {"reason": r1+r2+r3}}
'''
    result=run_function(source,None,{'inputs':{'x':{'current':current,'temp':temp,'applicable':applicable}}},legacy=True)
    assert result['result']['reason']==expected


def test_mixed_addition_reports_actual_source_line():
    with pytest.raises(ExpressionError,match='第 3 行 Add.*str.*int'):
        run_function('def compute(inputs):\n    a = "温度："\n    return a + 80\n',None,{'inputs':{}},legacy=True)


def test_explicit_text_type_error_is_routed_to_candidate_repair():
    from pe_workflow.formula_authoring import execution_failure
    from pe_workflow.engine import WorkflowError
    from pe_workflow.repair_routing import route_failure
    with pytest.raises(ExpressionError) as failed:
        evaluate(parse('a+b'),{'a':'温度：','b':80})
    with pytest.raises(WorkflowError) as result:
        execution_failure({'diagnostics':[{'code':'COMPUTATION_ERROR','message':str(failed.value)}]},'/code')
    assert result.value.diagnostic.code=='CONTROLLED_RUNTIME_ERROR'
    assert route_failure(result.value,result.value.diagnostic.model_dump())['automatic']


def test_concatenated_member_text_is_collected_without_coercion():
    from test_planning_protocol import mini
    from pe_workflow.models import Port
    from pe_workflow.plan_controls import compile_plan
    from pe_workflow.engine import execute
    plan,registry=mini()
    reason=Port(type='string',semantic_id='constraint_reasons')
    plan.steps[0].outputs['loss'].fields['reason']=reason
    module=registry['copper@1'].model_copy(deep=True)
    module.outputs['loss'].fields['reason']=reason
    module.expressions['loss']="{'P':item['I']**2*device['R'], 'reason': ('电流超限；' if item['I']>2 else '') + ('电阻超限；' if device['R']>1 else '')}"
    registry['copper@1']=module
    wf=compile_plan(plan,'text_batch',registry=registry)
    out=execute(wf,registry,{'scan':{'currents':[1,3]},'device':{'R':2}})
    assert out['scan_loop__loss']['reason']==['电阻超限；','电流超限；电阻超限；']
    assert out['scan_loop__loss']['P']==[2,18]
