import copy
from pe_workflow.evaluation import grade,equivalent


def good():
    return {'status':'applied','generation_count':1,'batch':1,'source_matches_patch':True,
            'original_ports_unchanged':True,'outputs':{'loss':1.},'case_results':[]}


def test_correctness_and_first_generation_are_separate():
    case={'kind':'interface','expected':{'loss':1.}}
    outcome=good();assert grade(case,outcome)['first_generation_passed']
    outcome['generation_count']=2
    assert grade(case,outcome)['passed'] and not grade(case,outcome)['first_generation_passed']


def test_grader_rejects_wrong_answer_scope_and_budget():
    case={'kind':'interface','expected':{'loss':1.}}
    for change in ({'outputs':{'loss':2.}},{'generation_count':0},{'generation_count':5},
                   {'batch':2},{'source_matches_patch':False},{'original_ports_unchanged':False}):
        outcome=good();outcome.update(change);assert not grade(case,outcome)['passed']


def test_missing_semantics_success_requires_specific_question_and_no_publication():
    case={'kind':'ambiguity'}
    outcome={'status':'needs_clarification','generation_count':1,'questions':{'unit':'返回单位和倍率是什么？'},
             'module_keys_unchanged':True,'published':False}
    assert grade(case,outcome)['first_generation_passed']
    for change in ({'published':True},{'module_keys_unchanged':False},{'questions':{'x':'请补充资料'}},{'questions':{'x':'输入 drive 的单位是什么？'}},{'status':'failed'}):
        bad=copy.deepcopy(outcome);bad.update(change);assert not grade(case,bad)['passed']


def test_all_expected_cases_must_be_compared_and_nonfinite_rejected():
    case={'kind':'formula','expected':{'loss':1.},'evaluation_cases':[{'expected':{'loss':2.}}]}
    outcome=good();assert not grade(case,outcome)['passed']
    outcome['case_results']=[{'passed':True,'outputs':{'loss':2.}}]
    assert grade(case,outcome)['passed']
    assert not equivalent({'loss':float('nan')},{'loss':1.})
