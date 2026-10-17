# -*- coding: utf-8 -*-
"""Flux (PE Workflow) DSH 桥接客户端：只允许调用本机平台 8020 的显式白名单接口。

用法：
    python flux_client.py <endpoint> <base64(json_payload)>   # 正常工具调用
    python flux_client.py --list                              # 打印白名单
    python flux_client.py --selfcheck                         # 只读自检（不注册/不替换模块）

安全边界：
    - SERVICE_BASE 固定为 http://127.0.0.1:8020，不接受任意 URL；
    - 只允许 ALLOWED 中列出的接口（按方法精确匹配），其余一律拒绝；
    - 不执行任何提交的源码 —— 源码只作为 HTTP 载荷转发给平台，由平台受控解释器校验/执行；
    - HTTP 错误与平台 diagnostics 原样返回给模型（附加 http_status）。
"""
import base64
import io
import json
from pathlib import Path
import tomllib
_config_root=Path(__file__).resolve().parents[1]
_config_path=_config_root/('config.local.toml' if (_config_root/'config.local.toml').exists() else 'config.example.toml')
_config=tomllib.loads(_config_path.read_text(encoding='utf-8')) if _config_path.exists() else {}
_service_port=_config.get('server',{}).get('port',8020)
if type(_service_port)!=int or not 1<=_service_port<=65535:raise ValueError('Invalid configured service port')
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVICE_BASE = "http://127.0.0.1:"+str(_service_port)
TIMEOUT = 180

# 路径参数模式（与 backend/pe_workflow/models.py 的字段约束一致）
_MOD_ID = r"[A-Za-z][A-Za-z0-9_-]{0,79}"
_CAT_ID = r"[A-Za-z][A-Za-z0-9_.-]{0,99}"
_HEX_ID = r"[0-9a-fA-F]{16,64}"  # run / change-task / artifact(context) id

# method -> 精确匹配路径的正则列表（每列一个端点只对应一种方法）
ALLOWED = {
    "GET": [
        r"^/api/health$",
        r"^/api/schema$",
        r"^/api/modules$",
        r"^/api/modules/search$",
        rf"^/api/modules/{_MOD_ID}/versions/\d+$",
        r"^/api/parameter-catalogs$",
        rf"^/api/parameter-catalogs/{_CAT_ID}/versions/\d+$",
        r"^/api/parameter-definitions$",
        r"^/api/naming-rules$",
        r"^/api/numeric/capabilities$",
        r"^/api/workflows$",
        rf"^/api/workflows/{_MOD_ID}$",
        rf"^/api/workflows/{_MOD_ID}/history$",
        r"^/api/runs$",
        rf"^/api/runs/{_HEX_ID}$",
        r"^/api/examples/thermal$",
        r"^/api/examples/waveform$",
        r"^/api/examples/dictionaries$",
        r"^/api/change-tasks$",
        rf"^/api/change-tasks/{_HEX_ID}$",
        r"^/api/harness/capabilities$",
        r"^/api/harness/dictionary-contract$",
        r"^/api/harness/registration-contract$",
        r"^/api/harness/change-task-contract$",
        rf"^/api/harness/artifacts/{_HEX_ID}$",
    ],
    "POST": [
        r"^/api/validate$",
        r"^/api/runs$",
        rf"^/api/runs/{_HEX_ID}/replay$",
        r"^/api/modules/analyze-source$",
        r"^/api/modules/replacement-plan$",
        r"^/api/harness/prepare-registration$",
        r"^/api/harness/prepare-change$",
        r"^/api/harness/submit-candidate$",
        r"^/api/change-tasks$",
        rf"^/api/change-tasks/{_HEX_ID}/clarify$",
        rf"^/api/change-tasks/{_HEX_ID}/answer$",
        rf"^/api/change-tasks/{_HEX_ID}/apply$",
    ],
}


def _reconfigure_stdout():
    # Windows 下 stdout 默认 GBK；强制 UTF-8 以便 DSH subprocess 按 UTF-8 读取。
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, TypeError, io.UnsupportedOperation):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except (AttributeError, TypeError, io.UnsupportedOperation):
                pass


def match_endpoint(endpoint: str):
    """返回该 endpoint 被允许的 HTTP 方法；不允许则返回 None。"""
    for method, patterns in ALLOWED.items():
        for pattern in patterns:
            if re.match(pattern, endpoint):
                return method
    return None


def _clean(payload):
    """去掉顶层 None，避免把可选参数以 null 形式发给 Pydantic 严格模型。"""
    if isinstance(payload, dict):
        return {k: v for k, v in payload.items() if v is not None}
    return payload


