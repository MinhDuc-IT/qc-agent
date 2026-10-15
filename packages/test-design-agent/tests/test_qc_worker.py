import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from tda.infrastructure.qc_worker import create_app
from tda.pipeline import approve, qc_plan
from test_workflow import ScriptedAgent, run_workflow, service


def payload(version, url):
    return {"schema_version": "1.0", "task": {
        "schema_version": "1.1", "task_id": "task-1", "run_id": "qc-run-1", "capability": "baseline.http",
        "objective": "Check baseline", "parameters": {"baseline_version": version},
        "target": {"type": "http_service", "project_id": "backend", "ref": url}},
        "project": {"id": "backend", "language": "javascript", "root": "."},
        "source": {"type": "local_path", "ref": "ignored-untrusted-checkout"}}


def test_worker_protocol_normalizes_pass_and_defect(tmp_path, service):
    result, store = run_workflow(tmp_path, ScriptedAgent(kinds=["functional"]), service[0])
    baseline = Path(result["baseline"])
    approve(baseline, "reviewer", store.root / "execution_report.json")
    app = create_app(baseline.parent, "service-token")
    headers = {"Authorization": "Bearer service-token"}
    with TestClient(app) as client:
        submitted = client.post("/v1/tasks", json=payload(baseline.name, service[0]), headers=headers)
        assert submitted.status_code == 200
        state = client.get("/v1/tasks/" + submitted.json()["run_id"], headers=headers).json()
        assert state["status"] == "completed"
        assert state["result"]["verdict"] == "pass"
        qc_root = Path(__file__).resolve().parents[3]
        spec = importlib.util.spec_from_file_location("worker_qc_models", qc_root / "app/domain/models.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        worker_result = module.WorkerResult.model_validate(state["result"])
        assert worker_result.task_id == "task-1"
        module.ExecutionPlan.model_validate(qc_plan(baseline, "backend", "run", external=True))
        service[1].broken = True
        submitted = client.post("/v1/tasks", json=payload(baseline.name, service[0]), headers=headers)
        state = client.get("/v1/tasks/" + submitted.json()["run_id"], headers=headers).json()
        assert state["result"]["verdict"] == "fail"
        assert state["result"]["findings"][0]["id"] == "baseline-case-functional"
        module.WorkerResult.model_validate(state["result"])


def test_worker_rejects_missing_auth_draft_untrusted_host_and_tampering(tmp_path, service):
    result, store = run_workflow(tmp_path, ScriptedAgent(kinds=["functional"]), service[0])
    baseline = Path(result["baseline"])
    with TestClient(create_app(baseline.parent, "service-token")) as client:
        request = payload(baseline.name, service[0])
        assert client.post("/v1/tasks", json=request).status_code == 401
        headers = {"Authorization": "Bearer service-token"}
        assert client.post("/v1/tasks", json=request, headers=headers).status_code == 409
        approve(baseline, "reviewer", store.root / "execution_report.json")
        request["task"]["target"]["ref"] = "https://untrusted.example"
        assert client.post("/v1/tasks", json=request, headers=headers).status_code == 422
        request["task"]["target"]["ref"] = service[0]
        (baseline / "cases/cases.json").write_text("[]")
        assert client.post("/v1/tasks", json=request, headers=headers).status_code == 409


def test_worker_requires_explicit_configuration():
    with pytest.raises(ValueError, match="required"):
        create_app(baseline_root="", token="")
