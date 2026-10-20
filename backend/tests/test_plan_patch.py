import copy
import pytest
from pydantic import ValidationError
from pe_workflow.models import DesignPlan,PlanPatch,Port
from pe_workflow.storage import digest
from pe_workflow.engine import WorkflowError
from pe_workflow.plan_patch import apply_patch


def fixture_plan():
    field=Port(type='number',quantity='resistance',unit='ohm')
    bag=Port(type='flat_dict',fields={'R':field})
    return DesignPlan(title='frozen',summary='user formula',external_inputs={'parts':Port(type='flat_dict',fields={'R_hs':field})},steps=[dict(
        id='loss',title='loss',purpose='model',inputs={'device':bag},outputs={'loss':Port(type='flat_dict',fields={'p':Port(type='number',quantity='power_loss',unit='W')})},
        input_sources={'device':{'source':'input','port':'parts','field_map':{'R':'R_hs'}}},input_checks=["parts['R_hs'] >= 0"],formula_latex='P=I^2R',assumptions=['fixed R'],evidence=['user formula'])])


def patch(p,edits=None):return PlanPatch(base_hash=digest(p.model_dump()),edits=edits or [])


def test_empty_patch_translates_guards_without_agent_rewriting_them():
    p=fixture_plan();before=copy.deepcopy(p.model_dump());new,audit=apply_patch(p,patch(p))
    assert new.steps[0].input_checks==["device['R'] >= 0"]
    assert new.steps[0].formula_latex==p.steps[0].formula_latex and new.steps[0].id=='loss'
    assert new.steps[0].outputs==p.steps[0].outputs and p.model_dump()==before
    assert len(audit['guard_translations'])==1


def test_patch_cannot_inject_expressions_or_steps():
    p=fixture_plan()
    with pytest.raises(ValidationError):PlanPatch(base_hash=digest(p.model_dump()),edits=[dict(step_id='loss',inputs={},input_sources={},input_checks=[])])
    with pytest.raises(WorkflowError) as e:apply_patch(p,PlanPatch(base_hash='0'*64))
    assert e.value.diagnostic.code=='PATCH_BASE_MISMATCH'
    with pytest.raises(WorkflowError):apply_patch(p,patch(p,[dict(step_id='renamed',inputs={},input_sources={})]))


def test_interface_derivation_keeps_original_formula_and_outputs():
    p=fixture_plan();s=p.steps[0];s.module_ref='resistor@1'
    edit=dict(step_id='loss',derive_interface=True,inputs={'component':s.inputs['device']},input_sources={'component':s.input_sources['device']})
    new,_=apply_patch(p,patch(p,[edit]))
    assert new.steps[0].module_ref is None and new.steps[0].derives_from=='resistor@1'
    assert new.steps[0].input_checks==["component['R'] >= 0"]
    assert s.module_ref=='resistor@1'
    edit['derive_interface']=False
    with pytest.raises(WorkflowError) as e:apply_patch(p,patch(p,[edit]))
    assert e.value.diagnostic.code=='PATCH_DERIVATION_REQUIRED'


@pytest.mark.parametrize('change',[{'unit':'mohm'},{'required':False},{'default':0},{'quantity':'current'}])
def test_input_semantics_and_defaults_are_frozen(change):
    p=fixture_plan();s=p.steps[0];ports=copy.deepcopy(s.inputs);ports['device'].fields['R']=ports['device'].fields['R'].model_copy(update=change)
    with pytest.raises(WorkflowError) as e:apply_patch(p,patch(p,[dict(step_id='loss',inputs=ports,input_sources=s.input_sources)]))
    assert e.value.diagnostic.code=='PATCH_CONTRACT_CHANGE'


def test_ambiguous_guards_require_explicit_map_not_guessing():
    p=fixture_plan();s=p.steps[0];s.inputs['other']=s.inputs['device'].model_copy(deep=True);s.input_sources['other']=s.input_sources['device'].model_copy(deep=True)
    with pytest.raises(WorkflowError) as e:apply_patch(p,patch(p))
    assert e.value.diagnostic.code=='PATCH_GUARD_MAPPING_REQUIRED'
    new,_=apply_patch(p,patch(p,[dict(step_id='loss',inputs=s.inputs,input_sources=s.input_sources,guard_field_map={'parts.R_hs':'device.R'})]))
    assert new.steps[0].input_checks==["device['R'] >= 0"]


def test_dangling_source_rejected_atomically():
    p=fixture_plan();before=p.model_dump();s=p.steps[0]
    with pytest.raises(WorkflowError):apply_patch(p,patch(p,[dict(step_id='loss',inputs=s.inputs,input_sources={'device':{'source':'node','node':'missing','port':'parts'}})]))
    assert p.model_dump()==before

def test_failed_patch_reports_candidate_sources_not_original_guard_errors():
    p=fixture_plan();s=p.steps[0]
    # No guards here so the source error is reached; frozen plan remains valid.
    s.input_checks=[]
    with pytest.raises(WorkflowError) as e:apply_patch(p,patch(p,[dict(step_id='loss',inputs=s.inputs,input_sources={'device':{'source':'input','port':'unknown'}})]))
    assert e.value.plan_preflight['issues'][0]['code']=='PLAN_SOURCE_NOT_FOUND'
    assert p.steps[0].input_sources['device'].port=='parts'
