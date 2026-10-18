import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import WorkflowError, check_module, fill_inputs, validate_value, validate_workflow
from .examples import builtins, demo
from .models import Diagnostic, Module, OptimizeRequest, RevisionRequest, RunRequest, SaveRequest, Workflow, WorkflowRequest
from .storage import Conflict, ROOT, Store, digest, now

app = FastAPI(title="Power Electronics Workflow", version="0.1.0")
store = Store()
for module in builtins():
    store.seed_module(module)
from .waveform import fixture
store.seed_module(fixture()[0])
from .waveform_flat import fixture as flat_waveform_fixture
store.seed_module(flat_waveform_fixture()[0])
from .catalog import seed as seed_catalog
seed_catalog(store)
from .dictionary_demo import fixture as dictionary_fixture
for module in dictionary_fixture()[0]:store.seed_module(module)
from .dbsrc import fixture as dbsrc_fixture
for module in dbsrc_fixture()[0]:store.seed_module(module)


def module_map():
    return {f"{m['id']}@{m['version']}": Module.model_validate(m) for m in store.modules()}


def problem(code, message, status=422, path="/"):
    raise HTTPException(status_code=status, detail={"code": code, "message": message, "path": path, "severity": "error"})


@app.exception_handler(RequestValidationError)
async def bad_request(request, exc):
    return JSONResponse(status_code=422, content={"status": "invalid", "diagnostics": [{"code": "SCHEMA_ERROR", "message": e["msg"], "path": "/" + "/".join(str(p) for p in e["loc"] if p != "body"), "severity": "error"} for e in exc.errors()]})


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "REQUEST_ERROR", "message": str(exc.detail), "path": "/", "severity": "error"}
    return JSONResponse(status_code=exc.status_code, content={"status": "failed", "diagnostics": [detail]})


@app.exception_handler(WorkflowError)
async def workflow_error(request, exc):
    return JSONResponse(status_code=422, content={"status": "invalid", "diagnostics": [exc.diagnostic.model_dump()]})


@app.exception_handler(Conflict)
async def conflict(request, exc):
    return JSONResponse(status_code=409, content={"status": "conflict", "diagnostics": [{"code": "REVISION_CONFLICT", "message": str(exc), "path": "/", "severity": "error"}]})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0", "harness": "external_dsh_worker", "automation": {"enabled": os.getenv("PE_DSH_ENABLED","1")!="0", "transport": "local_dsh_rpc", "description": "后台调用在任务提交时核对；此健康接口不保证 DSH 在线"}}


@app.get("/api/schema")
def schema():
    return Workflow.model_json_schema()


@app.get("/api/examples/thermal")
def thermal():
    wf, inputs = demo()
    return {"workflow": wf.model_dump(), "inputs": inputs, "revision": 0}


@app.get("/api/modules")
def modules():
    return store.modules()


@app.get('/api/module-families')
def module_families():
    from .module_library import catalog
    return catalog(store)


@app.get('/api/examples/dbsrc-thermal-task')
def dbsrc_thermal_task():
    from .thermal_task import recipe
    return recipe()


@app.get('/api/examples/waveform')
def waveform():
    _,wf,inputs=fixture()
    return {'workflow':wf.model_dump(),'inputs':inputs,'revision':0}


@app.get('/api/examples/dbsrc')
def dbsrc_example():
    _,wf,inputs=dbsrc_fixture()
    return {'workflow':wf.model_dump(),'inputs':inputs,'revision':0}


@app.get('/api/examples/dictionaries')
def dictionaries():
    _,wf,inputs=dictionary_fixture()
    return {'workflow':wf.model_dump(),'inputs':inputs,'revision':0}


@app.post("/api/modules")
def register(module: Module):
    check_module(module)
    if module.implementation=='python_function' or module.transforms or module.contract_version=='3.0':
        problem('CANDIDATE_REQUIRED','函数适配及显式变换必须通过 /api/harness/submit-candidate 提交验证样例')
    from .registration import verify_references
    try:verify_references(module.model_dump(),store.definitions(),store.catalogs(),store.rules())
    except ValueError as exc:problem('DEFINITION_CONFLICT',str(exc))
    if module.version != 1:
        problem("MODULE_VERSION", "新模块从 v1 开始；修订请调用 revisions 接口")
    store.add_module(module)
    return {"status": "registered", "module": module.model_dump()}


@app.post("/api/validate")
def validate(req: WorkflowRequest):
    from .registration import verify_references
    try:verify_references(req.workflow.model_dump(),store.definitions(),store.catalogs(),store.rules())
    except ValueError as exc:problem('DEFINITION_CONFLICT',str(exc))
    return {**validate_workflow(req.workflow, module_map(), req.inputs), "workflow": req.workflow.model_dump()}