def request(endpoint: str, payload=None):
    """按白名单调用平台接口，返回 dict；HTTP 错误与 diagnostics 完整保留。"""
    method = match_endpoint(endpoint)
    if method is None:
        return {"status": "error", "error": f"接口不在白名单内：{endpoint}", "endpoint": endpoint}
    payload = _clean(payload or {})
    url = SERVICE_BASE + endpoint
    if method == "GET":
        qs = urllib.parse.urlencode({k: v for k, v in payload.items() if v is not None and v != ""})
        if qs:
            url = url + "?" + qs
        req = urllib.request.Request(url, method="GET")
    else:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    status = None
    raw = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            raw = ""
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc), "endpoint": endpoint}

    if raw is None:
        return {"status": "error", "error": "empty response", "endpoint": endpoint, "http_status": status}
    try:
        result = json.loads(raw)
    except ValueError:
        return {"status": "error", "error": "平台返回非 JSON", "raw": raw[:400], "endpoint": endpoint, "http_status": status}
    if isinstance(result, dict) and status is not None and status >= 400:
        # 完整保留平台 diagnostics，并标注 HTTP 状态码。
        result["http_status"] = status
    return result


def _b64(payload):
    return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")


def selfcheck():
    """只读自检：读取模块/词典/示例，试读（validate）与查看运行结果；不注册、不替换任何模块。"""
    checks = []
    def record(name, endpoint, payload=None):
        try:
            res = request(endpoint, payload)
            checks.append({"name": name, "endpoint": endpoint, "method": match_endpoint(endpoint), "result": res})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": name, "endpoint": endpoint, "error": str(exc)})

    record("health", "/api/health")
    record("dictionary_contract", "/api/harness/dictionary-contract")
    record("capabilities", "/api/harness/capabilities")
    record("registration_contract", "/api/harness/registration-contract")
    record("change_task_contract", "/api/harness/change-task-contract")
    record("modules", "/api/modules")
    record("module_search", "/api/modules/search", {"q": "loss"})
    record("catalogs", "/api/parameter-catalogs")
    record("catalog_rows", "/api/parameter-catalogs/team_parameters/versions/1", {"q": "i_rms"})
    record("definitions", "/api/parameter-definitions")
    record("naming_rules", "/api/naming-rules")
    record("numeric", "/api/numeric/capabilities")
    record("workflows", "/api/workflows")
    record("runs", "/api/runs")
    record("example_dictionaries", "/api/examples/dictionaries")

    # 试读（纯校验，无副作用）：用平台自带的 v3 双字典示例做一次 validate
    example = next((c["result"] for c in checks if c["name"] == "example_dictionaries" and "workflow" in c.get("result", {})), None)
    if example:
        record("validate_v3_example", "/api/validate", {"workflow": example["workflow"], "inputs": example["inputs"]})

    # 查看最近一次运行结果（若存在）
    runs = next((c["result"] for c in checks if c["name"] == "runs" and isinstance(c.get("result"), list)), [])
    if runs:
        record("read_run", "/api/runs/" + runs[0]["id"])

    summary = {
        "service": SERVICE_BASE,
        "read_only": True,
        "note": "只读自检：未调用 prepare-registration / prepare-change / submit-candidate / change-tasks 等写入接口",
        "check_count": len(checks),
        "checks": checks,
    }
    return summary


def tool_output(value):
    """Stable object envelope at the subprocess boundary, including cached plugins."""
    if isinstance(value, list):
        return {"items": value, "count": len(value)}
    return value if isinstance(value, dict) else {"value": value}


def main(argv):
    _reconfigure_stdout()
    if len(argv) >= 1 and argv[0] == "--list":
        print(json.dumps({"service": SERVICE_BASE, "allowed": {m: sorted(p for p in ALLOWED[m]) for m in ALLOWED}}, ensure_ascii=False, indent=2))
        return
    if len(argv) >= 1 and argv[0] == "--selfcheck":
        print(json.dumps(selfcheck(), ensure_ascii=False))
        return
    if len(argv) < 2:
        print(json.dumps({"status": "error", "error": "用法: flux_client.py <endpoint> <base64(payload)> | --list | --selfcheck"}, ensure_ascii=False))
        return
    endpoint, payload_b64 = argv[0], argv[1]
    try:
        payload = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "error": f"bad payload: {exc}"}, ensure_ascii=False))
        return
    print(json.dumps(tool_output(request(endpoint, payload)), ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
