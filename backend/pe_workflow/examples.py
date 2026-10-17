"""Small, explicit demonstration fixtures; coefficients are assumptions."""
from .models import Module, Port, Workflow


def number(quantity="", unit="", **kw):
    return Port(quantity=quantity, unit=unit, **kw)


def input_ref(port, field=None):
    return {"source": "input", "port": port, **({"field": field} if field else {})}


def node_ref(node, port, field=None):
    return {"source": "node", "node": node, "port": port, **({"field": field} if field else {})}


def builtins():
    inputs = {"i_rms": number("current", "A"), "fs": number("frequency", "Hz"),
              "vce": number("voltage", "V"), "e_sw": number("energy", "J")}
    fields = {"p_cond": number("power", "W", characteristic="loss_conduction"),
              "p_sw": number("power", "W", characteristic="loss_switching"),
              "p_total": number("power", "W", characteristic="loss_total")}
    # Uses the simplified formula present in the old demo, not the detailed
    # eight-device DBSRC model; no claim of physical equivalence to that model.
    common = dict(id="switch_loss", name="开关损耗 · 简化演示", outputs=fields,
                  provenance="迁移旧 demo 的简化公式；导通系数 0.5 为演示假设，非已核验器件模型")
    base = Module(**common, version=1, inputs=inputs, expressions={
        "p_cond": "vce * i_rms * 0.5", "p_sw": "e_sw * fs", "p_total": "vce * i_rms * 0.5 + e_sw * fs"})
    corrected = Module(**common, version=2, inputs={**inputs, "Tj": number("temperature", "degC")}, expressions={
        "p_cond": "(vce + 0.0018 * (Tj - 25)) * i_rms * 0.5", "p_sw": "e_sw * fs",
        "p_total": "(vce + 0.0018 * (Tj - 25)) * i_rms * 0.5 + e_sw * fs"},
        description="演示假设：导通压降每升高 1 ℃ 增加 1.8 mV；25 ℃ 退化为 v1")
    pack = Module(id="loss_pack", version=1, name="损耗字典打包", inputs=fields,
                  outputs={"loss": Port(type="flat_dict", fields=fields)},
                  expressions={"loss": "{'p_cond': p_cond, 'p_sw': p_sw, 'p_total': p_total}"}, provenance="平台确定性打包模块")
    return [base, corrected, pack]


def demo():
    modules = builtins()
    scalar_inputs = {**modules[0].inputs, "Tj": number("temperature", "degC")}
    def case(name, version):
        args = {k: input_ref(k) for k in modules[version - 1].inputs}
        return Workflow(id=name, name=name, inputs=scalar_inputs,
                        nodes=[{"id": "loss", "kind": "compute", "module_id": "switch_loss", "module_version": version, "inputs": args}],
                        outputs={k: node_ref("loss", k) for k in modules[0].outputs}, output_ports=modules[0].outputs)
    workpoint = Port(type="flat_dict", fields={"i_rms": number("current", "A"), "Tj": number("temperature", "degC")})
    shared = {"fs": number("frequency", "kHz"), "vce": number("voltage", "V"), "e_sw": number("energy", "mJ"), "mode": Port(type="string")}
    body = Workflow(id="workpoint", name="单工况损耗", inputs={"item": workpoint, **shared}, nodes=[
        {"id": "model", "label": "选择损耗模型", "kind": "switch", "inputs": {
            "selector": input_ref("mode"), "i_rms": input_ref("item", "i_rms"), "Tj": input_ref("item", "Tj"),
            **{k: input_ref(k) for k in ("fs", "vce", "e_sw")}},
         "cases": {"baseline": case("baseline", 1), "temperature": case("temperature", 2)}},
        {"id": "pack", "label": "声明式字典输出", "kind": "compute", "module_id": "loss_pack", "module_version": 1,
         "inputs": {k: node_ref("model", k) for k in modules[0].outputs}}
    ], outputs={k: node_ref("pack", "loss", k) for k in modules[0].outputs}, output_ports=modules[0].outputs,
    layout={"model": {"x": 330, "y": 100}, "pack": {"x": 660, "y": 100}})
    wf = Workflow(id="thermal_demo", name="结温与开关损耗", description="多工况遍历 → 条件分支 → 裸参数/扁平字典 → 汇总。系数为演示假设。", inputs={
        "workpoints": Port(type="collection", item=workpoint), **shared}, nodes=[
        {"id": "sweep", "label": "遍历工况", "kind": "foreach", "body": body,
         "inputs": {"items": input_ref("workpoints"), **{k: input_ref(k) for k in shared}}},
        {"id": "mean_loss", "label": "平均总损耗", "kind": "aggregate", "operation": "mean", "field": "p_total", "inputs": {"items": node_ref("sweep", "results")}},
        {"id": "worst_loss", "label": "最大总损耗", "kind": "aggregate", "operation": "max", "field": "p_total", "inputs": {"items": node_ref("sweep", "results")}}
    ], outputs={"mean_loss": node_ref("mean_loss", "value"), "worst_loss": node_ref("worst_loss", "value"), "workpoint_results": node_ref("sweep", "results")},
    output_ports={"mean_loss": number("power", "W"), "worst_loss": number("power", "W"), "workpoint_results": Port(type="collection", item=Port(type="flat_dict", fields=modules[0].outputs))},
    layout={"sweep": {"x": 330, "y": 130}, "mean_loss": {"x": 710, "y": 65}, "worst_loss": {"x": 710, "y": 310}})
    return wf, {"workpoints": [{"i_rms": 8.0, "Tj": t} for t in (25.0, 75.0, 125.0)], "fs": 100.0, "vce": 1.7, "e_sw": 1.0, "mode": "temperature"}
