"""Isolated process for each evaluation or optimization (bounded by parent)."""
import json
import math
import sys
from .engine import WorkflowError, execute, fill_inputs, check_domains, shape_dimensions, check_module
from .models import Module, Workflow


def main(payload):
    if payload.get('mode')=='assembly_reference':
        from .assembly_reference import reference
        try:return {'status':'succeeded','outputs':reference(payload['inputs']),'trace':[]}
        except Exception as exc:return {'status':'failed','outputs':{},'trace':[], 'diagnostics':[{'code':'REFERENCE_UNAVAILABLE','message':str(exc),'path':'/inputs','severity':'error'}]}
    workflow = Workflow.model_validate(payload["workflow"])
    modules = {f"{m['id']}@{m['version']}": Module.model_validate(m) for m in payload["modules"]}
    trace = []
    try:
        if payload.get("mode") == "anchors":
            module = Module.model_validate(payload["module"])
            check_module(module)
            from .expressions import execute_module
            records = []
            for index, anchor in enumerate(payload["anchors"]):
                inputs = fill_inputs(anchor["inputs"], module.inputs, f"/anchors/{index}/inputs")
                check_domains(inputs,module.domains,"/anchors/inputs")
                actual = fill_inputs(execute_module(module, inputs), module.outputs, "/module/outputs")
                shape_dimensions(actual,module.outputs,"/anchors/outputs",shape_dimensions(inputs,module.inputs,"/anchors/inputs"))
                def compare(a, b):
                    if isinstance(b, dict):
                        return isinstance(a, dict) and all(k in a and compare(a[k], v) for k,v in b.items())
                    if type(b) in (int,float):
                        return type(a) in (int,float) and math.isclose(a, b, rel_tol=anchor["tolerance"], abs_tol=anchor["tolerance"])
                    if isinstance(b,list):
                        return isinstance(a,list) and len(a)==len(b) and all(compare(x,y) for x,y in zip(a,b))
                    return a == b
                passed = compare(actual, anchor["expected"])
                records.append({"index": index, "passed": passed, "actual": actual, "expected": anchor["expected"]})
            return {"status": "succeeded" if all(r["passed"] for r in records) else "failed", "anchors": records, "trace": [], "outputs": {}}
        if payload.get("optimization"):
            from scipy.optimize import differential_evolution
            opt = payload["optimization"]
            names = list(opt["bounds"])
            history = []
            def objective(vector):
                values = {**payload["inputs"], **dict(zip(names, [float(v) for v in vector]))}
                result = execute(workflow, modules, values)
                score = result[opt["objective"]]
                history.append({"evaluation": len(history)+1, "parameters": {n: values[n] for n in names}, "objective": score})
                return score if opt["direction"] == "min" else -score
            result = differential_evolution(objective, list(opt["bounds"].values()), seed=opt["seed"], maxiter=opt["maxiter"], popsize=opt["popsize"], polish=False, workers=1)
            final_inputs = {**payload["inputs"], **dict(zip(names, [float(v) for v in result.x]))}
            outputs = execute(workflow, modules, final_inputs, trace)
            return {"status": "succeeded", "outputs": outputs, "trace": trace, "optimization": {"algorithm": "DE", "converged": bool(result.success), "message": str(result.message), "evaluations": result.nfev, "best_parameters": {k: final_inputs[k] for k in names}, "history": history}}
        outputs = execute(workflow, modules, payload["inputs"], trace)
        return {"status": "succeeded", "outputs": outputs, "trace": trace}
    except Exception as exc:
        diagnostic = exc.diagnostic.model_dump() if isinstance(exc, WorkflowError) else {"code": "COMPUTATION_ERROR", "message": str(exc), "path": "/", "severity": "error"}
        return {"status": "failed", "outputs": {}, "trace": trace, "diagnostics": [diagnostic]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    result = main(json.load(sys.stdin))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
