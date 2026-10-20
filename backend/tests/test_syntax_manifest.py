import ast
import numpy as np
import pytest
from pe_workflow import expressions as ex
from test_designs import fixture
from test_design_lifecycle import active

def test_initial_rules_use_actual_validator_registries(fixture):
    c,s,_=fixture;rules=s.context(active(c,s))['controlled_syntax'];m=rules['manifest']
    assert set(m['expression_nodes'])=={t.__name__ for t in ex.ALLOWED}
    assert set(m['binary_operators'])=={t.__name__ for t in ex.BINARY}
    assert set(m['statement_nodes'])=={t.__name__ for t in ex.STATEMENT_NODES}
    assert 'BitAnd' not in m['expression_nodes'] and 'For' not in m['statement_nodes']
    assert m['budgets']['source_chars']==ex.MAX_SOURCE_CHARS
    assert m['budgets']['expression_ast_nodes']==ex.MAX_AST_NODES
    assert rules['array_logic']['and'] and '&' in rules['array_logic']['forbidden']
    assert {f['name'] for f in rules['numeric_catalog']['functions']}==set(ex.numeric.FUNCTIONS)

@pytest.mark.parametrize('source,expected',[('2+3',5),('2**3',8),('5%2',1),('3<4',True),('sqrt(4)',2),('2 if 3>1 else 0',2)])
def test_published_expression_forms_execute(source,expected):
    assert ex.evaluate(ex.parse(source),{})==expected

@pytest.mark.parametrize('source',['a & b','a | b','~a','[x for x in a]','numpy.all(a, axis=0)','numpy.logical_and(a,b)'])
def test_published_forbidden_forms_rejected(source):
    with pytest.raises(ex.ExpressionError):ex.parse(source)

def test_array_mask_instructions_execute_as_published(fixture):
    c,s,_=fixture;rules=s.context(active(c,s))['controlled_syntax']['array_logic']
    a=np.array([True,True,False,False]);b=np.array([True,False,True,False])
    for operation,expected in [('and',[True,False,False,False]),('or',[True,True,True,False]),('not',[False,False,True,True])]:
        np.testing.assert_array_equal(ex.evaluate(ex.parse(rules[operation]),{'a':a,'b':b}),expected)

def test_budgets_and_manifest_change_together(monkeypatch):
    monkeypatch.setattr(ex,'MAX_SOURCE_CHARS',80)
    assert ex.syntax_manifest()['budgets']['source_chars']==80
    ex.program('def compute(inputs):\n    return inputs\n',legacy=True)
    with pytest.raises(ex.ExpressionError):ex.program('def compute(inputs):\n    return inputs\n'+'#'*81,legacy=True)

def test_frozen_configuration_detects_interpreter_capability_changes(monkeypatch):
    from pe_workflow.designs import configuration
    before=configuration()['provider_hash']
    monkeypatch.setattr(ex,'MAX_AST_NODES',ex.MAX_AST_NODES-1)
    assert configuration()['provider_hash']!=before
