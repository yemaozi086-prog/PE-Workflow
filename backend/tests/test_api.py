import copy
import pytest
from fastapi.testclient import TestClient
from pe_workflow import api
from pe_workflow.storage import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = Store(tmp_path / "test.sqlite")
    for module in api.builtins():
        store.seed_module(module)
    monkeypatch.setattr(api, "store", store)
    with TestClient(api.app) as client:
        yield client


def example(client):
    data = client.get("/api/examples/thermal").json()
    return {k:v for k,v in data.items() if k != "revision"}


def test_run_and_frozen_replay(client):
    data = example(client)
    response = client.post("/api/runs", json=data)
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "succeeded"
    assert run["outputs"]["mean_loss"] == pytest.approx(107.16)
    snapshot = client.get("/api/runs/" + run["id"]).json()
    assert snapshot["snapshot"]["modules"]
    replay = client.post(f"/api/runs/{run['id']}/replay").json()
    assert replay["status"] == "succeeded"
    assert replay["snapshot_hash"] == run["snapshot_hash"]
    assert replay["outputs"] == run["outputs"]


def test_invalid_graph_never_runs(client):
    data = example(client)
    data["workflow"]["nodes"][0]["inputs"].pop("items")
    report = client.post("/api/validate", json=data).json()
    assert not report["valid"]
    response = client.post("/api/runs", json=data)
    assert response.status_code == 422
    assert response.json()["diagnostics"][0]["node_id"] == "sweep"
    assert client.get("/api/runs").json() == []


def test_schema_errors_have_json_paths(client):
    data = example(client)
    data["workflow"]["schema_version"] = "99"
    response = client.post("/api/validate", json=data)
    assert response.status_code == 422
    assert response.json()["diagnostics"][0]["path"] == "/workflow/schema_version"


def test_saving_draft_revision_conflict_and_history(client):
    data = example(client)
    assert client.post("/api/workflows", json={**data,"expected_revision":0}).json()["revision"] == 1
    assert client.post("/api/workflows", json={**data,"expected_revision":0}).status_code == 409
    data["inputs"]["fs"] = 90
    assert client.post("/api/workflows", json={**data,"expected_revision":1}).json()["revision"] == 2
    key = data["workflow"]["id"]
    assert client.get(f"/api/workflows/{key}?revision=1").json()["inputs"]["fs"] == 100
    assert len(client.get(f"/api/workflows/{key}/history").json()) == 2


def test_formula_revision_regression_reject_then_apply(client):
    base = next(m for m in client.get("/api/modules").json() if m["id"] == "switch_loss" and m["version"] == 2)
    candidate = copy.deepcopy(base)
    candidate["version"] = 3
    candidate["expressions"]["p_total"] += " + 1"
    request = {"base_version":2, "module":candidate, "anchors":[{"inputs":{"i_rms":8,"fs":100000,"vce":1.7,"e_sw":0.001,"Tj":25},"expected":{"p_cond":6.8,"p_sw":100,"p_total":106.8}}]}
    response = client.post("/api/modules/switch_loss/revisions", json=request).json()
    assert response["status"] == "rejected"
    assert not any(m["version"] == 3 and m["id"] == "switch_loss" for m in client.get("/api/modules").json())
    candidate["expressions"]["p_total"] = base["expressions"]["p_total"].replace("0.0018", "0.0020")
    candidate["expressions"]["p_cond"] = base["expressions"]["p_cond"].replace("0.0018", "0.0020")
    response = client.post("/api/modules/switch_loss/revisions", json=request).json()
    assert response["status"] == "applied"
    assert response["regression"]["anchors"][0]["passed"]


def test_runtime_failure_does_not_masquerade_as_success(client):
    data = example(client)
    data["inputs"]["mode"] = "absent"
    run = client.post("/api/runs", json=data).json()
    assert run["status"] == "failed"
    assert run["outputs"] == {}
    assert run["diagnostics"][0]["code"] == "BRANCH_NOT_FOUND"
    assert any(t["status"] == "failed" for t in run["trace"])


def test_de_has_reproducible_seed_and_effective_input(client):
    data = example(client)
    request = {**data, "bounds":{"fs":[80,120]},"objective":"mean_loss","direction":"min","seed":42,"maxiter":1,"popsize":5}
    first = client.post("/api/optimize",json=request).json()
    second = client.post("/api/optimize",json=request).json()
    assert first["status"] == "succeeded", first
    assert first["optimization"]["best_parameters"] == second["optimization"]["best_parameters"]
    assert first["outputs"]["mean_loss"] < 107.16
    assert first["snapshot_hash"] == second["snapshot_hash"]


def test_interface_and_schema_available_without_harness(client):
    assert client.get("/api/schema").status_code == 200
    capabilities = client.get("/api/harness/capabilities").json()
    assert not capabilities["connected"]
    assert len(capabilities["tools"]) == 4


def test_validate_returns_canonical_document_for_editor(client):
    data = example(client)
    data["workflow"].pop("layout")
    response = client.post("/api/validate", json=data).json()
    assert response["valid"]
    assert response["workflow"]["layout"] == {}
