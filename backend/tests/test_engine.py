import copy
import math
import pytest
from pydantic import ValidationError

from pe_workflow.engine import WorkflowError, check_module, execute, fill_inputs, validate_workflow
from pe_workflow.examples import builtins, demo
from pe_workflow.expressions import execute_module
from pe_workflow.models import Module, Port, Workflow
from pe_workflow.storage import Conflict, Store


@pytest.fixture
def case():
    wf, inputs = demo()
    modules = {f"{m.id}@{m.version}": m for m in builtins()}
    return wf, inputs, modules


def test_units_multicase_and_known_results(case):
    wf, inputs, modules = case
    report = validate_workflow(wf, modules, inputs)
    assert report["valid"]
    assert {d["code"] for d in report["diagnostics"]} == {"UNIT_CONVERSION"}
    output = execute(wf, modules, inputs)
    assert output["mean_loss"] == pytest.approx(107.16)
    assert output["worst_loss"] == pytest.approx(107.52)
    assert [p["p_total"] for p in output["workpoint_results"]] == pytest.approx([106.8, 107.16, 107.52])


def test_only_selected_branch_executes(case):
    wf, inputs, modules = case
    trace = []
    outputs = execute(wf, modules, {**inputs, "mode": "baseline"}, trace)
    assert outputs["mean_loss"] == pytest.approx(106.8)
    assert all("/temperature/" not in t["path"] for t in trace)
    assert trace[0]["inputs"]["items"] == inputs["workpoints"]
    assert any(t["inputs"].get("selector") == "baseline" for t in trace)


def test_missing_branch_fails_without_outputs(case):
    wf, inputs, modules = case
    with pytest.raises(WorkflowError, match="没有分支"):
        execute(wf, modules, {**inputs, "mode": "unknown"})


@pytest.mark.parametrize("change,code", [
    (lambda w: w["nodes"].append(copy.deepcopy(w["nodes"][0])), "DUPLICATE_NODE"),
    (lambda w: w["nodes"][0]["inputs"].pop("items"), "MISSING_BINDING"),
    (lambda w: w["nodes"][0]["inputs"]["fs"].update(source="node", node="missing"), "UNKNOWN_REFERENCE"),
    (lambda w: w["nodes"][1]["inputs"]["items"].update(node="worst_loss", port="value"), "AGGREGATE_SOURCE"),
    (lambda w: w["nodes"][1].update(field="imaginary"), "UNKNOWN_FIELD"),
    (lambda w: w["inputs"]["fs"].update(unit="V"), "UNIT_DECLARATION"),
    (lambda w: w["inputs"]["fs"].update(unit="GHz"), "UNKNOWN_UNIT"),
    (lambda w: w["inputs"]["fs"].update(quantity="voltage", unit="V"), "SEMANTIC_CONFLICT"),
    (lambda w: w["outputs"].pop("mean_loss"), "OUTPUT_CONTRACT"),
    (lambda w: w["nodes"][0]["body"]["nodes"][0]["cases"]["baseline"]["nodes"][0].update(module_version=999), "MODULE_NOT_FOUND"),
])
def test_static_failures_are_located(case, change, code):
    wf, inputs, modules = case
    document = wf.model_dump()
    change(document)
    report = validate_workflow(Workflow.model_validate(document), modules, inputs)
    assert not report["valid"]
    assert report["diagnostics"][0]["code"] == code
    assert report["diagnostics"][0]["path"].startswith("/")


def test_cycle_rejected(case):
    wf, inputs, modules = case
    doc = wf.model_dump()
    doc["nodes"][0]["inputs"]["items"] = {"source": "node", "node": "mean_loss", "port": "value"}
    assert validate_workflow(Workflow.model_validate(doc), modules)["diagnostics"][0]["code"] == "CYCLE"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "100"])
def test_bad_numeric_input_rejected(case, value):
    wf, inputs, modules = case
    with pytest.raises(WorkflowError):
        execute(wf, modules, {**inputs, "fs": value})


def test_empty_aggregate_rejected(case):
    wf, inputs, modules = case
    with pytest.raises(WorkflowError, match="空集合"):
        execute(wf, modules, {**inputs, "workpoints": []})


@pytest.mark.parametrize("expression", ["__import__('os')", "open('x')", "math.__dict__", "[x for x in []]", "inputs.get('x')"])
def test_dangerous_or_unsupported_expressions_rejected(expression):
    module = Module(id="bad", version=1, name="bad", inputs={}, outputs={"value": {"type": "number"}}, expressions={"value": expression}, provenance="test")
    with pytest.raises(WorkflowError):
        check_module(module)


def test_python_subset_assign_if_math_and_dict():
    module = Module(id="square", version=1, name="square", inputs={"x": {"type": "number"}}, outputs={"value": {"type": "number"}}, implementation="python_subset", source='import math\ndef compute(inputs):\n    x = inputs["x"]\n    if x >= 0:\n        return {"value": math.sqrt(x)}\n    return {"value": 0}\n', provenance="test")
    check_module(module)
    assert execute_module(module, {"x": 9}) == {"value": 3}
    assert execute_module(module, {"x": -1}) == {"value": 0}


@pytest.mark.parametrize("source", ['import os\ndef compute(inputs):\n    return {}', 'def compute(inputs):\n    while True:\n        pass', 'def compute(inputs):\n    return {"x": inputs.__class__}', 'def compute(inputs):\n    return {"x": inputs["fn"]()}'])
def test_python_is_interpreted_not_arbitrary_execution(source):
    module = Module(id="bad", version=1, name="bad", inputs={}, outputs={"x": {"type": "number"}}, implementation="python_subset", source=source, provenance="test")
    with pytest.raises(WorkflowError):
        check_module(module)


