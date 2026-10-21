from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator, model_serializer


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, populate_by_name=True)


class ValueType(StrictModel):
    dtype: Literal["float64", "float32", "int64", "string"] = "float64"
    shape: list[int | str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def dimensions(self):
        for size in self.shape:
            if isinstance(size, int) and (isinstance(size, bool) or size < 1 or size > 100000):
                raise ValueError("固定维度必须在 1–100000 之间")
            if isinstance(size, str) and (not size.isidentifier() or size.startswith("_")):
                raise ValueError("符号维度必须是普通标识符")
        return self


class Axis(StrictModel):
    name: str
    coordinate_ref: str | None = None
    monotonic: bool = False


class DefinitionRef(StrictModel):
    id: str
    version: int = Field(ge=1)


class UnitDefinition(StrictModel):
    dimension: str
    scale: float = Field(gt=0)
    offset: float = 0


class CatalogRef(DefinitionRef):
    name: str


class ExtensionRef(StrictModel):
    project_id: str
    kind: Literal['unit','parameter','alias']
    name: str
    version: int = Field(ge=1)
    fingerprint: str

class Port(StrictModel):
    missing_when: str | None = None  # sibling 0/1 validity column; null encodes missing NaN
    @model_serializer(mode='wrap')
    def serialize_extensions(self,handler):
        result=handler(self)
        if self.missing_when is None:result.pop('missing_when',None)
        if not self.extension_refs:result.pop('extension_refs',None)
        return result
    type: Literal["number", "string", "boolean", "array", "flat_dict", "collection"] = "number"
    kind: Literal["bare", "flat_dict", "collection"] | None = None
    value_type: ValueType | None = None
    axes: list[Axis] = Field(default_factory=list)
    definition_ref: DefinitionRef | None = None
    unit_definition: UnitDefinition | None = None
    extension_refs: list[ExtensionRef] = Field(default_factory=list)
    catalog_ref: CatalogRef | None = None
    chinese_name: str = ""
    family_id: str = ""
    entry_type: Literal['quantity','reference','control'] = 'quantity'
    device: str = ""
    nature: str = ""
    statistic: str = ""
    event: str = ""
    direction: str = ""
    aggregation: str = ""
    loss_component: str = ""
    instance_id: str = ""
    semantic_id: str = ""
    semantic_evidence: str = ""
    quantity: str = ""
    characteristic: str = ""
    scope: str = ""
    role: str = ""
    index: str = ""
    unit: str = ""
    description: str = ""
    required: bool = True
    default: Any = None
    fields: dict[str, Port] = Field(default_factory=dict)
    item: Port | None = None

    @model_validator(mode="before")
    @classmethod
    def canonical_kind(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "semantic" in data:
            semantic = data.pop("semantic")
            if not isinstance(semantic, dict) or set(semantic) - {"quantity", "characteristic", "scope", "role", "index"}:
                raise ValueError("semantic 包含未知维度")
            for key, value in semantic.items():
                if key in data and data[key] != value:
                    raise ValueError(f"重复语义声明冲突：{key}")
                data[key] = value
        if data.get("kind") in ("flat_dict", "collection"):
            data.setdefault("type", data["kind"])
        elif data.get("kind") == "bare" and isinstance(data.get("value_type"), dict):
            data.setdefault("type", "array" if data["value_type"].get("shape") else "number")
        return data

    @model_validator(mode="after")
    def shape(self):
        expected_kind = self.type if self.type in ("flat_dict", "collection") else "bare"
        if self.kind is not None and self.kind != expected_kind:
            raise ValueError("kind 和 type 不一致")
        self.kind = expected_kind
        if self.type == "array":
            if self.value_type is None or not self.value_type.shape:
                raise ValueError("数值数组必须声明 value_type.shape")
            if self.value_type.dtype == "string" and len(self.value_type.shape)!=1:
                raise ValueError("文字数组只支持一维工况列表")
            if len(self.axes) != len(self.value_type.shape):
                raise ValueError("数组的每个维度必须声明轴")
        elif self.axes or (self.value_type is not None and self.value_type.shape):
            raise ValueError("只有数组可以声明非空形状或轴")
        if self.type == "flat_dict":
            if not self.fields or any(p.type in ("flat_dict", "collection") for p in self.fields.values()):
                raise ValueError("flat_dict 必须声明字段，且字段必须是裸参数")
        elif self.fields:
            raise ValueError("只有 flat_dict 可以声明 fields")
        if (self.type == "collection") != (self.item is not None):
            raise ValueError("collection 必须声明 item，其他类型不能声明 item")
        if self.required and self.default is not None:
            raise ValueError("有默认值的端口需显式设置 required=false")
        return self


class Conversion(StrictModel):
    from_unit: str
    to_unit: str


class Mapping(StrictModel):
    from_path: list[str | int] | None = Field(default=None, alias="from")
    assemble: dict[str, Mapping] | None = None
    conversion: Conversion | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.from_path is None) == (self.assemble is None):
            raise ValueError("映射必须选择 from 或 assemble 之一")
        if self.from_path is not None and not self.from_path:
            raise ValueError("from 路径不能为空")
        if self.assemble is not None and (not self.assemble or self.conversion):
            raise ValueError("组装必须非空，不能对整包设置单位换算")
        return self


class Adapter(StrictModel):
    arguments: dict[str, Mapping]
    returns: dict[str, Mapping]


class NumericRange(StrictModel):
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("适用范围下界不能大于上界")
        return self


class TransformEvidence(StrictModel):
    operation: Literal["transpose", "reshape", "resample", "slice"]
    preconditions: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class ParameterDefinition(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,99}$")
    version: int = Field(ge=1)
    name: str
    port: Port
    aliases: list[str] = Field(default_factory=list)
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def no_reference(self):
        if self.port.definition_ref:
            raise ValueError("参数定义本体不能再引用其他定义")
        return self


class Binding(StrictModel):
    # Constants are explicit, so strings never double as addresses.
    source: Literal["input", "node", "constant"]
    node: str | None = None
    port: str | None = None
    field: str | None = None
    value: Any = None
    field_map: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def address(self):
        if self.source == "constant":
            if self.node is not None or self.port is not None or self.field is not None:
                raise ValueError("常量绑定不能包含地址")
            if self.field_map:raise ValueError('常量字典应直接按目标契约提供，不能声明字段来源映射')
        elif not self.port or (self.source == "node" and not self.node):
            raise ValueError("绑定必须包含端口，node 来源还必须包含节点 ID")
        elif self.source == "input" and self.node is not None:
            raise ValueError("input 来源不能包含 node")
        return self


class RuleExample(StrictModel):
    source: str
    target: str | None = None


class NamingRule(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,99}$')
    version: int = Field(ge=1)
    scope: Literal['module','project','shared'] = 'module'
    scope_id: str = Field(min_length=1)
    source_pattern: str = Field(min_length=1,max_length=100)
    target_pattern: str = Field(min_length=1,max_length=100)
    declaration: Port
    evidence: str = Field(min_length=1)
    examples: list[RuleExample] = Field(min_length=2,max_length=30)


class RuleRegistration(StrictModel):
    rule: NamingRule
    confirm_shared: bool = False


class CatalogImport(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,99}$')
    version: int = Field(ge=1)
    csv_text: str = Field(min_length=1,max_length=1000000)


class Module(StrictModel):
    input_checks: list[str] = Field(default_factory=list, max_length=40)
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    version: int = Field(ge=1)
    name: str
    description: str = ""
    inputs: dict[str, Port]
    outputs: dict[str, Port]
    contract_version: Literal["1.0", "2.0", "3.0"] = "1.0"
    project_id: str = ""
    naming_rules: list[NamingRule] = Field(default_factory=list,max_length=100)
    implementation: Literal["expressions", "python_subset", "python_function", "trusted_builtin"] = "expressions"
    builtin_id: str | None = None
    builtin_hash: str | None = None
    expressions: dict[str, str] = Field(default_factory=dict)
    source: str = ""
    provenance: str
    entrypoint: str | None = None
    adapter: Adapter | None = None
    capabilities: list[str] = Field(default_factory=list)
    applies_to: dict[str, list[str]] = Field(default_factory=dict)
    domains: dict[str, NumericRange] = Field(default_factory=dict)
    transforms: list[TransformEvidence] = Field(default_factory=list)


class Node(StrictModel):
    input_checks: list[str] = Field(default_factory=list, max_length=40)
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    label: str = ""
    kind: Literal["compute", "switch", "foreach", "aggregate", "batch", "grid"] = "compute"
    grid_ports: dict[str, Port] = Field(default_factory=dict)
    grid_axes: dict[str, str] = Field(default_factory=dict)
    grid_order: list[str] = Field(default_factory=list)
    inputs: dict[str, Binding] = Field(default_factory=dict)
    module_id: str | None = None
    module_version: int | None = Field(default=None, ge=1)
    cases: dict[str, Workflow] = Field(default_factory=dict)
    body: Workflow | None = None
    operation: Literal["sum", "mean", "max", "min"] | None = None
    field: str | None = None

    @model_validator(mode="after")
    def configuration(self):
        if self.kind == 'grid':
            if not self.grid_ports or not self.grid_axes or len(self.grid_order)!=len(self.grid_axes) or set(self.grid_order)!=set(self.grid_axes):raise ValueError('grid 必须声明输入接口、轴映射及无重复的遍历轴顺序')
        elif self.grid_ports or self.grid_axes or self.grid_order:raise ValueError('只有 grid 可以声明轴映射')
        if self.kind == "compute":
            if not self.module_id or not self.module_version:
                raise ValueError("compute 必须固定 module_id 和 module_version")
        elif self.module_id or self.module_version:
            raise ValueError("只有 compute 使用 module 引用")
        if self.kind == "switch" and not self.cases:
            raise ValueError("switch 必须声明 cases")
        if self.kind != "switch" and self.cases:
            raise ValueError("只有 switch 使用 cases")
        if (self.kind in ("foreach", "batch")) != (self.body is not None):
            raise ValueError("foreach 必须声明 body，其他节点不能声明 body")
        if self.kind == "aggregate" and not self.operation:
            raise ValueError("aggregate 必须声明 operation")
        if self.kind != "aggregate" and (self.operation or self.field):
            raise ValueError("只有 aggregate 使用 operation/field")
        return self


class Workflow(StrictModel):
    schema_version: Literal["1.0", "2.0", "3.0"] = "1.0"
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    name: str
    description: str = ""
    project_id: str = ""
    inputs: dict[str, Port] = Field(default_factory=dict)
    nodes: list[Node] = Field(default_factory=list, max_length=200)
    outputs: dict[str, Binding]
    output_ports: dict[str, Port]
    layout: dict[str, dict[str, float]] = Field(default_factory=dict)


Node.model_rebuild()
Workflow.model_rebuild()


class Diagnostic(StrictModel):
    code: str
    message: str
    path: str
    severity: Literal["error", "warning"] = "error"
    node_id: str | None = None
    port: str | None = None
    suggestion: str = ""


class WorkflowRequest(StrictModel):
    workflow: Workflow
    inputs: dict[str, Any] | None = None


class RunRequest(WorkflowRequest):
    timeout_seconds: float = Field(default=30, ge=1, le=120)


class SaveRequest(WorkflowRequest):
    expected_revision: int = Field(ge=0)
    expected_draft_revision: int | None = Field(default=None,ge=0)


class Anchor(StrictModel):
    inputs: dict[str, Any]
    expected: dict[str, Any]
    tolerance: float = Field(default=1e-8, ge=0, le=0.1)


class RevisionRequest(StrictModel):
    base_version: int = Field(ge=1)
    module: Module
    anchors: list[Anchor] = Field(min_length=1, max_length=50)


class OptimizeRequest(RunRequest):
    bounds: dict[str, tuple[float, float]]
    objective: str
    direction: Literal["min", "max"] = "min"
    seed: int = 42
    maxiter: int = Field(default=8, ge=1, le=100)
    popsize: int = Field(default=5, ge=5, le=20)


class CandidateRequest(StrictModel):
    module: Module
    anchors: list[Anchor] = Field(min_length=1, max_length=50)
    context_id: str | None = None


class SourceRequest(StrictModel):
    source: str = Field(max_length=16000)
    entrypoint: str | None = None


class RegistrationContextRequest(SourceRequest):
    module_id: str
    project_id: str = ""
    instruction: str = Field(min_length=1, max_length=16000)
    declared_facts: dict = Field(default_factory=dict)
    anchors: list[Anchor] = Field(default_factory=list, max_length=50)


class ContextRequest(WorkflowRequest):
    node_id: str
    instruction: str = Field(min_length=1, max_length=16000)
    source: str = Field(default="", max_length=16000)


class ReplacementRequest(WorkflowRequest):
    node_id: str
    module_id: str
    module_version: int = Field(ge=1)


class EvaluationCase(StrictModel):
    name: str = Field(min_length=1,max_length=100)
    added_bindings: dict[str, Binding]
    expected: dict[str, Any] = Field(min_length=1)
    tolerance: float = Field(default=1e-9,ge=0,le=.1)


class ChangeTaskRequest(RunRequest):
    expected_revision: int = Field(ge=0)
    node_path: list[str] = Field(min_length=1, max_length=24)
    instruction: str = Field(min_length=1, max_length=16000)
    anchors: list[Anchor] = Field(default_factory=list, max_length=50)
    added_bindings: dict[str, Binding] = Field(default_factory=dict)
    added_input_ports: dict[str, Port] = Field(default_factory=dict)
    unchanged_outputs: list[str] = Field(default_factory=list)
    alpha_demo: bool = False
    alpha: float | None = None
    evaluation_cases: list[EvaluationCase] = Field(default_factory=list,max_length=10)
    acceptance_label: str = Field(default='用户提供的验收依据；平台不证明物理准确性',max_length=300)


class ChangeApplyRequest(StrictModel):
    context_id: str
    module: Module
    added_bindings: dict[str, Binding] = Field(default_factory=dict)


class ClarifyRequest(StrictModel):
    questions: dict[str, str] = Field(min_length=1, max_length=12)


class AnswerRequest(StrictModel):
    answers: dict[str, str] = Field(min_length=1, max_length=12)


class AutomationRequest(ChangeTaskRequest):
    source: str = Field(min_length=1, max_length=16000)
    mode: Literal['interface', 'model']
    facts: str = Field(default='', max_length=16000)
    source_policy: Literal['preserve','modify'] = 'preserve'
    worker_protocol: Literal['module_v1','implementation_patch_v1'] = 'implementation_patch_v1'

    @model_validator(mode='after')
    def modification_scope(self):
        if self.source_policy=='modify' and self.mode!='model':
            raise ValueError('仅改变物理模型的任务允许修改源码')
        return self


class AutomationAnswer(StrictModel):
    answers: dict[str, str] = Field(default_factory=dict, max_length=12)
    anchors: list[Anchor] = Field(default_factory=list, max_length=50)
    facts: str = Field(default='', max_length=16000)


class AutomationContinue(StrictModel):
    approved: bool = False
    retry_uncertain: bool = False
    refresh_version: bool = False
    accept_target_change: bool = False


class ImplementationPatch(StrictModel):
    source: str = Field(min_length=1,max_length=16000)
    entrypoint: str = Field(min_length=1)
    adapter: Adapter
    provenance: str = Field(min_length=1,max_length=2000)
    description: str = Field(default='',max_length=2000)
    added_inputs: list[str] = Field(default_factory=list,max_length=20)


class AutomationResult(StrictModel):
    call_id: str
    token: str
    kind: Literal['candidate', 'questions', 'blocked']
    module: dict | None = None
    patch: dict | None = None
    questions: dict[str, str] = Field(default_factory=dict, max_length=12)
    explanation: str = Field(default='', max_length=16000)


class SceneBinding(StrictModel):
    role: str
    module_id: str
    module_version: int = Field(ge=1)
    evidence: str = Field(min_length=1)


class SceneRule(StrictModel):
    id: str
    kind: Literal['required_fact', 'compatible_profile', 'supported_control']
    fact: str
    expected: str
    evidence: str = Field(min_length=1)


class ScenePack(StrictModel):
    id: str
    version: int = Field(ge=1)
    name: str
    topology: str
    task_type: str
    review_status: Literal['extracted_from_code', 'needs_review', 'reviewed']
    bindings: list[SceneBinding]
    rules: list[SceneRule]
    limitations: list[str]


class AssemblyStep(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    role: Literal['candidate_current', 'capacitor_loss']
    module_id: str
    module_version: int = Field(ge=1)
    inputs: dict[str, Binding]
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(min_length=1, max_length=10)


class AssemblyPlan(StrictModel):
    steps: list[AssemblyStep] = Field(min_length=2, max_length=2)
    control: Literal['finite_batch']
    summary: str = Field(min_length=1, max_length=4000)


class AssemblyContextRequest(StrictModel):
    instruction: str = Field(min_length=1, max_length=16000)
    facts: dict[str, str]
    inputs: dict[str, Any]
    provenance: dict[str,str] = Field(default_factory=dict)


class AssemblyPreviewRequest(AssemblyContextRequest):
    plan: AssemblyPlan


class AssemblyTaskRequest(AssemblyContextRequest):
    timeout_seconds: float = Field(default=60, ge=1, le=120)
    max_calls: int = Field(default=4, ge=1, le=4)


class AssemblyWorkerResult(StrictModel):
    call_id: str
    token: str
    kind: Literal['plan', 'questions', 'blocked']
    plan: dict | None = None
    questions: dict[str, str] = Field(default_factory=dict, max_length=12)
    explanation: str = Field(default='', max_length=8000)


class AssemblyAnswer(StrictModel):
    answers: dict[str, str] = Field(min_length=1, max_length=12)


class AssemblyContinue(StrictModel):
    approved: bool
    retry_uncertain: bool = False
    refresh_configuration: bool = False


class DraftRequest(StrictModel):
    workflow: Workflow
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_draft_revision: int = Field(ge=0)
    base_revision: int = Field(ge=0)
    provenance: dict[str,str] = Field(default_factory=dict)


class WorkspaceName(StrictModel):
    name: str = Field(min_length=1,max_length=120)

    @field_validator('name')
    @classmethod
    def nonempty_name(cls,value):
        if not value.strip():raise ValueError('名称不能为空')
        return value.strip()


class WorkspaceCopy(WorkspaceName):
    revision: int | None = Field(default=None,ge=1)
    use_draft: bool = False


class WorkspaceSnapshotCopy(WorkflowRequest):
    name: str = Field(min_length=1,max_length=120)


class AssemblyInterpretRequest(StrictModel):
    instruction: str = Field(min_length=1,max_length=16000)

class AssemblyInputProfileRequest(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(min_length=1, max_length=2000)
    inputs: dict[str, Any] = Field(default_factory=dict, max_length=20)
    facts: dict[str, str] = Field(default_factory=dict, max_length=12)

    @field_validator('name', 'notes')
    @classmethod
    def nonempty_profile_text(cls, value):
        if not value.strip():raise ValueError('配置名称及适用说明不能为空')
        return value.strip()


class AssemblyInputProfileApply(StrictModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    facts: dict[str, str] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(default_factory=dict)


class AssemblyInputConfig(AssemblyInputProfileRequest):
    schema_version: Literal['assembly_input_config_v1']
    id: str = Field(pattern=r'^file_[a-z][a-z0-9_]{0,70}$')
    version: int = Field(ge=1)
    scene_id: str
    scene_version: int = Field(ge=1)
    module_refs: dict[str, int]
    contract_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    catalog_source: dict[str, Any]
    parameter_declarations: dict[str, dict[str, Any]]
    limitations: list[str]

# Design plans intentionally contain no operating-point values.
class DesignStep(StrictModel):
    derives_from: str | None = None
    input_sources: dict[str, Binding] = Field(default_factory=dict)
    input_checks: list[str] = Field(default_factory=list, max_length=40)
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,59}$')
    title: str = Field(min_length=1,max_length=160)
    purpose: str = Field(min_length=1,max_length=2000)
    dependencies: list[str] = Field(default_factory=list,max_length=40)
    inputs: dict[str, Port] = Field(default_factory=dict)
    outputs: dict[str, Port] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list,max_length=30)
    evidence: list[str] = Field(default_factory=list,max_length=30)
    formula_latex: str = Field(default='',max_length=8000)
    module_ref: str | None = None
    selection_reason: str = Field(default='',max_length=2000)

class PlanGrid(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,59}$')
    title: str
    input_sources: dict[str, Binding]
    axes: dict[str, str]  # output field -> explicit local bag.field; order is significant
    axis_order: list[str]

class PlanLoop(StrictModel):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,59}$')
    title: str
    kind: Literal['batch'] = 'batch'
    step_ids: list[str] = Field(min_length=1,max_length=60)
    rows: Binding
    captures: dict[str, Binding] = Field(default_factory=dict)
    outputs: dict[str, Binding] = Field(min_length=1)

class DesignPlan(StrictModel):
    external_inputs: dict[str, Port] = Field(default_factory=dict)
    title: str = Field(min_length=1,max_length=160)
    summary: str = Field(min_length=1,max_length=4000)
    steps: list[DesignStep] = Field(min_length=1,max_length=60)
    limitations: list[str] = Field(default_factory=list,max_length=40)
    grids: list[PlanGrid] = Field(default_factory=list,max_length=4)
    loops: list[PlanLoop] = Field(default_factory=list,max_length=4)

class PlanStepEdit(StrictModel):
    step_id: str
    inputs: dict[str, Port]
    input_sources: dict[str, Binding]
    derive_interface: bool = False
    repair_shapes: bool = False
    guard_field_map: dict[str, str] = Field(default_factory=dict, max_length=80)

class PlanPatch(StrictModel):
    base_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    edits: list[PlanStepEdit] = Field(default_factory=list, max_length=60)

class ShapeRepairConfirm(StrictModel):
    revision: int = Field(ge=1)
    patch_hash: str = Field(pattern=r'^[0-9a-f]{64}$')

class DesignRequest(StrictModel):
    project_id: str = Field(default='local',pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')
    instruction: str = Field(min_length=3,max_length=20000)
    allow_external_suggestions: bool = True
    environment_ref: str | None = None

class DesignRevision(StrictModel):
    revision: int = Field(ge=1)
    plan: DesignPlan

class DesignReplan(StrictModel):
    revision: int = Field(ge=1)
    instruction: str = Field(min_length=3, max_length=20000)
    max_calls: int = Field(default=4, ge=1, le=4)

class DesignConfirm(StrictModel):
    revision: int = Field(ge=1)
    allow_unverified: bool = False
    max_calls: int = Field(default=4,ge=1,le=4)

class ExtensionProposal(StrictModel):
    kind: Literal['unit','parameter','alias']
    name: str = Field(min_length=1,max_length=100)
    description: str = Field(default='',max_length=2000)
    evidence: str = Field(default='',max_length=4000)
    unit_definition: UnitDefinition | None = None
    declaration: Port | None = None
    alias_target: str | None = None

class ExtensionEdit(StrictModel):
    revision: int = Field(ge=1)
    proposal: ExtensionProposal

class ExtensionDecision(StrictModel):
    revision: int = Field(ge=1)
    approve: bool

class FormulaCode(StrictModel):
    """Implementation only. Confirmed contracts and identities are server-owned."""
    implementation: Literal['expressions','python_subset','python_function'] = 'python_subset'
    source: str = Field(default='',max_length=60000)
    expressions: dict[str,str] = Field(default_factory=dict,max_length=40)
    entrypoint: str | None = None
    adapter: Adapter | None = None
    transforms: list[TransformEvidence] = Field(default_factory=list,max_length=30)

class FormulaPrecheck(StrictModel):
    call_id: str
    token: str
    contract_hash: str
    code: FormulaCode

class FormulaSubmission(StrictModel):
    contract_hash: str
    code: FormulaCode
    precheck_id: str

class DesignWorkerResult(StrictModel):
    call_id: str
    token: str
    kind: Literal['plan','plan_patch','workflow','questions','blocked','module','extension_request']
    patch: PlanPatch | None = None
    extension_request: ExtensionProposal | None = None
    plan: DesignPlan | None = None
    workflow: Workflow | None = None
    module: Module | None = None
    questions: dict[str,str] = Field(default_factory=dict,max_length=20)
    explanation: str = Field(default='',max_length=10000)

class FormulaExample(StrictModel):
    inputs: dict[str,Any]
    outputs: dict[str,Any]

class FormulaRequest(StrictModel):
    revision: int = Field(ge=1)
    step_id: str
    formula: str = Field(min_length=1,max_length=10000)
    declarations: str = Field(min_length=1,max_length=10000)
    evidence: str = Field(min_length=1,max_length=4000)
    examples: list[FormulaExample] = Field(default_factory=list,max_length=30)

class ToolCompletionItem(StrictModel):
    step_id: str
    formula: str = Field(default='',max_length=10000)
    evidence: str = Field(default='',max_length=4000)
    examples: list[FormulaExample] = Field(default_factory=list,max_length=30)
    max_calls: int = Field(default=4,ge=1,le=4)

class ToolCompletionRequest(StrictModel):
    revision: int = Field(ge=1)
    confirm_plan: Literal[True]
    concurrency: int = Field(default=2,ge=1,le=10)
    items: list[ToolCompletionItem] = Field(min_length=1,max_length=60)

class ToolCompletionConcurrency(StrictModel):
    revision: int = Field(ge=1)
    concurrency: int = Field(ge=1,le=10)

class ToolCompletionRegister(StrictModel):
    revision: int = Field(ge=1)
    task_ids: list[str] = Field(min_length=1,max_length=60)
    allow_unverified: bool = False

class DesignAnswer(StrictModel):
    revision: int = Field(ge=1)
    answers: dict[str,str]

class DesignArchive(StrictModel):
    revision: int = Field(ge=1)
    archived: bool = True

class BindingAssemblyConfirm(StrictModel):
    revision: int = Field(ge=1)
    preview_hash: str
    allow_unverified: Literal[True]

class CompletionRecovery(StrictModel):
    revision: int = Field(ge=1)
    preview_hash: str
    confirm: Literal[True]

class MissingOutputRevision(StrictModel):
    revision: int = Field(ge=1)
    port: str
    mask: str
    fields: list[str] = Field(min_length=1,max_length=40)
    evidence: str = Field(min_length=3,max_length=2000)
    confirm_missing_nan: Literal[True]

class DeclarationRecovery(CompletionRecovery):
    quantities: dict[str,str] = Field(default_factory=dict)

class DesignEvidenceReview(StrictModel):
    revision: int = Field(ge=1)
    evidence: str = Field(min_length=3,max_length=4000)
    keep_confirmed_basis: Literal[True]

class FormulaRecovery(StrictModel):
    revision: int = Field(ge=1)
    examples: list[FormulaExample] = Field(min_length=2,max_length=30)
    evidence: str = Field(min_length=3,max_length=4000)
    confirm: Literal[True]
    max_calls: Literal[1]

class DesignContinue(DesignConfirm):
    max_calls: int = Field(default=3,ge=1,le=3)


class StructureRepairConfirm(StrictModel):
    revision: int = Field(ge=1)
    preview_hash: str
    confirm: Literal[True]


class DependencyAudit(StrictModel):
    warnings: list[dict[str,Any]] = Field(default_factory=list)
    check_fields: list[str] = Field(default_factory=list)
    carried_fields: list[str] = Field(default_factory=list)
    status: Literal['passed','failed','uncertain']
    required_fields: list[str]
    optional_fields: list[str]
    read_fields: list[str]
    guarded_fields: list[str]
    unused_required_fields: list[str]
    undeclared_fields: list[str]
    unsafe_optional_fields: list[str]
    uncertainties: list[str]
    basis: str = 'restricted_python_ast_v1'