@app.get("/api/workflows")
def workflows():
    return store.workflows()


@app.get("/api/workflows/{workflow_id}")
def load(workflow_id: str, revision: int | None = None):
    result = store.workflow(workflow_id, revision)
    if result is None:
        problem("NOT_FOUND", "工作流版本不存在", 404)
    return result


@app.get("/api/workflows/{workflow_id}/history")
def workflow_history(workflow_id: str):
    return store.history(workflow_id)


@app.post("/api/workflows")
def save(req: SaveRequest):
    # Drafts may be saved even while invalid, but the diagnostic is explicit.
    diagnostic = validate_workflow(req.workflow, module_map(), req.inputs)
    revision = store.save(req.workflow, req.inputs, req.expected_revision, req.expected_draft_revision)
    return {"status": "saved", "revision": revision, "validation": diagnostic}


def worker(payload, timeout):
    backend = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(backend), "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run([sys.executable, "-m", "pe_workflow.worker"], input=json.dumps(payload, ensure_ascii=False, allow_nan=False), text=True, encoding="utf-8", capture_output=True, cwd=backend, env=environment, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "outputs": {}, "trace": [], "diagnostics": [{"code": "TIMEOUT", "message": f"超过 {timeout:g} 秒，计算进程已终止", "path": "/", "severity": "error"}]}
    except OSError as exc:
        return {"status": "failed", "outputs": {}, "trace": [], "diagnostics": [{"code": "WORKER_START", "message": str(exc), "path": "/", "severity": "error"}]}
    if result.returncode != 0:
        return {"status": "failed", "outputs": {}, "trace": [], "diagnostics": [{"code": "WORKER_FAILED", "message": result.stderr[-2000:] or "计算进程异常退出", "path": "/", "severity": "error"}]}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {"status": "failed", "outputs": {}, "trace": [], "diagnostics": [{"code": "WORKER_PROTOCOL", "message": "计算进程没有返回有效 JSON", "path": "/", "severity": "error"}]}


def snapshot(req):
    with store.connect() as con:store.assert_workflow_active(con,req.workflow.id)
    from .registration import verify_references
    try:verify_references(req.workflow.model_dump(),store.definitions(),store.catalogs(),store.rules())
    except ValueError as exc:problem('DEFINITION_CONFLICT',str(exc))
    all_modules = module_map()
    report = validate_workflow(req.workflow, all_modules, req.inputs or {})
    if not report["valid"]:
        raise WorkflowError(Diagnostic.model_validate(report["diagnostics"][0]))
    required = set()
    def collect(wf):
        for node in wf.nodes:
            if node.kind == "compute":
                required.add(f"{node.module_id}@{node.module_version}")
            for case in node.cases.values():
                collect(case)
            if node.body:
                collect(node.body)
    collect(req.workflow)
    return {"workflow": req.workflow.model_dump(), "inputs": req.inputs or {}, "modules": [all_modules[k].model_dump() for k in sorted(required)], "parameter_catalogs": store.catalogs() if any(all_modules[k].contract_version=='3.0' for k in required) else []}


async def perform(req, optimization=None):
    data = snapshot(req)
    if optimization:
        data["optimization"] = optimization
    record = {"id": uuid.uuid4().hex, "created_at": now(), "status": "running", "workflow_name": req.workflow.name, "output_ports": {k:p.model_dump() for k,p in req.workflow.output_ports.items()}, "snapshot_hash": digest(data), "snapshot": data, "timeout_seconds": req.timeout_seconds, "outputs": {}, "trace": []}
    store.save_run(record)
    result = await asyncio.to_thread(worker, data, req.timeout_seconds)
    record.update(result)
    record["finished_at"] = now()
    store.save_run(record)
    return {k: v for k, v in record.items() if k != "snapshot"}


@app.post("/api/runs")
async def run(req: RunRequest):
    return await perform(req)


@app.get("/api/runs")
def runs():
    return store.runs()


@app.get("/api/runs/{run_id}")
def read_run(run_id: str):
    result = store.run(run_id)
    if not result:
        problem("NOT_FOUND", "运行记录不存在", 404)
    return result


@app.post("/api/runs/{run_id}/replay")
async def replay(run_id: str):
    original = store.run(run_id)
    if not original:
        problem("NOT_FOUND", "运行记录不存在", 404)
    record = {**original, "id": uuid.uuid4().hex, "created_at": now(), "status": "running", "replayed_from": run_id, "outputs": {}, "trace": []}
    record.pop("finished_at", None)
    record.pop("diagnostics", None)
    store.save_run(record)
    record.update(await asyncio.to_thread(worker, original["snapshot"], original["timeout_seconds"]))
    record["finished_at"] = now()
    store.save_run(record)
    return {k:v for k,v in record.items() if k != "snapshot"}