def test_store_versions_and_conflicts(case, tmp_path):
    wf, inputs, modules = case
    store = Store(tmp_path / "data.sqlite")
    assert store.save(wf, inputs, 0) == 1
    with pytest.raises(Conflict):
        store.save(wf, inputs, 0)
    assert store.save(wf, {**inputs, "fs": 90}, 1) == 2
    assert store.workflow(wf.id, 1)["inputs"]["fs"] == 100
    assert store.workflow(wf.id)["inputs"]["fs"] == 90
    module = next(iter(modules.values()))
    store.add_module(module)
    with pytest.raises(Conflict):
        store.add_module(module)


def test_unknown_document_keys_rejected(case):
    wf, _, _ = case
    with pytest.raises(ValidationError):
        Workflow.model_validate({**wf.model_dump(), "hidden_fallback": True})


def test_explicit_defaults_inside_flat_dict_and_collection_are_materialized():
    scalar = Port(required=False, default=25)
    item = Port(type="flat_dict", fields={"temperature": scalar})
    ports = {"items": Port(type="collection", item=item)}
    assert fill_inputs({"items": [{}]}, ports, "/inputs") == {"items": [{"temperature": 25}]}


def test_warnings_have_complete_nested_paths(case):
    wf, inputs, modules = case
    report = validate_workflow(wf, modules, inputs)
    assert all(d["path"].startswith("/nodes/sweep/body/nodes/model/inputs/") for d in report["diagnostics"])


def test_restart_marks_unfinished_run_interrupted(tmp_path):
    file = tmp_path / "restart.sqlite"
    store = Store(file)
    store.save_run({"id": "interrupted-test", "created_at": "2026-10-15", "status": "running", "outputs": {"fake": 1}})
    restored = Store(file).run("interrupted-test")
    assert restored["status"] == "interrupted"
    assert restored["outputs"] == {}
    assert restored["diagnostics"][0]["code"] == "INTERRUPTED"

def test_optional_fields_without_defaults_can_be_omitted():
    from pe_workflow.engine import fill_inputs
    ports={'design':Port(type='flat_dict',fields={'C':Port(type='number',unit='F',quantity='capacitance'),'L':Port(type='number',unit='H',quantity='inductance',required=False)})}
    assert fill_inputs({'design':{'C':0.0001}},ports,'/inputs')=={'design':{'C':0.0001}}
    assert fill_inputs({'design':{'C':0.0001,'L':0}},ports,'/inputs')['design']['L']==0


def test_optional_supplied_invalid_value_is_not_silently_ignored():
    from pe_workflow.engine import fill_inputs,WorkflowError
    import pytest
    with pytest.raises(WorkflowError) as error:fill_inputs({'L':''},{'L':Port(type='number',required=False)},'/inputs')
    assert error.value.diagnostic.code=='VALUE_TYPE'


def test_optional_omission_does_not_relax_required_fields():
    from pe_workflow.engine import fill_inputs,WorkflowError
    import pytest
    with pytest.raises(WorkflowError) as error:fill_inputs({}, {'C':Port(type='number'),'L':Port(type='number',required=False)},'/inputs')
    assert error.value.diagnostic.code=='MISSING_INPUT'


def test_optional_dictionary_fields_can_be_absent_during_execution():
    from pe_workflow.engine import execute
    from pe_workflow.models import Module,Workflow,Node,Binding
    inp={'design':Port(type='flat_dict',fields={'C':Port(type='number',unit='F',quantity='capacitance'),'L':Port(type='number',unit='H',quantity='inductance',required=False)})}
    out={'result':Port(type='flat_dict',fields={'value':Port(type='number',quantity='capacitance',unit='F')})}
    m=Module(id='optional_demo',version=1,name='optional',contract_version='3.0',inputs=inp,outputs=out,expressions={'result':"{'value': design['C'] * 2}"},provenance='test')
    w=Workflow(schema_version='3.0',id='optional',name='optional',inputs=inp,nodes=[Node(id='calc',module_id=m.id,module_version=1,inputs={'design':Binding(source='input',port='design')})],outputs={'result':Binding(source='node',node='calc',port='result')},output_ports=out)
    assert execute(w,{'optional_demo@1':m},{'design':{'C':0.0001}})['result']['value']==0.0002


def test_optional_field_not_declared_by_source_can_be_omitted():
    from pe_workflow.engine import project_dictionary
    source=Port(type='flat_dict',fields={'C':Port(type='number',unit='F',quantity='capacitance')})
    target=Port(type='flat_dict',fields={**source.fields,'L':Port(type='number',unit='H',quantity='inductance',required=False)})
    assert project_dictionary({'C':0.0001},source,target,{},'/binding')=={'C':0.0001}


def test_thermal_resistance_units_have_zero_offset_and_distinct_dimension():
    from pe_workflow.engine import check_port,compatible,WorkflowError,UNITS
    from pe_workflow.adapters import conversion
    import pytest
    check_port(Port(type='number',quantity='thermal_resistance',unit='K/W'),'/thermal')
    assert UNITS['K/W']==('thermal_resistance',1,0)
    assert conversion(10,'K/W','degC/W')==10
    assert conversion(10,'degC/W','K/W')==10
    assert conversion(0,'degC','K')==pytest.approx(273.15)
    with pytest.raises(WorkflowError):compatible(Port(type='number',unit='K/W'),Port(type='number',unit='K'),'/thermal')