@app.post("/api/modules/{module_id}/revisions")
async def revise(module_id: str, req: RevisionRequest):
    all_modules = module_map()
    base = all_modules.get(f"{module_id}@{req.base_version}")
    if not base or req.module.id != module_id:
        problem("MODULE_BASE", "基准模块不存在或模块族 ID 不一致")
    latest = max(m.version for m in all_modules.values() if m.id == module_id)
    if req.base_version != latest or req.module.version != latest + 1:
        problem("REVISION_CONFLICT", f"请基于最新 v{latest} 派生 v{latest+1}", 409)
    check_module(req.module)
    from .registration import verify_references
    try:verify_references(req.module.model_dump(),store.definitions(),store.catalogs(),store.rules())
    except ValueError as exc:problem('DEFINITION_CONFLICT',str(exc))
    if req.module.transforms and len(req.anchors)<2:problem('TRANSFORM_EVIDENCE','显式变换至少提交两个验证样例')
    if req.module.implementation=='python_function' or req.module.transforms:
        if any(a.expected.keys()!=req.module.outputs.keys() for a in req.anchors):problem('ANCHOR_OUTPUT','函数适配和显式变换样例必须覆盖所有输出')
    for anchor in req.anchors:
        if not anchor.expected or not anchor.expected.keys() <= req.module.outputs.keys():
            problem("ANCHOR_OUTPUT", "锚点必须核验至少一个已声明输出")
        fill_inputs(anchor.inputs, req.module.inputs, "/anchors/inputs")
        for key, value in anchor.expected.items():
            validate_value(value, req.module.outputs[key], f"/anchors/expected/{key}")
    wf, inputs = demo()
    payload = {"workflow": wf.model_dump(), "inputs": inputs, "modules": [], "mode": "anchors", "module": req.module.model_dump(), "anchors": [a.model_dump() for a in req.anchors]}
    result = await asyncio.to_thread(worker, payload, 10)
    if result["status"] != "succeeded":
        return {"status": "rejected", "module": None, "regression": result}
    store.add_module(req.module,parent_version=req.base_version,summary=req.module.description)
    return {"status": "applied", "module": req.module.model_dump(), "regression": result}


@app.post("/api/optimize")
async def optimize(req: OptimizeRequest):
    if not 1 <= len(req.bounds) <= 8:
        problem("OPTIMIZATION_BOUNDS", "首版 DE 支持 1–8 个变量")
    for key, (lo, hi) in req.bounds.items():
        port = req.workflow.inputs.get(key)
        if port is None or port.type != "number" or not lo < hi:
            problem("OPTIMIZATION_BOUNDS", f"{key} 必须是工作流数字输入且下界小于上界")
    objective = req.workflow.output_ports.get(req.objective)
    if objective is None or objective.type != "number":
        problem("OPTIMIZATION_OBJECTIVE", "目标必须是声明的数字输出")
    opt = req.model_dump(include={"bounds", "objective", "direction", "seed", "maxiter", "popsize"})
    return await perform(req, opt)


@app.get("/api/harness/capabilities")
def harness():
    return {"protocol_version": "1.0", "connected": False, "description": "供 DeepSeek Harness 的工具适配器调用；核心运行无需 LLM", "tools": [
        {"name": "workflow_validate", "method": "POST", "path": "/api/validate", "schema": WorkflowRequest.model_json_schema()},
        {"name": "workflow_execute", "method": "POST", "path": "/api/runs", "schema": RunRequest.model_json_schema()},
        {"name": "module_revise", "method": "POST", "path": "/api/modules/{module_id}/revisions", "schema": RevisionRequest.model_json_schema()},
        {"name": "workflow_optimize", "method": "POST", "path": "/api/optimize", "schema": OptimizeRequest.model_json_schema()}]}


DIST = ROOT / "frontend" / "dist"
from .registration import install
install(app,lambda:store,module_map,worker,problem)
from .changes import install as install_changes
install_changes(app,lambda:store,module_map,worker,problem)
from .catalog import install as install_catalog
install_catalog(app,lambda:store,module_map,problem)
from .automation import install as install_automation
install_automation(app,lambda:store,module_map,worker,problem)
from .scenarios import install as install_scenarios
install_scenarios(app,module_map)
from .assembly import install as install_assembly
install_assembly(app,lambda:store,module_map,worker,problem)
from .workspace import install as install_workspace
install_workspace(app,lambda:store,problem)
from .assembly_form import install as install_assembly_form
install_assembly_form(app,module_map)
from .input_profiles import install as install_input_profiles
install_input_profiles(app,lambda:store,module_map,problem)
from .designs import install as install_designs
install_designs(app,lambda:store,module_map,worker,problem)
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(DIST / "index.html")
